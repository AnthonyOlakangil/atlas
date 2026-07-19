"""The approval gate (§1.2): the ONLY path from a ticket to an order.

Invariants enforced here, in code:
- only an APPROVED ticket can execute; approval is a separate explicit step
- review_equity_order runs before ANY live order; warnings force a second confirmation
- fractional+limit is rejected before it can reach the broker
- a buy that exceeds settled cash at execution time is rejected (§2.1)
- a sell that would cause a GFV is rejected (§2.1)
- LIVE mode is unreachable before 30 logged paper days + a rendering track record (§1.3)
- a failed placement is NEVER auto-retried; the ticket stays approved-but-unexecuted
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.logging import get_logger
from atlas.core.models import Mode, OrderType, Position, Ticket, TicketStatus, TradeAction
from atlas.core.settings import get_settings
from atlas.execution.mcp_client import MCPClient
from atlas.execution.paper import PaperBook
from atlas.execution.transport import HTTPMCPTransport, MCPTransportError

log = get_logger("execution.approval")

PAPER_DAYS_REQUIRED_FOR_LIVE = 30

Confirm = Callable[[str], bool]


class ApprovalError(RuntimeError):
    pass


class LiveModeLocked(ApprovalError):
    pass


def approve_ticket(ticket_id: int, session: Session | None = None) -> None:
    if session is None:
        from atlas.core.db import session_scope

        with session_scope() as s:
            approve_ticket(ticket_id, s)
            return
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise ApprovalError(f"no ticket #{ticket_id}")
    if ticket.status is not TicketStatus.PENDING_APPROVAL:
        raise ApprovalError(f"ticket #{ticket_id} is {ticket.status.value}, not PENDING_APPROVAL")
    ticket.status = TicketStatus.APPROVED
    log.info(f"ticket #{ticket_id} approved by operator")


def reject_ticket(ticket_id: int, session: Session | None = None) -> None:
    if session is None:
        from atlas.core.db import session_scope

        with session_scope() as s:
            reject_ticket(ticket_id, s)
            return
    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise ApprovalError(f"no ticket #{ticket_id}")
    if ticket.status not in (TicketStatus.PENDING_APPROVAL, TicketStatus.APPROVED):
        raise ApprovalError(f"ticket #{ticket_id} is {ticket.status.value}")
    ticket.status = TicketStatus.REJECTED
    log.info(f"ticket #{ticket_id} rejected by operator")


def _default_confirm(prompt: str) -> bool:
    import typer

    return bool(typer.confirm(prompt))


def build_execution_client(session: Session) -> MCPClient:
    """Compose the runtime client: PAPER intercepts placements into the paper book."""
    settings = get_settings()
    mode = Mode(settings.atlas_mode)
    transport = HTTPMCPTransport(settings.rh_mcp_url, settings.rh_mcp_access_token)
    holder: list[MCPClient] = []

    def price_provider(symbol: str) -> Decimal:
        try:
            return Decimal(str(holder[0].paper_price_provider()(symbol)))
        except Exception:  # noqa: BLE001 — MCP may be unauthenticated; yfinance fallback
            from atlas.ingest.market import _yf_price

            return Decimal(str(_yf_price(symbol)))

    book = PaperBook(session, price_provider) if mode is Mode.PAPER else None
    client = MCPClient(transport, mode=mode, paper_book=book)
    holder.append(client)
    return client


def _check_live_gate(session: Session, mode: Mode) -> None:
    if mode is not Mode.LIVE:
        return
    from atlas.portfolio.scoring import paper_days_logged, render_track_record

    days = paper_days_logged(session)
    if days < PAPER_DAYS_REQUIRED_FOR_LIVE:
        raise LiveModeLocked(
            f"LIVE mode is locked: {days}/{PAPER_DAYS_REQUIRED_FOR_LIVE} consecutive paper "
            "trading days logged. Keep running in PAPER (§1.3)."
        )
    report = render_track_record(session)
    if not report.strip():
        raise LiveModeLocked("LIVE mode is locked: track-record report does not render (§1.3).")
    log.info(f"live gate passed: {days} paper days, track record renders")


def _revalidate(ticket: Ticket, session: Session) -> None:
    """Broker rules + cash rules re-checked at execution time, not just proposal time."""
    if ticket.is_fractional and ticket.order_type is not OrderType.MARKET:
        raise ApprovalError(
            f"ticket #{ticket.id}: fractional quantity with {ticket.order_type.value} order — "
            "Robinhood rejects fractional non-market orders; refusing before the broker does"
        )
    if ticket.is_fractional and ticket.limit_price is not None:
        raise ApprovalError(f"ticket #{ticket.id}: fractional ticket carries a limit price")

    from atlas.portfolio.state import load_paper_state

    state = load_paper_state(session, {}, datetime.now(UTC).date())
    if ticket.action is TradeAction.BUY and ticket.dollar_amount > state.cash.settled:
        raise ApprovalError(
            f"ticket #{ticket.id}: spend ${ticket.dollar_amount} exceeds settled cash "
            f"${state.cash.settled} — a buy is never funded by unsettled proceeds (§2.1)"
        )
    if ticket.action is TradeAction.SELL and state.ledger.would_cause_gfv(
        ticket.ticker, datetime.now(UTC).date()
    ):
        raise ApprovalError(
            f"ticket #{ticket.id}: selling {ticket.ticker} now would trigger a Good Faith "
            "Violation — blocked (§2.1)"
        )


def execute_ticket(
    ticket_id: int,
    *,
    assume_yes: bool = False,
    session: Session | None = None,
    client: MCPClient | None = None,
    confirm: Confirm | None = None,
) -> dict[str, object]:
    """Execute ONE approved ticket. This function is the entire attack surface for
    order placement — everything else in Atlas can only create PENDING_APPROVAL rows."""
    if session is None:
        from atlas.core.db import session_scope

        with session_scope() as s:
            return execute_ticket(
                ticket_id, assume_yes=assume_yes, session=s, client=client, confirm=confirm
            )
    confirm = confirm or _default_confirm

    ticket = session.get(Ticket, ticket_id)
    if ticket is None:
        raise ApprovalError(f"no ticket #{ticket_id}")
    if ticket.status is not TicketStatus.APPROVED:
        raise ApprovalError(
            f"ticket #{ticket_id} is {ticket.status.value} — only an APPROVED ticket can "
            "execute. Approve explicitly first: atlas approve --ticket-id "
            f"{ticket_id}"
        )

    client = client or build_execution_client(session)
    _check_live_gate(session, client.mode)
    _revalidate(ticket, session)

    # --- mandatory pre-flight review (§1.2) ----------------------------------
    warnings: list[str] = []
    try:
        review = client.review_equity_order(
            symbol=ticket.ticker,
            side=ticket.action.value.lower(),
            order_type=ticket.order_type.value,
            quantity=str(ticket.quantity),
            limit_price=str(ticket.limit_price) if ticket.limit_price else None,
        )
        raw = review.get("warnings", []) if isinstance(review, dict) else []
        warnings = [str(w) for w in raw]
    except MCPTransportError as exc:
        warnings = [f"review_equity_order failed: {exc}"]

    if warnings:
        print("⚠️  Broker review warnings:")
        for w in warnings:
            print(f"   - {w}")
        # warnings ALWAYS require an interactive second confirmation — --yes cannot skip it
        if not confirm("Proceed despite the warnings above?"):
            log.info(f"ticket #{ticket_id}: operator declined after warnings")
            raise ApprovalError("execution aborted by operator after review warnings")
    elif not assume_yes and not confirm(
        f"Execute {ticket.action.value} {ticket.quantity} {ticket.ticker} "
        f"(${ticket.dollar_amount}, {ticket.order_type.value}, {client.mode.value})?"
    ):
        raise ApprovalError("execution aborted by operator")

    # --- the one placement call; NEVER auto-retried (§12) --------------------
    try:
        result = client.place_equity_order(
            symbol=ticket.ticker,
            side=ticket.action.value.lower(),
            order_type=ticket.order_type.value,
            quantity=ticket.quantity,
            limit_price=ticket.limit_price,
            ticket_id=ticket.id,
        )
    except Exception as exc:
        ticket.review_warnings = {"warnings": warnings, "error": str(exc)}
        log.error(
            "placement failed — ticket left APPROVED, no auto-retry",
            extra={"data": {"ticket": ticket.id, "error": str(exc)}},
        )
        raise ApprovalError(f"placement failed (not retried): {exc}") from exc

    ticket.status = TicketStatus.EXECUTED
    ticket.executed_at = datetime.now(UTC)
    if isinstance(result, dict) and result.get("fill_price"):
        ticket.fill_price = Decimal(str(result["fill_price"]))
    ticket.review_warnings = {"warnings": warnings}

    # paper fills land with sleeve "unassigned"; claim them for this ticket's sleeve
    for pos in session.scalars(
        select(Position).where(Position.ticker == ticket.ticker, Position.sleeve == "unassigned")
    ):
        pos.sleeve = ticket.sleeve
    session.flush()
    log.info(
        "ticket executed",
        extra={"data": {"ticket": ticket.id, "mode": client.mode.value, "result": result}},
    )
    return dict(result) if isinstance(result, dict) else {"result": result}
