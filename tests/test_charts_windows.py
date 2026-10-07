"""Task 1b [high]: dashboard windows are chained per-position returns, not total diffs.

Same root cause family as the day-change bug. `performance_series` based every
window on `rows[0].total_value` — the first snapshot in the slice — so a deposit
or a composition change read as performance, and each window rebased to a
different poisoned base.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from atlas.core.models import Snapshot
from atlas.viz.charts import drawdown_series, performance_series


def _snap(session: Session, day: int, cash: str, **holdings: tuple[str, str]) -> None:
    positions = {
        t: {"shares": sh, "price": px, "value": str(Decimal(sh) * Decimal(px))}
        for t, (sh, px) in holdings.items()
    }
    total = sum(Decimal(p["value"]) for p in positions.values()) + Decimal(cash)
    session.add(
        Snapshot(
            taken_at=datetime(2026, 8, day, 20, 0, tzinfo=UTC),
            total_value=total,
            settled_cash=Decimal(cash),
            unsettled_cash=Decimal(0),
            spy_close=Decimal("700.00"),
            positions=positions,
        )
    )
    session.flush()


def test_a_deposit_is_not_performance(db_session: Session) -> None:
    """Prices flat, $2000 deposited. Total value doubles; return is 0%."""
    _snap(db_session, 10, "100", VOO=("1.0", "700.00"))
    _snap(db_session, 11, "2100", VOO=("1.0", "700.00"))
    series = performance_series(db_session)
    assert series[-1]["atlas"] == 0.0


def test_price_move_is_captured(db_session: Session) -> None:
    _snap(db_session, 10, "100", VOO=("1.0", "700.00"))
    _snap(db_session, 11, "100", VOO=("1.0", "770.00"))  # +10%
    assert performance_series(db_session)[-1]["atlas"] == 10.0


def test_returns_chain_across_periods(db_session: Session) -> None:
    _snap(db_session, 10, "0", VOO=("1.0", "100.00"))
    _snap(db_session, 11, "0", VOO=("1.0", "110.00"))  # +10%
    _snap(db_session, 12, "0", VOO=("1.0", "121.00"))  # +10% again -> +21% cumulative
    assert performance_series(db_session)[-1]["atlas"] == 21.0


def test_composition_change_emits_a_gap_not_a_number(db_session: Session) -> None:
    _snap(db_session, 10, "0", VOO=("1.0", "100.00"))
    _snap(db_session, 11, "0", VOO=("1.0", "100.00"), ANET=("1.0", "200.00"))
    series = performance_series(db_session)
    gap = series[-1]
    assert gap["atlas"] is None
    assert "ANET" in (gap.get("note") or "")


def test_chain_resumes_after_a_composition_gap(db_session: Session) -> None:
    _snap(db_session, 10, "0", VOO=("1.0", "100.00"))
    _snap(db_session, 11, "0", VOO=("1.0", "100.00"), ANET=("1.0", "100.00"))  # gap
    _snap(db_session, 12, "0", VOO=("1.0", "110.00"), ANET=("1.0", "110.00"))  # +10%
    series = performance_series(db_session)
    assert series[-2]["atlas"] is None
    assert series[-1]["atlas"] == 10.0, "rebased after the gap, not carried through it"


def test_legacy_value_only_snapshot_yields_a_gap(db_session: Session) -> None:
    session = db_session
    session.add(
        Snapshot(
            taken_at=datetime(2026, 8, 10, 20, 0, tzinfo=UTC),
            total_value=Decimal("800"), settled_cash=Decimal("100"),
            unsettled_cash=Decimal(0), positions={"VOO": "700.00"},
        )
    )
    session.flush()
    _snap(session, 11, "100", VOO=("1.0", "700.00"))
    assert performance_series(session)[-1]["atlas"] is None


def test_implausible_period_breaks_the_chain_rather_than_emitting_it(
    db_session: Session,
) -> None:
    """The +/-25% tripwire applies to every window, not just day change."""
    _snap(db_session, 10, "0", VOO=("1.0", "100.00"))
    _snap(db_session, 11, "0", VOO=("1.0", "200.00"))  # +100%
    series = performance_series(db_session)
    assert series[-1]["atlas"] is None
    assert "tripwire" in (series[-1].get("note") or "").lower()


def test_drawdown_ignores_cash_flows(db_session: Session) -> None:
    """A withdrawal must not register as drawdown."""
    _snap(db_session, 10, "1000", VOO=("1.0", "100.00"))
    _snap(db_session, 11, "0", VOO=("1.0", "100.00"))  # withdrew $1000, price flat
    assert drawdown_series(db_session)[-1]["drawdown"] == 0.0


def test_empty_snapshot_table_returns_empty_not_an_error(db_session: Session) -> None:
    assert performance_series(db_session) == []
    assert drawdown_series(db_session) == []
