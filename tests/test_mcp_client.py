"""Phase 2 gate: retry policy, PAPER interception, paper-book broker rules, logging."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.models import Mode, PaperOrder, Position, TradeAction
from atlas.execution.mcp_client import ORDER_TOOLS, MCPClient, OptionOrdersDisabled
from atlas.execution.paper import PaperBook, PaperOrderRejected
from atlas.execution.transport import MCPTransportError
from tests.fakes import FakeMCPTransport

PRICES = {"VOO": Decimal("686.90"), "NVDA": Decimal("204.12"), "SOXX": Decimal("412.55")}


def price_provider(symbol: str) -> Decimal:
    return PRICES[symbol]


@pytest.fixture
def paper_client(db_session: Session) -> tuple[MCPClient, FakeMCPTransport, Session]:
    transport = FakeMCPTransport()
    book = PaperBook(db_session, price_provider)
    return MCPClient(transport, mode=Mode.PAPER, paper_book=book, sleep=lambda _: None), transport, db_session


# --- retry policy ------------------------------------------------------------


def test_read_calls_retry_on_transient_failure(db_session: Session) -> None:
    transport = FakeMCPTransport(fail_times={"get_equity_quotes": 2})
    client = MCPClient(
        transport, mode=Mode.PAPER, paper_book=PaperBook(db_session, price_provider), sleep=lambda _: None
    )
    quotes = client.get_equity_quotes(["SPY"])
    assert quotes["SPY"]["last_trade_price"] == "748.30"
    assert transport.call_count("get_equity_quotes") == 3  # 2 failures + 1 success


def test_read_calls_give_up_after_three_attempts(db_session: Session) -> None:
    transport = FakeMCPTransport(fail_times={"get_portfolio": 99})
    client = MCPClient(
        transport, mode=Mode.PAPER, paper_book=PaperBook(db_session, price_provider), sleep=lambda _: None
    )
    with pytest.raises(MCPTransportError):
        client.get_portfolio()
    assert transport.call_count("get_portfolio") == 3


def test_order_placement_is_never_retried_live() -> None:
    transport = FakeMCPTransport(fail_times={"place_equity_order": 99})
    client = MCPClient(transport, mode=Mode.LIVE, sleep=lambda _: None)
    with pytest.raises(MCPTransportError):
        client.place_equity_order(symbol="VOO", side="buy", order_type="market", amount="10")
    assert transport.call_count("place_equity_order") == 1  # exactly one attempt (§12)


# --- PAPER interception ------------------------------------------------------


def test_paper_mode_intercepts_place_and_never_hits_transport(
    paper_client: tuple[MCPClient, FakeMCPTransport, Session],
) -> None:
    client, transport, session = paper_client
    result = client.place_equity_order(
        symbol="VOO", side="buy", order_type="market", quantity=Decimal("0.5")
    )
    assert result["state"] == "filled"
    assert result["account"] == "paper"
    assert transport.call_count("place_equity_order") == 0  # structurally intercepted

    pos = session.scalars(select(Position).where(Position.ticker == "VOO")).one()
    assert pos.shares == Decimal("0.5")
    assert pos.account == "paper"
    order = session.scalars(select(PaperOrder)).one()
    assert order.side is TradeAction.BUY
    assert order.settles_on > order.placed_at.date()  # T+1


def test_paper_mode_requires_paper_book() -> None:
    with pytest.raises(ValueError):
        MCPClient(FakeMCPTransport(), mode=Mode.PAPER, paper_book=None)


def test_read_calls_pass_through_in_paper_mode(
    paper_client: tuple[MCPClient, FakeMCPTransport, Session],
) -> None:
    client, transport, _ = paper_client
    client.get_equity_positions()
    assert transport.call_count("get_equity_positions") == 1


def test_option_order_tools_disabled_in_v1(
    paper_client: tuple[MCPClient, FakeMCPTransport, Session],
) -> None:
    client, _, _ = paper_client
    with pytest.raises(OptionOrdersDisabled):
        client.call("place_option_order", legs=[])
    assert "place_option_order" in ("place_option_order",)  # documented member of ORDER_TOOLS
    assert "place_option_order" in ORDER_TOOLS


# --- paper book broker rules -------------------------------------------------


def test_fractional_limit_order_rejected(db_session: Session) -> None:
    book = PaperBook(db_session, price_provider)
    with pytest.raises(PaperOrderRejected, match="fractional"):
        book.place_equity_order(
            symbol="VOO",
            side="buy",
            order_type="limit",
            quantity=Decimal("0.5"),
            limit_price=Decimal("680"),
        )


def test_whole_share_limit_order_fills(db_session: Session) -> None:
    book = PaperBook(db_session, price_provider)
    result = book.place_equity_order(
        symbol="NVDA",
        side="buy",
        order_type="limit",
        quantity=Decimal("2"),
        limit_price=Decimal("210.00"),
    )
    # market (204.12) below limit (210) -> fills at market
    assert Decimal(result["fill_price"]) == Decimal("204.12")


def test_dollar_amount_buy_computes_fractional_shares(db_session: Session) -> None:
    book = PaperBook(db_session, price_provider)
    result = book.place_equity_order(
        symbol="VOO", side="buy", order_type="market", amount=Decimal("150.00")
    )
    assert Decimal(result["quantity"]) == (Decimal("150.00") / Decimal("686.90")).quantize(
        Decimal("0.000001")
    )


def test_cannot_sell_more_than_held(db_session: Session) -> None:
    book = PaperBook(db_session, price_provider)
    book.place_equity_order(symbol="VOO", side="buy", order_type="market", quantity=Decimal("1"))
    with pytest.raises(PaperOrderRejected, match="long-only"):
        book.place_equity_order(symbol="VOO", side="sell", order_type="market", quantity=Decimal("2"))


def test_sell_reduces_position_and_closes_at_zero(db_session: Session) -> None:
    book = PaperBook(db_session, price_provider)
    book.place_equity_order(symbol="VOO", side="buy", order_type="market", quantity=Decimal("1"))
    book.place_equity_order(symbol="VOO", side="sell", order_type="market", quantity=Decimal("1"))
    pos = db_session.scalars(select(Position).where(Position.ticker == "VOO")).one()
    assert pos.shares == 0
    assert pos.closed_at is not None


# --- structured logging ------------------------------------------------------


def test_every_call_logs_request_and_response(
    paper_client: tuple[MCPClient, FakeMCPTransport, Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _, _ = paper_client
    with caplog.at_level("INFO", logger="atlas.execution.mcp"):
        client.get_equity_quotes(["SPY"])
        client.place_equity_order(symbol="VOO", side="buy", order_type="market", quantity=Decimal("0.5"))
    messages = [r.message for r in caplog.records]
    assert "mcp request" in messages
    assert "mcp response" in messages
    assert "mcp paper fill" in messages
    datas = [getattr(r, "data", {}) for r in caplog.records]
    assert any(d.get("tool") == "get_equity_quotes" and "arguments" in d for d in datas)
