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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from atlas.core.llm import LLMClient, build_llm_client
from atlas.core.logging import get_logger, run_dir, setup_logging
from atlas.core.settings import OperatorConfig, get_operator_config, get_settings, load_sources
from atlas.ingest.macro import MacroContext, build_macro_context
from atlas.ingest.market import MarketReader, MarketSnapshot, build_market_snapshot
from atlas.ingest.news import NewsDigest, fetch_news
from atlas.ingest.social import SentimentReport, SocialPost, analyze_sentiment
from atlas.portfolio.state import PortfolioState, load_paper_state
from atlas.research.agents.risk import run_risk_agent
from atlas.research.agents.runners import (
    run_macro_agent,
    run_market_agent,
    run_news_agent,
    run_sector_agent,
    run_sentiment_agent,
)
from atlas.research.agents.schemas import ResearchBundle, RiskAssessment

log = get_logger("research.orchestrator")

DEFAULT_WATCHLIST = ["SPY", "VOO", "NVDA", "SOXX", "SMH", "TSM", "GLDM", "XLV", "XLF", "IBIT"]


def _dump_stage(directory: Path, name: str, payload: Any) -> None:
    try:
        text = payload.model_dump_json(indent=1) if hasattr(payload, "model_dump_json") else json.dumps(payload, indent=1, default=str)
        (directory / f"{name}.json").write_text(text)
    except Exception as exc:  # noqa: BLE001 — debug logging must never kill the cycle
        log.warning(f"could not dump stage {name}: {exc}")


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
) -> ResearchBundle:
    """Run the five analysts in parallel and assemble the bundle."""
    with ThreadPoolExecutor(max_workers=5) as pool:
        f_market = pool.submit(run_market_agent, snapshot, llm)
        f_news = pool.submit(run_news_agent, digest, llm)
        f_sent = pool.submit(run_sentiment_agent, sentiment, llm)
        f_macro = pool.submit(run_macro_agent, macro, llm)
        f_sector = pool.submit(
            run_sector_agent, snapshot, digest, cfg.sleeves, holdings_summary, llm
        )
        bundle = ResearchBundle(
            market=f_market.result(),
            news=f_news.result(),
            sentiment=f_sent.result(),
            macro=f_macro.result(),
            sector=f_sector.result(),
        )
    for view in bundle.all_views():
        bundle.anomalies.extend(view.anomalies)
    if stage_dir is not None:
        _dump_stage(stage_dir, "bundle", bundle)
    return bundle


def _holdings_summary(state: PortfolioState) -> str:
    lines = [
        f"{p.ticker} ({p.sleeve}): {p.shares} sh, ${p.value} "
        f"({float(state.position_weight(p.ticker)):.1%} of book)"
        for p in state.positions
    ] or ["(no positions yet)"]
    lines.append(
        f"settled cash ${state.cash.settled}, unsettled ${state.cash.unsettled}, "
        f"total value ${state.total_value}"
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

    snapshot = build_market_snapshot(watchlist or DEFAULT_WATCHLIST, reader=mcp_reader)
    _dump_stage(stage_dir, "market_snapshot", snapshot)

    digest = fetch_news(sources)
    _dump_stage(stage_dir, "news_digest", {"items": len(digest.items), "anomalies": digest.anomalies})

    sentiment = analyze_sentiment(reddit_posts or [])
    _dump_stage(stage_dir, "sentiment_input", {"posts": sentiment.posts_analyzed})

    macro = build_macro_context(settings.fred_api_key, sources.get("fred", {}))
    _dump_stage(stage_dir, "macro_context", {"series": {k: v.value for k, v in macro.series.items()}})

    prices = {s: Decimal(str(q.price)) for s, q in snapshot.quotes.items()}
    state = load_paper_state(session, prices, datetime.now(UTC).date())

    bundle = build_research_bundle(
        llm=llm,
        snapshot=snapshot,
        digest=digest,
        sentiment=sentiment,
        macro=macro,
        cfg=cfg,
        holdings_summary=_holdings_summary(state),
        stage_dir=stage_dir,
    )
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


def run_scheduler() -> None:
    """APScheduler loop — wired in Phase 9."""
    from atlas.research.scheduler import start_scheduler

    start_scheduler()
