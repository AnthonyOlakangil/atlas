"""Phase 7 gate: e2e paper trade; placement provably unreachable without an
approved ticket; fractional/limit and settled-cash rules enforced at the gate."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.models import (
    CashEvent,
    Mode,
    OrderType,
    PaperOrder,
    Position,
    Ticket,
    TicketStatus,
    TradeAction,
)
from atlas.execution.approval import (
    ApprovalError,
    LiveModeLocked,
    approve_ticket,
    execute_ticket,
    reject_ticket,
)
from atlas.execution.mcp_client import MCPClient
from atlas.execution.paper import PaperBook
from atlas.execution.tickets import check_stops, expire_stale_tickets, list_tickets
from tests.fakes import FakeMCPTransport

PRICES = {"VOO": Decimal("686.90"), "QQQM": Decimal("230.50"), "NVDA": Decimal("204.12")}
TODAY = datetime.now(UTC).date()


def make_ticket(session: Session, **overrides: object) -> Ticket:
    fields: dict[str, object] = dict(
        ticker="VOO", action=TradeAction.BUY, sleeve="core", order_type=OrderType.MARKET,
        quantity=Decimal("0.2183"), dollar_amount=Decimal("150.00"), is_fractional=True,
        cash_before=Decimal("250.00"), cash_after=Decimal("100.00"),
        thesis="t", catalyst="c", risk="r", bear_case="b", falsifier="f",
        time_horizon="years", sizing_rationale="s", portfolio_impact="p",
    )
    fields.update(overrides)
    ticket = Ticket(**fields)  # type: ignore[arg-type]
    session.add(ticket)
    session.flush()
    return ticket


def fund(session: Session, amount: str = "250", on: date | None = None) -> None:
    session.add(CashEvent(on=on or TODAY, amount=Decimal(amount)))
    session.flush()


@pytest.fixture
def paper_client(db_session: Session) -> tuple[MCPClient, FakeMCPTransport]:
    transport = FakeMCPTransport()
    book = PaperBook(db_session, lambda sym: PRICES[sym])
    return MCPClient(transport, mode=Mode.PAPER, paper_book=book, sleep=lambda _: None), transport


YES = lambda _prompt: True  # noqa: E731
NO = lambda _prompt: False  # noqa: E731


# --- the gate: placement unreachable without an approved ticket ---------------


def test_pending_ticket_cannot_execute(
    db_session: Session, paper_client: tuple[MCPClient, FakeMCPTransport]
) -> None:
    client, transport = paper_client
    fund(db_session)
    ticket = make_ticket(db_session)
    with pytest.raises(ApprovalError, match="only an APPROVED ticket"):
        execute_ticket(ticket.id, session=db_session, client=client, confirm=YES)
    assert transport.call_count("place_equity_order") == 0
    assert not db_session.scalars(select(PaperOrder)).all()  # nothing reached the paper book
    assert ticket.status is TicketStatus.PENDING_APPROVAL


def test_unknown_ticket_id_cannot_execute(
    db_session: Session, paper_client: tuple[MCPClient, FakeMCPTransport]
) -> None:
    client, _ = paper_client
    with pytest.raises(ApprovalError, match="no ticket"):
        execute_ticket(999, session=db_session, client=client, confirm=YES)


def test_rejected_ticket_cannot_execute_or_reapprove(
    db_session: Session, paper_client: tuple[MCPClient, FakeMCPTransport]
) -> None:
    client, _ = paper_client
    ticket = make_ticket(db_session)
    reject_ticket(ticket.id, db_session)
    with pytest.raises(ApprovalError):
        approve_ticket(ticket.id, db_session)
    with pytest.raises(ApprovalError):
        execute_ticket(ticket.id, session=db_session, client=client, confirm=YES)


# --- end-to-end paper trade ---------------------------------------------------


def test_e2e_paper_trade_through_the_gate(
    db_session: Session, paper_client: tuple[MCPClient, FakeMCPTransport]
) -> None:
    client, transport = paper_client
    fund(db_session)
    ticket = make_ticket(db_session)
    approve_ticket(ticket.id, db_session)
    result = execute_ticket(ticket.id, session=db_session, client=client, assume_yes=True, confirm=NO)

    assert result["state"] == "filled" and result["account"] == "paper"
    assert transport.call_count("place_equity_order") == 0  # intercepted by PAPER mode
    assert transport.call_count("review_equity_order") == 1  # pre-flight review always ran

    db_session.refresh(ticket)
    assert ticket.status is TicketStatus.EXECUTED
    assert ticket.executed_at is not None
    assert ticket.fill_price == Decimal("686.90")

    order = db_session.scalars(select(PaperOrder)).one()
    assert order.ticket_id == ticket.id and order.settles_on > TODAY

    pos = db_session.scalars(select(Position).where(Position.ticker == "VOO")).one()
    assert pos.sleeve == "core"  # claimed from 'unassigned' by the executing ticket


# --- broker + cash rules re-checked at the gate -------------------------------


def test_fractional_limit_ticket_rejected_before_broker(
    db_session: Session, paper_client: tuple[MCPClient, FakeMCPTransport]
) -> None:
    client, transport = paper_client
    fund(db_session)
    bad = make_ticket(
        db_session, order_type=OrderType.LIMIT, limit_price=Decimal("680.00"), is_fractional=True
    )
    approve_ticket(bad.id, db_session)
    with pytest.raises(ApprovalError, match="fractional"):
        execute_ticket(bad.id, session=db_session, client=client, confirm=YES)
    assert transport.call_count("place_equity_order") == 0
    assert transport.call_count("review_equity_order") == 0  # rejected before any broker contact
    assert bad.status is TicketStatus.APPROVED  # left for the operator to fix/reject


def test_buy_exceeding_settled_cash_rejected_at_execution(
    db_session: Session, paper_client: tuple[MCPClient, FakeMCPTransport]
) -> None:
    client, transport = paper_client
    fund(db_session, "100")  # ticket wants $150
    ticket = make_ticket(db_session)
    approve_ticket(ticket.id, db_session)
    with pytest.raises(ApprovalError, match="settled cash"):
        execute_ticket(ticket.id, session=db_session, client=client, confirm=YES)
    assert transport.call_count("place_equity_order") == 0


def test_sell_that_would_cause_gfv_blocked(
    db_session: Session, paper_client: tuple[MCPClient, FakeMCPTransport]
) -> None:
    client, transport = paper_client
    yesterday = TODAY - timedelta(days=1)
    tomorrow = TODAY + timedelta(days=1)
    fund(db_session, "100", on=yesterday)
    now = datetime.now(UTC)
    db_session.add_all(
        [
            PaperOrder(placed_at=now - timedelta(days=1), ticker="AAA", side=TradeAction.BUY,
                       order_type=OrderType.MARKET, quantity=Decimal("1"), notional=Decimal("100"),
                       fill_price=Decimal("100"), settles_on=TODAY),
            PaperOrder(placed_at=now - timedelta(hours=5), ticker="AAA", side=TradeAction.SELL,
                       order_type=OrderType.MARKET, quantity=Decimal("1"), notional=Decimal("100"),
                       fill_price=Decimal("100"), settles_on=tomorrow),
            PaperOrder(placed_at=now - timedelta(hours=4), ticker="BBB", side=TradeAction.BUY,
                       order_type=OrderType.MARKET, quantity=Decimal("1"), notional=Decimal("100"),
                       fill_price=Decimal("100"), settles_on=tomorrow),
        ]
    )
    db_session.add(Position(ticker="BBB", sleeve="speculative", shares=Decimal("1"),
                            cost_basis=Decimal("100")))
    db_session.flush()
    sell = make_ticket(
        db_session, ticker="BBB", action=TradeAction.SELL, quantity=Decimal("1"),
        is_fractional=False, dollar_amount=Decimal("100"),
    )
    approve_ticket(sell.id, db_session)
    with pytest.raises(ApprovalError, match="Good Faith Violation"):
        execute_ticket(sell.id, session=db_session, client=client, confirm=YES)
    assert transport.call_count("place_equity_order") == 0


# --- review warnings force a second confirmation ------------------------------


def test_review_warnings_require_confirmation_even_with_yes_flag(
    db_session: Session,
) -> None:
    transport = FakeMCPTransport(
        responses={"review_equity_order": {"warnings": ["High volatility instrument"]}}
    )
    book = PaperBook(db_session, lambda sym: PRICES[sym])
    client = MCPClient(transport, mode=Mode.PAPER, paper_book=book, sleep=lambda _: None)
    fund(db_session)
    ticket = make_ticket(db_session)
    approve_ticket(ticket.id, db_session)

    confirms: list[str] = []

    def declining(prompt: str) -> bool:
        confirms.append(prompt)
        return False

    with pytest.raises(ApprovalError, match="after review warnings"):
        execute_ticket(ticket.id, session=db_session, client=client, assume_yes=True, confirm=declining)
    assert confirms, "--yes must NOT skip the warnings confirmation"
    assert ticket.status is TicketStatus.APPROVED

    result = execute_ticket(ticket.id, session=db_session, client=client, assume_yes=True, confirm=YES)
    assert result["state"] == "filled"
    db_session.refresh(ticket)
    assert ticket.review_warnings["warnings"] == ["High volatility instrument"]


# --- LIVE gate ----------------------------------------------------------------


def test_live_mode_locked_without_30_paper_days(db_session: Session) -> None:
    client = MCPClient(FakeMCPTransport(), mode=Mode.LIVE, sleep=lambda _: None)
    fund(db_session)
    ticket = make_ticket(db_session, mode=Mode.LIVE)
    approve_ticket(ticket.id, db_session)
    with pytest.raises(LiveModeLocked, match="0/30"):
        execute_ticket(ticket.id, session=db_session, client=client, confirm=YES)


def test_live_placement_failure_never_retried_ticket_stays_approved(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas.execution.approval as approval_mod

    monkeypatch.setattr("atlas.portfolio.scoring.paper_days_logged", lambda s: 30)
    monkeypatch.setattr("atlas.portfolio.scoring.render_track_record", lambda s: "ok")
    transport = FakeMCPTransport(fail_times={"place_equity_order": 99})
    client = MCPClient(transport, mode=Mode.LIVE, sleep=lambda _: None)
    fund(db_session)
    ticket = make_ticket(db_session, mode=Mode.LIVE, is_fractional=False,
                         quantity=Decimal("1"), order_type=OrderType.LIMIT,
                         limit_price=Decimal("100.00"), dollar_amount=Decimal("100.00"))
    approve_ticket(ticket.id, db_session)
    with pytest.raises(ApprovalError, match="not retried"):
        execute_ticket(ticket.id, session=db_session, client=client, assume_yes=True, confirm=NO)
    assert transport.call_count("place_equity_order") == 1  # exactly one attempt (§12)
    assert ticket.status is TicketStatus.APPROVED  # surfaced, never silently consumed
    assert approval_mod  # (import used for monkeypatch scope clarity)


# --- app-managed stop monitor -------------------------------------------------


def _executed_buy_with_stop(session: Session) -> Ticket:
    ticket = make_ticket(
        session, ticker="QQQM", sleeve="satellite_ai_semis", quantity=Decimal("0.5"),
        dollar_amount=Decimal("115.25"), exit_stop=Decimal("212.06"), exit_target=Decimal("276.60"),
    )
    ticket.status = TicketStatus.EXECUTED
    ticket.executed_at = datetime.now(UTC)
    ticket.fill_price = Decimal("230.50")
    session.add(Position(ticker="QQQM", sleeve="satellite_ai_semis", shares=Decimal("0.5"),
                         cost_basis=Decimal("115.25")))
    session.flush()
    return ticket


def test_stop_trigger_proposes_exit_never_executes(db_session: Session) -> None:
    _executed_buy_with_stop(db_session)
    proposals = check_stops(db_session, {"QQQM": Decimal("210.00")})  # below stop
    assert len(proposals) == 1
    exit_ticket = proposals[0]
    assert exit_ticket.status is TicketStatus.PENDING_APPROVAL  # proposed, NOT placed
    assert exit_ticket.action is TradeAction.SELL
    assert exit_ticket.is_fractional and exit_ticket.order_type is OrderType.MARKET
    assert "stop level" in exit_ticket.catalyst

    again = check_stops(db_session, {"QQQM": Decimal("209.00")})
    assert again == []  # one open exit proposal per ticker — no spam


def test_stop_not_triggered_above_level(db_session: Session) -> None:
    _executed_buy_with_stop(db_session)
    assert check_stops(db_session, {"QQQM": Decimal("225.00")}) == []


def test_target_trigger_proposes_exit(db_session: Session) -> None:
    _executed_buy_with_stop(db_session)
    proposals = check_stops(db_session, {"QQQM": Decimal("280.00")})
    assert len(proposals) == 1 and "target level" in proposals[0].catalyst


# --- housekeeping -------------------------------------------------------------


def test_expire_stale_tickets(db_session: Session) -> None:
    old = make_ticket(db_session)
    old.created_at = datetime.now(UTC) - timedelta(days=4)
    fresh = make_ticket(db_session, ticker="NVDA")
    db_session.flush()
    assert expire_stale_tickets(db_session, older_than_days=3) == 1
    assert old.status is TicketStatus.EXPIRED
    assert fresh.status is TicketStatus.PENDING_APPROVAL


def test_list_tickets_renders(db_session: Session) -> None:
    make_ticket(db_session)
    lines = list_tickets(db_session)
    assert len(lines) == 1 and "PENDING_APPROVAL" in lines[0] and "VOO" in lines[0]
