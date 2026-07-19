"""Structured outputs for the research agents (§6). All Pydantic, all validated."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

ClaimKind = Literal["fact", "consensus", "speculation"]


class Claim(BaseModel):
    text: str
    kind: ClaimKind
    source: str  # outlet/name, e.g. "FRED DGS10" or "reuters"
    retrieved_at: str  # ISO timestamp of retrieval — every number carries one (§1.4)


class AnalystView(BaseModel):
    agent: str
    summary: str
    key_findings: list[Claim] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    injection_flags: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)


class TradeIdea(BaseModel):
    """A research-stage idea — NOT a ticket. Risk + sizing decide what survives."""

    ticker: str
    sleeve: str
    action: Literal["BUY", "SELL", "TRIM", "HOLD", "WATCH"]
    target_dollars: float = Field(ge=0)
    thesis: str
    catalyst: str
    risk: str
    bear_case: str
    falsifier: str
    time_horizon: str
    is_diversified_fund: bool = False
    beats_seed_alternative: str = ""  # required when the ticker is not a seed example (§2.0)
    confidence: float = Field(ge=0, le=1)


class SectorView(AnalystView):
    trade_ideas: list[TradeIdea] = Field(default_factory=list)
    rotation_notes: str = ""


class Veto(BaseModel):
    ticker: str
    reason: str


class ApprovedIdea(BaseModel):
    idea: TradeIdea
    sized_dollars: float
    quantity: str  # Decimal as string, 4dp
    order_type: Literal["market", "limit", "stop_limit"]
    limit_price: float | None = None
    is_fractional: bool
    cash_before: float
    cash_after: float
    sizing_notes: list[str] = Field(default_factory=list)
    portfolio_impact: str  # sleeve delta, correlation, cash delta — §2.0.1


class RiskAssessment(BaseModel):
    agent: str = "portfolio-risk"
    approved: list[ApprovedIdea] = Field(default_factory=list)
    vetoed: list[Veto] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)
    drift_notes: list[str] = Field(default_factory=list)
    gfv_status: str = ""
    settled_cash: float = 0.0
    unsettled_cash: float = 0.0
    total_value: float = 0.0
    sleeve_weights: dict[str, float] = Field(default_factory=dict)


class ResearchBundle(BaseModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    market: AnalystView
    news: AnalystView
    sentiment: AnalystView
    macro: AnalystView
    sector: SectorView
    anomalies: list[str] = Field(default_factory=list)

    def all_views(self) -> list[AnalystView]:
        return [self.market, self.news, self.sentiment, self.macro, self.sector]
