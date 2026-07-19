"""Ticket queries and the app-managed stop monitor (§5 gap 1).

The stop monitor NEVER executes: a triggered stop/target produces a new
PENDING_APPROVAL exit ticket for the operator — the approval gate holds
everywhere, always.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.logging import get_logger
from atlas.core.models import OrderType, Position, Ticket, TicketStatus, TradeAction

log = get_logger("execution.tickets")


def list_tickets(session: Session | None = None) -> list[str]:
    if session is None:
        from atlas.core.db import session_scope

        with session_scope() as s:
            return list_tickets(s)
    rows = session.scalars(select(Ticket).order_by(Ticket.created_at.desc())).all()
    if not rows:
        return ["(no tickets yet — run `atlas cycle`)"]
    out = []
    for t in rows:
        price = f" @ limit ${t.limit_price}" if t.limit_price else " @ market"
        out.append(
            f"#{t.id} [{t.status.value}] {t.action.value} {t.quantity} {t.ticker}"
            f"{price} — ${t.dollar_amount} ({t.sleeve}, {t.mode.value})"
        )
    return out


def get_ticket(session: Session, ticket_id: int) -> Ticket | None:
    return session.get(Ticket, ticket_id)


def expire_stale_tickets(session: Session, older_than_days: int = 3) -> int:
    """Unapproved tickets go stale: theses are dated, prices move (honesty over convenience)."""
    now = datetime.now(UTC)
    count = 0
    for t in session.scalars(select(Ticket).where(Ticket.status == TicketStatus.PENDING_APPROVAL)):
        created = t.created_at if t.created_at.tzinfo else t.created_at.replace(tzinfo=UTC)
        if (now - created).days >= older_than_days:
            t.status = TicketStatus.EXPIRED
            count += 1
    session.flush()
    return count


def check_stops(session: Session, prices: dict[str, Decimal]) -> list[Ticket]:
    """Scan executed tickets' stop/target levels; propose (never place) exits.

    Returns newly created PENDING_APPROVAL exit tickets.
    """
    proposals: list[Ticket] = []
    executed = session.scalars(
        select(Ticket).where(
            Ticket.status == TicketStatus.EXECUTED,
            Ticket.action == TradeAction.BUY,
        )
    ).all()
    open_exit_tickers = {
        t.ticker
        for t in session.scalars(
            select(Ticket).where(
                Ticket.status == TicketStatus.PENDING_APPROVAL,
                Ticket.action == TradeAction.SELL,
            )
        )
    }
    for t in executed:
        if t.exit_stop is None and t.exit_target is None:
            continue
        if t.ticker in open_exit_tickers:
            continue  # one open exit proposal per ticker
        price = prices.get(t.ticker)
        if price is None:
            continue
        pos = session.scalars(
            select(Position).where(
                Position.ticker == t.ticker,
                Position.account == "paper",
                Position.closed_at.is_(None),
            )
        ).first()
        if pos is None or pos.shares <= 0:
            continue

        trigger: str | None = None
        if t.exit_stop is not None and price <= t.exit_stop:
            trigger = f"stop level ${t.exit_stop} hit (price ${price})"
        elif t.exit_target is not None and price >= t.exit_target:
            trigger = f"target level ${t.exit_target} hit (price ${price})"
        if trigger is None:
            continue

        is_frac = pos.shares != pos.shares.to_integral_value()
        proceeds = (pos.shares * price).quantize(Decimal("0.01"))
        exit_ticket = Ticket(
            ticker=t.ticker,
            action=TradeAction.SELL,
            sleeve=t.sleeve,
            order_type=OrderType.MARKET if is_frac else OrderType.STOP_LIMIT,
            quantity=pos.shares,
            dollar_amount=proceeds,
            limit_price=None if is_frac else (price * Decimal("0.995")).quantize(Decimal("0.01")),
            is_fractional=is_frac,
            cash_before=Decimal("0"),
            cash_after=Decimal("0"),
            thesis=f"App-managed exit: {trigger}. Original thesis: {t.thesis[:200]}",
            catalyst=trigger,
            risk="Exiting realizes the current P&L; the level may be noise.",
            bear_case="The trigger is a wick, not a break — exiting locks in the drawdown.",
            falsifier="price reclaims the entry level on volume",
            time_horizon="immediate",
            sizing_rationale="full position exit per app-managed stop plan",
            portfolio_impact=f"closes the {t.ticker} position; proceeds settle T+1 (unsettled until then)",
            learning_note=(
                "App-managed stops only work while Atlas is running — Robinhood has no "
                "bracket orders, so the level lives here, not at the broker."
            ),
            brief_id=t.brief_id,
        )
        session.add(exit_ticket)
        proposals.append(exit_ticket)
        log.info(
            "stop monitor proposal",
            extra={"data": {"ticker": t.ticker, "trigger": trigger, "ticket": "PENDING_APPROVAL"}},
        )
    session.flush()
    return proposals
