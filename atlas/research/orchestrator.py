"""The daily research cycle (§6 orchestration pattern).

market-data / news / sentiment / macro / sector run in parallel (staggered
under the shared LLM rate cap) → ResearchBundle → portfolio-risk on the bundle
+ live positions → synthesis → critic → persist → notify. Every stage's raw
output lands in runs/<date>/ for debugging.

This module can never place an order: importing the execution package from
research is a CI-failing violation — the cycle terminates at PENDING_APPROVAL
tickets.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from atlas.core.budget import CyclePlan, budget_status, plan_cycle, preflight
from atlas.core.format import money, shares
from atlas.core.llm import LLMClient, build_llm_client
from atlas.core.logging import get_logger, run_dir, setup_logging
from atlas.core.settings import OperatorConfig, get_operator_config, get_settings, load_sources
from atlas.ingest.macro import MacroContext, build_macro_context
from atlas.ingest.market import MarketReader, MarketSnapshot, build_market_snapshot
from atlas.ingest.news import NewsDigest, fetch_news
from atlas.ingest.social import SentimentReport, SocialPost, analyze_sentiment
from atlas.portfolio.state import PortfolioState, load_portfolio_state
from atlas.research.agents.risk import run_risk_agent
from atlas.research.agents.runners import (
    run_macro_agent,
    run_market_agent,
    run_news_agent,
    run_sector_agent,
    run_sentiment_agent,
)
from atlas.research.agents.schemas import AnalystView, ResearchBundle, RiskAssessment

log = get_logger("research.orchestrator")

# The benchmark every window is measured against; always analysed, held or not.
BENCHMARK = "SPY"

# Market context for §2's tape/rates/vol/commodities/crypto recap. These are
# benchmarks, NOT candidate positions — SOXX in particular is the semis yardstick
# the AI/semis sleeve is measured against, never something to propose buying.
BENCHMARKS = (BENCHMARK, "QQQ", "DIA", "SOXX", "^VIX", "^TNX", "GLD", "BTC-USD")


class HoldingsCoverageError(RuntimeError):
    """A held position has no usable quote, so §2 cannot be honoured.

    "Every holding, individually" is a contract. A brief that silently omits a
    position the operator owns is worse than no brief.
    """


class Watchlist(NamedTuple):
    """Two tiers, because they cost differently.

    `analysis` gets quotes *and* indicators (a 1y history fetch per name) and
    must contain every holding — v2 §2 reports on each one individually.
    `reference` gets quotes only (5d), which is all the sector analyst needs to
    weigh a candidate it does not yet own.
    """

    analysis: list[str]
    reference: list[str]


def build_watchlist(
    held: Iterable[str], cfg: OperatorConfig, *, benchmark: str = BENCHMARK
) -> Watchlist:
    """Derive coverage from the book, never a hardcoded list.

    The old `DEFAULT_WATCHLIST` was ten fixed tickers; holdings outside it were
    topped up by a second snapshot whose indicators were then discarded, so
    seven of twelve holdings had no technicals. Seeds stay reference-tier: §2.0
    makes them starting points, not a whitelist, so they must not crowd out the
    book's own coverage.
    """
    analysis: list[str] = []
    for ticker in [*held, *cfg.watchlist, benchmark]:
        symbol = ticker.strip().upper()
        if symbol and symbol not in analysis:
            analysis.append(symbol)

    reference: list[str] = []
    for ticker in BENCHMARKS:
        symbol = ticker.strip().upper()
        if symbol and symbol not in analysis and symbol not in reference:
            reference.append(symbol)

    return Watchlist(analysis=analysis, reference=reference)


def assert_holdings_quoted(held: Iterable[str], snapshot: MarketSnapshot) -> None:
    """Hard gate: every holding must carry a usable quote or nothing renders.

    A zero price counts as unquoted — that is a data failure, not a $0 asset
    (the BRK.B/`BRK-B` symbol mismatch produced exactly that). A wholly empty
    snapshot is reported as a fetch outage rather than as N individually dead
    symbols, because the remedy is completely different.
    """
    holdings = [t.strip().upper() for t in held if t.strip()]
    if not holdings:
        return

    missing = [
        t for t in holdings
        if t not in snapshot.quotes or snapshot.quotes[t].price <= 0
    ]
    if not missing:
        return

    if not snapshot.quotes:
        raise HoldingsCoverageError(
            "total quote-fetch failure: the market snapshot returned no quotes at "
            "all, so no holding can be priced. This is an upstream outage, not "
            f"{len(missing)} dead symbols — the cycle is aborted rather than "
            "rendering a brief with no portfolio data."
        )
    raise HoldingsCoverageError(
        f"no usable quote for held position(s): {', '.join(sorted(missing))}. "
        "§2 reports every holding individually, so the brief will not render "
        "while a position the operator owns is unpriced."
    )


def _dump_stage(directory: Path, name: str, payload: Any) -> None:
    try:
        text = payload.model_dump_json(indent=1) if hasattr(payload, "model_dump_json") else json.dumps(payload, indent=1, default=str)
        (directory / f"{name}.json").write_text(text)
    except Exception as exc:  # noqa: BLE001 — debug logging must never kill the cycle
        log.warning(f"could not dump stage {name}: {exc}")



def _budget_dropped(agent: str) -> AnalystView:
    """An agent the daily budget could not afford. Distinct from a skip (no
    input) and from a degradation (the call failed) — the cause matters to the
    reader, so §9 states which of the three happened."""
    log.warning(f"{agent} dropped: daily request budget too tight")
    return AnalystView(
        agent=agent,
        summary=f"{agent}: not run — daily request budget too tight this cycle.",
        key_findings=[],
        confidence=0.0,
        anomalies=[
            f"{agent}: dropped — daily request budget too tight to run it this cycle "
            "(the critic's slot is reserved and never dropped)"
        ],
    )


def build_research_bundle(
    *,
    llm: LLMClient,
    snapshot: MarketSnapshot,
    digest: NewsDigest,
    sentiment: SentimentReport,
    macro: MacroContext,
    cfg: OperatorConfig,
    holdings_summary: str,
    stage_dir: Path | None = None,
    plan: CyclePlan | None = None,
) -> ResearchBundle:
    """Run the analysts in parallel and assemble the bundle.

    `plan` (Lever 3) names which agents the daily request budget can afford. A
    dropped agent is never called and renders as budget-dropped rather than
    silently missing.
    """
    allowed = set(plan.to_run) if plan is not None else None

    def _run(agent: str, fn: Any, *args: Any) -> Any:
        if allowed is not None and agent not in allowed:
            return _budget_dropped(agent)
        return fn(*args)

    with ThreadPoolExecutor(max_workers=5) as pool:
        f_market = pool.submit(_run, "market-data", run_market_agent, snapshot, llm)
        f_news = pool.submit(_run, "news-analyst", run_news_agent, digest, llm)
        f_sent = pool.submit(_run, "sentiment-analyst", run_sentiment_agent, sentiment, llm)
        f_macro = pool.submit(_run, "macro-analyst", run_macro_agent, macro, llm)
        f_sector = pool.submit(
            _run, "sector-analyst", run_sector_agent,
            snapshot, digest, cfg.sleeves, holdings_summary, llm
        )
        bundle = ResearchBundle(
            market=f_market.result(),
            news=f_news.result(),
            sentiment=f_sent.result(),
            macro=f_macro.result(),
            sector=f_sector.result(),
        )
    # Retrieval timestamps are a fact Atlas knows, never the LLM's to invent.
    # Models otherwise hallucinate training-era dates (e.g. 2023) onto synthesized
    # findings. Stamp every claim with the real time this cycle retrieved its data.
    stamp = snapshot.as_of.isoformat()
    for view in bundle.all_views():
        for claim in view.key_findings:
            claim.retrieved_at = stamp

    for view in bundle.all_views():
        bundle.anomalies.extend(view.anomalies)
    if stage_dir is not None:
        _dump_stage(stage_dir, "bundle", bundle)
    return bundle


def _holdings_summary(state: PortfolioState) -> str:
    lines = [
        f"{p.ticker} ({p.sleeve}): {shares(p.shares)} sh, {money(p.value)} "
        f"({float(state.position_weight(p.ticker)):.1%} of book)"
        for p in state.positions
    ] or ["(no positions yet)"]
    lines.append(
        f"settled cash {money(state.cash.settled)}, unsettled {money(state.cash.unsettled)}, "
        f"total value {money(state.total_value)}"
    )
    return "\n".join(lines)


@dataclass
class CycleData:
    bundle: ResearchBundle
    assessment: RiskAssessment
    state: PortfolioState
    snapshot: MarketSnapshot
    macro: MacroContext
    llm: LLMClient


def run_cycle(
    *,
    session: Session,
    llm: LLMClient | None = None,
    mcp_reader: MarketReader | None = None,
    reddit_posts: list[SocialPost] | None = None,
    watchlist: list[str] | None = None,
) -> CycleData:
    """Ingest → analysts → risk. Synthesis/critic run downstream (synthesis.py)."""
    setup_logging()
    settings = get_settings()
    cfg = get_operator_config()
    sources = load_sources()
    stage_dir = run_dir()
    llm = llm or build_llm_client(settings)
    # The brief's date is the operator's local trading day (§12: America/Los_Angeles
    # throughout) — not UTC, which rolls a day ahead every evening Pacific.
    today = datetime.now(ZoneInfo(settings.timezone)).date()

    # Coverage is derived from the book, so no holding can be left without
    # technicals. Probing state with empty prices is safe: we only need the
    # ticker list, and valuation happens below once quotes exist.
    state_probe = load_portfolio_state(session, {}, today)
    wl = build_watchlist(
        watchlist or [p.ticker for p in state_probe.positions], cfg
    )
    snapshot = build_market_snapshot(
        wl.analysis, reader=mcp_reader, index_symbols=tuple(wl.reference)
    )
    # Dumped only once the snapshot is final — dumping mid-assembly previously
    # under-reported coverage and misled diagnosis.
    _dump_stage(stage_dir, "market_snapshot", snapshot)

    digest = fetch_news(sources)
    _dump_stage(stage_dir, "news_digest", {"items": len(digest.items), "anomalies": digest.anomalies})

    sentiment = analyze_sentiment(reddit_posts or [])
    _dump_stage(stage_dir, "sentiment_input", {"posts": sentiment.posts_analyzed})

    macro = build_macro_context(settings.fred_api_key, sources.get("fred", {}))
    _dump_stage(stage_dir, "macro_context", {"series": {k: v.value for k, v in macro.series.items()}})

    # Hard gate before anything downstream consumes the snapshot: §2 reports on
    # every holding, so an unpriced position aborts rather than vanishing.
    assert_holdings_quoted([p.ticker for p in state_probe.positions], snapshot)

    # Budget gate (Levers 2/3). Every input above is free — no LLM request has
    # been spent yet — so this is the last honest moment to refuse. An agent whose
    # input is empty is already gated and costs nothing, so it is excluded from
    # the count rather than reserved for.
    candidates = [
        agent for agent, has_input in (
            ("market-data", bool(snapshot.quotes)),
            ("news-analyst", bool(digest.items)),
            ("sentiment-analyst", sentiment.posts_analyzed > 0),
            ("macro-analyst", bool(macro.series or macro.upcoming_events)),
            ("sector-analyst", True),
        ) if has_input
    ]
    usage_path = stage_dir / "usage.json"
    # +1 for the synthesizer; the critic's slot is added inside preflight so it
    # can never be the request that gets dropped.
    preflight(len(candidates) + 1, usage_path=usage_path)
    status = budget_status(usage_path)
    plan = plan_cycle(candidates, available=max(0, status.available_for_agents - 1))
    if plan.dropped:
        log.warning(f"budget dropped agent(s): {', '.join(plan.dropped)}")

    prices = {s: Decimal(str(q.price)) for s, q in snapshot.quotes.items()}
    state = load_portfolio_state(session, prices, today)

    bundle = build_research_bundle(
        llm=llm,
        snapshot=snapshot,
        digest=digest,
        sentiment=sentiment,
        macro=macro,
        cfg=cfg,
        holdings_summary=_holdings_summary(state),
        stage_dir=stage_dir,
        plan=plan,
    )
    bundle.anomalies.extend(plan.anomalies)
    assessment = run_risk_agent(bundle, state, cfg, prices)
    _dump_stage(stage_dir, "risk_assessment", assessment)
    return CycleData(
        bundle=bundle, assessment=assessment, state=state,
        snapshot=snapshot, macro=macro, llm=llm,
    )


def run_daily_cycle(kind: str = "preopen") -> None:
    """CLI entrypoint: full cycle → synthesis → critic → persisted brief + tickets."""
    from atlas.core.db import session_scope
    from atlas.research.synthesis import synthesize_and_persist

    with session_scope() as session:
        data = run_cycle(session=session)
        synthesize_and_persist(
            session, data.bundle, data.assessment, data.state,
            snapshot=data.snapshot, macro=data.macro, llm=data.llm, kind=kind,
        )


