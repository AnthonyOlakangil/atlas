"""The single wrapper every Robinhood MCP call goes through (§5).

Responsibilities:
- structured logging of the exact request/response for every call
- retry with exponential backoff for transient READ failures
- NEVER retry order placement (§12: surface the error, leave the ticket pending)
- PAPER mode: `place_*`/`cancel_*` order calls are intercepted into the PaperBook
  and never reach the transport
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from atlas.core.logging import get_logger
from atlas.core.models import Mode
from atlas.execution.paper import PaperBook
from atlas.execution.transport import MCPTransport, MCPTransportError

log = get_logger("execution.mcp")

# Order-mutating tools: intercepted in PAPER mode, never retried in LIVE mode.
ORDER_TOOLS = (
    "place_equity_order",
    "cancel_equity_order",
    "place_option_order",
    "cancel_option_order",
)

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 0.5  # seconds; exponential


class OptionOrdersDisabled(RuntimeError):
    """v1 never generates option tickets (§12) — belt and suspenders."""


class MCPClient:
    def __init__(
        self,
        transport: MCPTransport,
        *,
        mode: Mode = Mode.PAPER,
        paper_book: PaperBook | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        if mode is Mode.PAPER and paper_book is None:
            raise ValueError("PAPER mode requires a PaperBook")
        self._transport = transport
        self._mode = mode
        self._paper = paper_book
        self._sleep = sleep

    @property
    def mode(self) -> Mode:
        return self._mode

    def call(self, tool: str, **arguments: Any) -> Any:
        """Route one MCP tool call: paper interception, retry policy, logging."""
        log.info("mcp request", extra={"data": {"tool": tool, "arguments": arguments, "mode": self._mode.value}})
        if tool.startswith("place_option") or tool.startswith("cancel_option"):
            raise OptionOrdersDisabled("options order tools are disabled in v1 (§12)")

        if self._mode is Mode.PAPER and tool in ORDER_TOOLS:
            assert self._paper is not None
            handler = getattr(self._paper, tool)
            result = handler(**arguments)
            log.info("mcp paper fill", extra={"data": {"tool": tool, "result": result}})
            return result

        attempts = 1 if tool in ORDER_TOOLS else RETRY_ATTEMPTS
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = self._transport.call_tool(tool, arguments)
                log.info("mcp response", extra={"data": {"tool": tool, "attempt": attempt, "result": result}})
                return result
            except MCPTransportError as exc:
                last_error = exc
                log.warning(
                    "mcp transport error",
                    extra={"data": {"tool": tool, "attempt": attempt, "error": str(exc)}},
                )
                if attempt < attempts:
                    self._sleep(RETRY_BASE_DELAY * 2 ** (attempt - 1))
        assert last_error is not None
        raise last_error

    # --- account / portfolio -------------------------------------------------
    def get_accounts(self) -> Any:
        return self.call("get_accounts")

    def get_portfolio(self, **kw: Any) -> Any:
        return self.call("get_portfolio", **kw)

    def get_realized_pnl(self, **kw: Any) -> Any:
        return self.call("get_realized_pnl", **kw)

    # --- market data ---------------------------------------------------------
    def get_equity_quotes(self, symbols: list[str]) -> Any:
        if len(symbols) > 20:
            raise ValueError("get_equity_quotes accepts at most 20 symbols (§5)")
        return self.call("get_equity_quotes", symbols=symbols)

    def get_equity_historicals(self, **kw: Any) -> Any:
        return self.call("get_equity_historicals", **kw)

    def get_equity_fundamentals(self, **kw: Any) -> Any:
        return self.call("get_equity_fundamentals", **kw)

    def get_equity_technical_indicators(self, **kw: Any) -> Any:
        return self.call("get_equity_technical_indicators", **kw)

    def get_earnings_calendar(self, **kw: Any) -> Any:
        return self.call("get_earnings_calendar", **kw)

    def get_earnings_results(self, **kw: Any) -> Any:
        return self.call("get_earnings_results", **kw)

    def get_indexes(self) -> Any:
        return self.call("get_indexes")

    def get_index_quotes(self, **kw: Any) -> Any:
        return self.call("get_index_quotes", **kw)

    # --- equities ------------------------------------------------------------
    def get_equity_positions(self, **kw: Any) -> Any:
        return self.call("get_equity_positions", **kw)

    def get_equity_orders(self, **kw: Any) -> Any:
        return self.call("get_equity_orders", **kw)

    def get_equity_tradability(self, **kw: Any) -> Any:
        return self.call("get_equity_tradability", **kw)

    def review_equity_order(self, **kw: Any) -> Any:
        return self.call("review_equity_order", **kw)

    def place_equity_order(self, **kw: Any) -> Any:
        return self.call("place_equity_order", **kw)

    def cancel_equity_order(self, **kw: Any) -> Any:
        return self.call("cancel_equity_order", **kw)

    # --- options (data/analysis only in v1) ----------------------------------
    def get_option_chains(self, **kw: Any) -> Any:
        return self.call("get_option_chains", **kw)

    def get_option_quotes(self, **kw: Any) -> Any:
        return self.call("get_option_quotes", **kw)

    def get_option_positions(self, **kw: Any) -> Any:
        return self.call("get_option_positions", **kw)

    def paper_price_provider(self) -> Any:
        """Quote lookup for paper fills, routed through this client's read path."""

        def provider(symbol: str) -> Decimal:
            quotes = self.get_equity_quotes([symbol])
            if isinstance(quotes, dict):
                entry = quotes.get(symbol) or next(iter(quotes.get("quotes", [])), None)
            else:
                entry = quotes[0] if quotes else None
            if entry is None:
                raise MCPTransportError(f"no quote for {symbol}")
            price = entry.get("last_trade_price") or entry.get("price") or entry.get("last")
            return Decimal(str(price))

        return provider
