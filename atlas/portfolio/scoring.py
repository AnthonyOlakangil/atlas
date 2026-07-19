"""Track record: every ticket scored at +1d/+5d/+20d vs SPY (§1.3).

This is the honesty mechanism. It renders verbatim, including when it's bad.
Scoring windows are computed in trading days; the scheduler (Phase 9) calls
score_due_tickets daily.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.calendar import add_trading_days
from atlas.core.logging import get_logger
from atlas.core.models import Score, Snapshot, Ticket, TicketStatus

log = get_logger("portfolio.scoring")

WINDOWS = {"+1d": 1, "+5d": 5, "+20d": 20}

# (ticker, on_date) -> close price; injected so tests and backfills stay offline
PriceLookup = Callable[[str, date], Decimal | None]


def score_due_tickets(session: Session, today: date, lookup: PriceLookup) -> int:
    """Score every executed ticket whose windows have matured. Returns scores written."""
    written = 0
    tickets = session.scalars(
        select(Ticket).where(Ticket.status == TicketStatus.EXECUTED, Ticket.fill_price.is_not(None))
    ).all()
    for ticket in tickets:
        assert ticket.executed_at is not None and ticket.fill_price is not None
        base_day = ticket.executed_at.date()
        existing = {s.window for s in ticket.scores}
        for window, days in WINDOWS.items():
            if window in existing:
                continue
            due = add_trading_days(base_day, days)
            if due > today:
                continue
            t_price = lookup(ticket.ticker, due)
            spy_then = lookup("SPY", due)
            spy_base = lookup("SPY", base_day)
            if t_price is None or spy_then is None or spy_base in (None, Decimal(0)):
                log.warning(f"cannot score {ticket.ticker} {window}: missing price data")
                continue
            assert spy_base is not None
            t_ret = (t_price - ticket.fill_price) / ticket.fill_price * 100
            s_ret = (spy_then - spy_base) / spy_base * 100
            if ticket.action.value == "SELL":
                t_ret = -t_ret  # a good sell is one the price fell after
            session.add(
                Score(
                    ticket=ticket,  # via relationship so ticket.scores stays coherent
                    window=window,
                    ticket_return_pct=t_ret.quantize(Decimal("0.0001")),
                    spy_return_pct=s_ret.quantize(Decimal("0.0001")),
                    alpha_pct=(t_ret - s_ret).quantize(Decimal("0.0001")),
                )
            )
            written += 1
    session.flush()
    return written


def track_record_stats(session: Session) -> dict[str, object]:
    scores = session.scalars(select(Score)).all()
    by_window: dict[str, list[Score]] = {}
    for s in scores:
        by_window.setdefault(s.window, []).append(s)

    stats: dict[str, object] = {"windows": {}, "scored": len(scores)}
    for window, rows in sorted(by_window.items()):
        wins = [r for r in rows if r.alpha_pct > 0]
        losses = [r for r in rows if r.alpha_pct <= 0]
        stats["windows"][window] = {  # type: ignore[index]
            "n": len(rows),
            "hit_rate": round(len(wins) / len(rows), 3) if rows else None,
            "avg_win_alpha": round(float(sum(r.alpha_pct for r in wins) / len(wins)), 3) if wins else None,
            "avg_loss_alpha": round(float(sum(r.alpha_pct for r in losses) / len(losses)), 3) if losses else None,
            "avg_alpha": round(float(sum(r.alpha_pct for r in rows) / len(rows)), 3) if rows else None,
        }

    snaps = session.scalars(select(Snapshot).order_by(Snapshot.taken_at)).all()
    peak = trough_dd = Decimal(0)
    for s in snaps:
        peak = max(peak, s.total_value)
        if peak > 0:
            trough_dd = min(trough_dd, (s.total_value - peak) / peak)
    stats["max_drawdown_pct"] = round(float(trough_dd * 100), 2)
    stats["paper_days_logged"] = len({s.taken_at.date() for s in snaps})
    return stats


def paper_days_logged(session: Session) -> int:
    return int(track_record_stats(session)["paper_days_logged"])  # type: ignore[arg-type]


def render_track_record(session: Session | None = None) -> str:
    """Verbatim track record — displayed even when it's bad (§1.3)."""
    if session is None:
        from atlas.core.db import session_scope

        with session_scope() as s:
            return render_track_record(s)

    stats = track_record_stats(session)
    lines = ["# Atlas Track Record", ""]
    if stats["scored"] == 0:
        lines.append(
            "No scored tickets yet — windows mature at +1/+5/+20 trading days after execution."
        )
    windows = stats["windows"]
    assert isinstance(windows, dict)
    for window, w in windows.items():
        lines.append(
            f"- **{window}**: n={w['n']}, hit rate {w['hit_rate']}, "
            f"avg alpha {w['avg_alpha']}% (win {w['avg_win_alpha']}% / loss {w['avg_loss_alpha']}%)"
        )
    lines.append(f"- **Max drawdown (paper book)**: {stats['max_drawdown_pct']}%")
    lines.append(f"- **Paper trading days logged**: {stats['paper_days_logged']} / 30 required for LIVE")
    lines.append("")
    lines.append(
        "_If no edge shows after 30 days, weight the index core more heavily — that outcome "
        "is the measurement system succeeding, not the project failing._"
    )
    return "\n".join(lines)


def track_record_section(session: Session) -> str:
    """Compact version for the daily brief."""
    stats = track_record_stats(session)
    if stats["scored"] == 0:
        return (
            f"No scored tickets yet ({stats['paper_days_logged']} paper day(s) logged). "
            "Outcomes are scored at +1/+5/+20 trading days vs SPY."
        )
    windows = stats["windows"]
    assert isinstance(windows, dict)
    parts = [
        f"{win}: hit {w['hit_rate']}, alpha {w['avg_alpha']}%" for win, w in windows.items()
    ]
    return (
        "; ".join(parts)
        + f". Max drawdown {stats['max_drawdown_pct']}%. "
        + f"{stats['paper_days_logged']} paper day(s) logged."
    )
