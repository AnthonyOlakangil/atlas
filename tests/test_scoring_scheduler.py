"""Phase 9 gate: +1/+5/+20d scoring, 30-day simulation, track record renders."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

import atlas.scheduler as scheduler_mod
from atlas.core.calendar import add_trading_days, is_trading_day, next_business_day
from atlas.core.models import Mode, Snapshot, TicketStatus
from atlas.execution.approval import _check_live_gate
from atlas.portfolio.scoring import (
    render_track_record,
    score_due_tickets,
    track_record_stats,
)
from atlas.scheduler import _guarded, build_scheduler
from tests.test_execution import make_ticket

D = Decimal
START = date(2026, 6, 1)  # a Monday


def trading_days(start: date, n: int) -> list[date]:
    days = []
    d = start if is_trading_day(start) else next_business_day(start)
    while len(days) < n:
        days.append(d)
        d = next_business_day(d)
    return days


DAYS = trading_days(START, 45)
# deterministic walk: QQQM outruns SPY → positive alpha
PRICE_WALK: dict[tuple[str, str], Decimal] = {}
for i, day in enumerate(DAYS):
    PRICE_WALK[("QQQM", day.isoformat())] = D("230") + D(i)
    PRICE_WALK[("GLDM", day.isoformat())] = D("80") - D("0.1") * D(i)  # a loser, scored honestly
    PRICE_WALK[("SPY", day.isoformat())] = D("748") + D("0.5") * D(i)


def lookup(ticker: str, on: date) -> Decimal | None:
    return PRICE_WALK.get((ticker, on.isoformat()))


def executed_ticket(session: Session, ticker: str, day: date, fill: Decimal, **kw: object):
    t = make_ticket(session, ticker=ticker, **kw)
    t.status = TicketStatus.EXECUTED
    t.executed_at = datetime.combine(day, time(17, 0), tzinfo=UTC)
    t.fill_price = fill
    session.flush()
    return t


def test_scoring_windows_mature_incrementally(db_session: Session) -> None:
    t = executed_ticket(db_session, "QQQM", DAYS[0], PRICE_WALK[("QQQM", DAYS[0].isoformat())])

    assert score_due_tickets(db_session, DAYS[0], lookup) == 0  # nothing matured yet
    assert score_due_tickets(db_session, DAYS[1], lookup) == 1  # +1d
    assert score_due_tickets(db_session, DAYS[1], lookup) == 0  # idempotent
    assert score_due_tickets(db_session, DAYS[25], lookup) == 2  # +5d and +20d

    windows = {s.window: s for s in t.scores}
    assert set(windows) == {"+1d", "+5d", "+20d"}
    one = windows["+1d"]
    # QQQM +1/230 = .4348%, SPY +0.5/748 = .0668% → alpha ≈ .368%
    assert one.ticket_return_pct == D("0.4348")
    assert one.spy_return_pct == D("0.0668")
    assert one.alpha_pct == D("0.3679")  # quantized from the exact difference


def test_sell_scoring_inverts_return(db_session: Session) -> None:
    from atlas.core.models import TradeAction

    t = executed_ticket(
        db_session, "GLDM", DAYS[0], PRICE_WALK[("GLDM", DAYS[0].isoformat())],
        action=TradeAction.SELL, quantity=D("1"), is_fractional=False,
    )
    score_due_tickets(db_session, DAYS[2], lookup)
    s = t.scores[0]
    assert s.ticket_return_pct > 0  # GLDM fell after the sell → the sell was right


def test_missing_price_data_skips_without_crashing(db_session: Session) -> None:
    executed_ticket(db_session, "ZZZZ", DAYS[0], D("10"))
    assert score_due_tickets(db_session, DAYS[5], lookup) == 0  # no data → no fake scores


# --- the 30-day simulation gate ----------------------------------------------


def test_thirty_day_simulation_track_record_renders(db_session: Session) -> None:
    executed_ticket(db_session, "QQQM", DAYS[0], PRICE_WALK[("QQQM", DAYS[0].isoformat())])
    executed_ticket(
        db_session, "GLDM", DAYS[1], PRICE_WALK[("GLDM", DAYS[1].isoformat())],
        quantity=D("1"), is_fractional=False,
    )

    for i, day in enumerate(DAYS[:30]):
        spy = PRICE_WALK[("SPY", day.isoformat())]
        value = D("250") + D(i)  # a slowly growing paper book
        db_session.add(
            Snapshot(
                taken_at=datetime.combine(day, time(20, 0), tzinfo=UTC),
                total_value=value, settled_cash=D("10"), unsettled_cash=D("0"), spy_close=spy,
            )
        )
        score_due_tickets(db_session, day, lookup)
    db_session.flush()

    stats = track_record_stats(db_session)
    assert stats["paper_days_logged"] == 30
    windows = stats["windows"]
    assert isinstance(windows, dict) and set(windows) == {"+1d", "+5d", "+20d"}
    assert windows["+1d"]["n"] == 2  # both tickets scored
    # the losing GLDM buy drags the average — shown, not hidden
    assert windows["+20d"]["avg_loss_alpha"] is not None

    report = render_track_record(db_session)
    assert "hit rate" in report and "30 / 30" in report
    assert "Max drawdown" in report

    # 30 logged paper days + rendering report = the LIVE gate opens (§1.3)
    _check_live_gate(db_session, Mode.LIVE)  # must not raise


def test_live_gate_still_locked_at_29_days(db_session: Session) -> None:
    from atlas.execution.approval import LiveModeLocked

    for day in DAYS[:29]:
        db_session.add(
            Snapshot(taken_at=datetime.combine(day, time(20, 0), tzinfo=UTC),
                     total_value=D("250"), settled_cash=D("0"), unsettled_cash=D("0"))
        )
    db_session.flush()
    with pytest.raises(LiveModeLocked, match="29/30"):
        _check_live_gate(db_session, Mode.LIVE)


# --- scheduler wiring ---------------------------------------------------------


def test_scheduler_jobs_registered_on_pt_cron() -> None:
    sched = build_scheduler()
    jobs = {j.id: j for j in sched.get_jobs()}
    assert set(jobs) == {"preopen_cycle", "postclose_update", "stop_monitor", "scoring", "expire_tickets"}
    pre = str(jobs["preopen_cycle"].trigger)
    assert "hour='6'" in pre and "minute='30'" in pre and "day_of_week='mon-fri'" in pre
    post = str(jobs["postclose_update"].trigger)
    assert "hour='13'" in post and "minute='15'" in post
    assert "minute='*/10'" in str(jobs["stop_monitor"].trigger)


def test_guard_skips_non_trading_days_and_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    @_guarded("unit")
    def boom() -> None:
        calls.append(1)
        raise RuntimeError("job blew up")

    monkeypatch.setattr(scheduler_mod, "is_trading_day", lambda d: False)
    boom()
    assert calls == []  # skipped off-market

    monkeypatch.setattr(scheduler_mod, "is_trading_day", lambda d: True)
    boom()  # raises inside, but the scheduler loop must survive
    assert calls == [1]


def test_add_trading_days_used_by_windows() -> None:
    assert add_trading_days(date(2026, 7, 2), 1) == date(2026, 7, 6)  # holiday + weekend hop
