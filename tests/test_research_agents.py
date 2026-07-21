"""Phase 5 gate: offline deterministic ResearchBundle via FakeLLMClient + risk agent."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from atlas.core.settings import OperatorConfig
from atlas.ingest.macro import MacroContext
from atlas.ingest.market import Indicators, MarketSnapshot, Quote
from atlas.ingest.news import NewsDigest, NewsItem
from atlas.ingest.social import SentimentReport
from atlas.portfolio.state import CashState, Ledger, PortfolioState, PositionView
from atlas.research.agents.risk import MAX_TICKETS_PER_DAY, run_risk_agent
from atlas.research.agents.runners import run_sentiment_agent
from atlas.research.agents.schemas import AnalystView, Claim, ResearchBundle, TradeIdea
from atlas.research.orchestrator import build_research_bundle
from tests.fakes import FIXED_TS, FakeLLMClient, canned_analyst_view, canned_sector_view

CFG = OperatorConfig.load()
NOW = datetime(2026, 7, 17, 13, 30, tzinfo=UTC)

PRICES = {
    "VOO": Decimal("686.90"), "QQQM": Decimal("230.50"), "GLDM": Decimal("80.15"),
    "NVDA": Decimal("204.12"), "SPY": Decimal("748.30"),
}


def fixture_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        as_of=NOW,
        quotes={s: Quote(symbol=s, price=float(p), source="fixture", as_of=NOW) for s, p in PRICES.items()},
        indicators={"NVDA": Indicators(symbol="NVDA", rsi_14=48.2)},
        index_levels={"SPY": 748.30},
    )


def fixture_digest() -> NewsDigest:
    return NewsDigest(
        items=[
            NewsItem(title="Q2 bank earnings begin", url="u", published=NOW, source="reuters",
                     trust_tier="secondary"),
        ]
    )


def fixture_state(settled: str = "250") -> PortfolioState:
    cash = CashState(settled=Decimal(settled), unsettled=Decimal(0))
    return PortfolioState(
        as_of=date(2026, 7, 17), positions=[], cash=cash,
        ledger=Ledger(cash=cash, gfv_events=[], open_lots=[]),
    )


def build_offline_bundle(llm: FakeLLMClient | None = None) -> ResearchBundle:
    return build_research_bundle(
        llm=llm or FakeLLMClient(),
        snapshot=fixture_snapshot(),
        digest=fixture_digest(),
        sentiment=SentimentReport(posts_analyzed=3, ticker_mentions={"NVDA": 2}, bullish_ratio=0.7),
        macro=MacroContext(),
        cfg=CFG,
        holdings_summary="(no positions yet)",
    )


# --- the Phase 5 gate: offline, deterministic, valid --------------------------


def test_offline_bundle_valid_and_deterministic(tmp_path: Path) -> None:
    b1, b2 = build_offline_bundle(), build_offline_bundle()
    for b in (b1, b2):
        assert {v.agent for v in b.all_views()} == {
            "market-data", "news-analyst", "sentiment-analyst", "macro-analyst", "sector-analyst",
        }
        assert b.sector.trade_ideas, "sector agent must produce ideas"
    d1, d2 = b1.model_dump(exclude={"generated_at"}), b2.model_dump(exclude={"generated_at"})
    assert d1 == d2  # deterministic from fixtures (§Phase 5 gate)
    # serializes cleanly for runs/<date>/ debugging
    (tmp_path / "bundle.json").write_text(b1.model_dump_json())


def test_all_five_analysts_called_in_parallel_run() -> None:
    llm = FakeLLMClient()
    build_offline_bundle(llm)
    agents = {a for a, _ in llm.calls}
    assert agents == {"market-data", "news-analyst", "sentiment-analyst", "macro-analyst", "sector-analyst"}


def test_agent_outage_degrades_but_cycle_continues() -> None:
    bundle = build_offline_bundle(FakeLLMClient(fail_agents=frozenset({"macro-analyst"})))
    assert bundle.macro.confidence == 0.0
    assert any("macro-analyst" in a for a in bundle.anomalies)
    assert bundle.market.confidence > 0  # everyone else unaffected


# --- sentiment: structural clamps, not prompt hopes ---------------------------


def test_sentiment_agent_structurally_clamped() -> None:
    overconfident = canned_analyst_view("sentiment-analyst")
    overconfident.confidence = 0.95
    overconfident.key_findings = [
        Claim(text="crowd is euphoric", kind="fact", source="reddit", retrieved_at=FIXED_TS)
    ]
    llm = FakeLLMClient(overrides={AnalystView: overconfident})
    view = run_sentiment_agent(SentimentReport(posts_analyzed=5), llm)
    assert view.confidence <= 0.4  # ceiling enforced in code
    assert all(c.kind == "speculation" for c in view.key_findings)  # facts demoted in code
    assert view.summary.startswith("[SENTIMENT ONLY")


# --- risk agent ---------------------------------------------------------------


def _bundle_with_ideas(ideas: list[TradeIdea]) -> ResearchBundle:
    sector = canned_sector_view()
    sector.trade_ideas = ideas
    return ResearchBundle(
        market=canned_analyst_view("market-data"),
        news=canned_analyst_view("news-analyst"),
        sentiment=canned_analyst_view("sentiment-analyst"),
        macro=canned_analyst_view("macro-analyst"),
        sector=sector,
    )


def _idea(**kw: object) -> TradeIdea:
    base = dict(
        ticker="QQQM", sleeve="satellite_ai_semis", action="BUY", target_dollars=55.0,
        thesis="t", catalyst="c", risk="r", bear_case="b", falsifier="f",
        time_horizon="months", confidence=0.7,
    )
    base.update(kw)
    return TradeIdea(**base)  # type: ignore[arg-type]


def test_risk_approves_and_sizes_non_seed_ticker_end_to_end() -> None:
    assessment = run_risk_agent(_bundle_with_ideas([_idea()]), fixture_state(), CFG, PRICES)
    assert len(assessment.approved) == 1
    a = assessment.approved[0]
    assert a.idea.ticker == "QQQM"  # not in any seed list — §2.0 holds end to end
    assert a.is_fractional and a.order_type == "market"
    assert a.cash_before == 250.0 and a.cash_after == 195.0
    assert "sleeve" in a.portfolio_impact and "settled cash" in a.portfolio_impact


def test_risk_unaffordable_idea_becomes_watch_item() -> None:
    # a real book (so bands don't bind) but nearly no settled cash — cash is the constraint
    state = fixture_state(settled="15")
    state.positions.append(
        PositionView(ticker="NVDA", sleeve="satellite_ai_semis", shares=Decimal("2"),
                     cost_basis=Decimal("380"), price=PRICES["NVDA"], value=Decimal("408.24"))
    )
    idea = _idea(ticker="VOO", sleeve="core", target_dollars=140.0, is_diversified_fund=True)
    assessment = run_risk_agent(_bundle_with_ideas([idea]), state, CFG, PRICES)
    assert not assessment.approved
    assert assessment.watch_items and "VOO" in assessment.watch_items[0]


def test_risk_hold_and_watch_never_become_tickets() -> None:
    assessment = run_risk_agent(
        _bundle_with_ideas([_idea(action="HOLD"), _idea(ticker="NVDA", action="WATCH")]),
        fixture_state(), CFG, PRICES,
    )
    assert not assessment.approved and len(assessment.watch_items) == 2


def test_risk_vetoes_sell_without_position() -> None:
    assessment = run_risk_agent(
        _bundle_with_ideas([_idea(action="SELL", ticker="NVDA")]), fixture_state(), CFG, PRICES
    )
    assert assessment.vetoed and "long-only" in assessment.vetoed[0].reason


def test_risk_caps_at_three_tickets_per_day() -> None:
    state = fixture_state(settled="2000")
    ideas = [
        _idea(ticker=t, sleeve=s, target_dollars=20.0, confidence=c)
        for t, s, c in [
            ("QQQM", "satellite_ai_semis", 0.9), ("GLDM", "diversifier", 0.8),
            ("NVDA", "satellite_ai_semis", 0.7), ("VOO", "core", 0.6),
        ]
    ]
    assessment = run_risk_agent(_bundle_with_ideas(ideas), state, CFG, PRICES)
    assert len(assessment.approved) == MAX_TICKETS_PER_DAY == 3
    assert any("daily ticket cap" in w for w in assessment.watch_items)
    approved_conf = [a.idea.confidence for a in assessment.approved]
    assert approved_conf == sorted(approved_conf, reverse=True)  # prioritized by confidence


def test_risk_trim_of_held_position_notes_t1_settlement() -> None:
    state = fixture_state()
    state.positions.append(
        PositionView(ticker="NVDA", sleeve="satellite_ai_semis", shares=Decimal("2"),
                     cost_basis=Decimal("380"), price=PRICES["NVDA"], value=Decimal("408.24"))
    )
    assessment = run_risk_agent(
        _bundle_with_ideas([_idea(action="TRIM", ticker="NVDA", target_dollars=204.12)]),
        state, CFG, PRICES,
    )
    assert len(assessment.approved) == 1
    a = assessment.approved[0]
    assert a.quantity == "1.0000" and not a.is_fractional
    assert any("T+1" in n for n in a.sizing_notes)
    assert a.cash_after == a.cash_before  # proceeds unsettled — cash unchanged today


def test_risk_trim_clamped_to_band_floor_never_overshoots() -> None:
    """Regression (2026-07-20 live run): an over-large TRIM must not push the sleeve
    below its band floor. Book: $1000 total, SMH sleeve at 28% ($280), floor 20%."""
    cash = CashState(settled=Decimal("400"), unsettled=Decimal(0))
    state = PortfolioState(
        as_of=date(2026, 7, 20),
        positions=[
            PositionView(ticker="SMH", sleeve="satellite_ai_semis", shares=Decimal("0.5"),
                         cost_basis=Decimal("250"), price=Decimal("560"), value=Decimal("280")),
            PositionView(ticker="VOO", sleeve="core", shares=Decimal("0.5"),
                         cost_basis=Decimal("300"), price=Decimal("640"), value=Decimal("320")),
        ],
        cash=cash, ledger=Ledger(cash=cash, gfv_events=[], open_lots=[]),
    )  # total 1000; satellite 28%, floor 20% → max trim = 280 - 200 = $80
    prices = {"SMH": Decimal("560")}
    # LLM asks to trim the near-entire $270 position — must clamp to $80
    assessment = run_risk_agent(
        _bundle_with_ideas([_idea(action="TRIM", ticker="SMH", target_dollars=270.0)]),
        state, CFG, prices,
    )
    assert len(assessment.approved) == 1
    a = assessment.approved[0]
    assert a.sized_dollars <= 80.0, "trim overshot the band floor"
    assert any("clamped" in n for n in a.sizing_notes)
    assert "OUT OF BAND" not in a.portfolio_impact  # lands in band now
    # and the portfolio_impact must NOT claim settled cash grew (proceeds are T+1)
    assert "unchanged" in a.portfolio_impact and "→ $480" not in a.portfolio_impact


def test_risk_trim_skipped_when_sleeve_already_at_floor() -> None:
    cash = CashState(settled=Decimal("850"), unsettled=Decimal(0))
    state = PortfolioState(
        as_of=date(2026, 7, 20),
        positions=[
            PositionView(ticker="SMH", sleeve="satellite_ai_semis", shares=Decimal("0.1"),
                         cost_basis=Decimal("100"), price=Decimal("560"), value=Decimal("56"))
        ],
        cash=cash, ledger=Ledger(cash=cash, gfv_events=[], open_lots=[]),
    )  # total ~906; satellite ~6%, already below the 20% floor
    assessment = run_risk_agent(
        _bundle_with_ideas([_idea(action="TRIM", ticker="SMH", target_dollars=30.0)]),
        state, CFG, {"SMH": Decimal("560")},
    )
    assert not assessment.approved
    assert any("already at/below" in w for w in assessment.watch_items)


def test_risk_full_sell_not_band_clamped() -> None:
    """A SELL (thesis exit) is NOT a rebalance and may take the sleeve to zero."""
    cash = CashState(settled=Decimal("400"), unsettled=Decimal(0))
    state = PortfolioState(
        as_of=date(2026, 7, 20),
        positions=[
            PositionView(ticker="SMH", sleeve="satellite_ai_semis", shares=Decimal("0.5"),
                         cost_basis=Decimal("250"), price=Decimal("560"), value=Decimal("280")),
            PositionView(ticker="VOO", sleeve="core", shares=Decimal("0.5"),
                         cost_basis=Decimal("300"), price=Decimal("640"), value=Decimal("320")),
        ],
        cash=cash, ledger=Ledger(cash=cash, gfv_events=[], open_lots=[]),
    )
    assessment = run_risk_agent(
        _bundle_with_ideas([_idea(action="SELL", ticker="SMH", target_dollars=280.0)]),
        state, CFG, {"SMH": Decimal("560")},
    )
    assert len(assessment.approved) == 1
    assert assessment.approved[0].sized_dollars == 280.0  # full exit, not clamped
