"""Offline fakes: the whole suite runs with no network (§5, §10 gates)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from atlas.core.llm import LLMUnavailable
from atlas.execution.transport import MCPTransportError
from atlas.research.agents.schemas import AnalystView, Claim, SectorView, TradeIdea

FIXED_TS = "2026-07-17T13:30:00+00:00"


def canned_analyst_view(agent: str) -> AnalystView:
    return AnalystView(
        agent=agent,
        summary=f"Canned {agent} analysis: market steady, chips rotating, Fed hawkish.",
        key_findings=[
            Claim(text="SPY at 748.30", kind="fact", source="robinhood_mcp", retrieved_at=FIXED_TS),
            Claim(text="Analysts see 8,000 year-end", kind="consensus", source="reuters", retrieved_at=FIXED_TS),
            Claim(text="Rotation may continue", kind="speculation", source="atlas-inference", retrieved_at=FIXED_TS),
        ],
        confidence=0.8,
    )


def canned_sector_view() -> SectorView:
    base = canned_analyst_view("sector-analyst")
    return SectorView(
        **base.model_dump(),
        rotation_notes="NVDA lagging; MU/INTC led; SOXX breadth beat SMH.",
        trade_ideas=[
            TradeIdea(
                ticker="VOO", sleeve="core", action="BUY", target_dollars=140,
                thesis="Anchor the core before satellites.", catalyst="none — structural",
                risk="broad-market drawdown", bear_case="CAPE 41 implies low forward returns",
                falsifier="break of 200-day trend", time_horizon="years",
                is_diversified_fund=True, confidence=0.9,
            ),
            TradeIdea(
                ticker="QQQM", sleeve="satellite_ai_semis", action="BUY", target_dollars=55,
                thesis="Broad growth exposure at lower fee while chips digest.",
                catalyst="Q2 earnings wave starting Jul 14",
                risk="rate-driven multiple compression", bear_case="AI capex disappoints",
                falsifier="hyperscaler capex guides down", time_horizon="months",
                beats_seed_alternative="QQQM 0.15% fee vs QQQ 0.20%; not in seed list by design",
                confidence=0.7,
            ),
            TradeIdea(
                ticker="GLDM", sleeve="diversifier", action="BUY", target_dollars=30,
                thesis="Offset tech concentration; central-bank bid persists.",
                catalyst="FOMC Jul 29", risk="high real rates", bear_case="gold yields nothing",
                falsifier="sustained real-rate spike with gold under 3800", time_horizon="months",
                confidence=0.6,
            ),
        ],
    )


class FakeLLMClient:
    """Deterministic canned responses keyed by schema; can simulate agent failures."""

    def __init__(
        self,
        overrides: dict[type[BaseModel], BaseModel] | None = None,
        fail_agents: frozenset[str] = frozenset(),
    ) -> None:
        self.overrides = overrides or {}
        self.fail_agents = fail_agents
        self.calls: list[tuple[str, str]] = []

    def complete(self, messages: list[dict[str, str]], schema: type, *, agent: str = "") -> Any:
        self.calls.append((agent, schema.__name__))
        if agent in self.fail_agents:
            raise LLMUnavailable(f"simulated outage for {agent}")
        if schema in self.overrides:
            return self.overrides[schema].model_copy(deep=True)
        if schema is SectorView:
            return canned_sector_view()
        if schema is AnalystView:
            return canned_analyst_view(agent or "analyst")
        if schema.__name__ == "SynthesisNarrative":
            return schema(
                tldr=[
                    "Market steady; chips rotating beneath the surface.",
                    "Three sized proposals today, all inside bands with settled cash to spare.",
                    "Fed stays hawkish — expensive growth carries rate risk.",
                ],
                teaching_note="A limit order caps what you pay; a market order guarantees the fill instead.",
                no_trade_rationale="No setups cleared the risk gate.",
            )
        if schema.__name__ == "LLMCritique":
            return schema(defects=[], verdict="clean")
        raise AssertionError(f"FakeLLMClient has no canned response for {schema}")

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
