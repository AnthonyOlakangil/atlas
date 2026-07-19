"""Offline fakes: the whole suite runs with no network (§5, §10 gates)."""

from __future__ import annotations

from typing import Any

from atlas.execution.transport import MCPTransportError

DEFAULT_RESPONSES: dict[str, Any] = {
    "get_accounts": [
        {"account_number": "AG-001", "type": "agentic", "tradable": True},
        {"account_number": "RH-MAIN", "type": "individual", "tradable": False},
    ],
    "get_portfolio": {
        "account": "AG-001",
        "equity": "250.00",
        "cash": "250.00",
    },
    "get_equity_quotes": {
        "SPY": {"symbol": "SPY", "last_trade_price": "748.30"},
        "VOO": {"symbol": "VOO", "last_trade_price": "686.90"},
        "NVDA": {"symbol": "NVDA", "last_trade_price": "204.12"},
        "SOXX": {"symbol": "SOXX", "last_trade_price": "412.55"},
        "GLDM": {"symbol": "GLDM", "last_trade_price": "80.15"},
    },
    "get_equity_positions": [],
    "get_equity_orders": [],
    "get_equity_tradability": {"symbol": "VOO", "tradable": True, "fractional_tradable": True},
    "review_equity_order": {"warnings": []},
    "get_indexes": [{"symbol": "SPX", "name": "S&P 500"}],
}


class FakeMCPTransport:
    """Canned-response transport. Records every call; can fail N times per tool."""

    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        fail_times: dict[str, int] | None = None,
    ) -> None:
        self.responses = {**DEFAULT_RESPONSES, **(responses or {})}
        self.fail_times = dict(fail_times or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if self.fail_times.get(name, 0) > 0:
            self.fail_times[name] -= 1
            raise MCPTransportError(f"simulated transient failure for {name}")
        if name not in self.responses:
            raise MCPTransportError(f"FakeMCPTransport has no fixture for {name}")
        return self.responses[name]

    def call_count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)
