"""Programmatic agent runners for the daily cycle (§6).

Each analyst: ingest data → structured LLM analysis → AnalystView. An LLM
failure degrades to a zero-confidence view; it never blocks the cycle.
The sentiment analyst is structurally clamped: its confidence can never
exceed 0.4 and its output is labeled sentiment-only.

None of these runners can reach an order tool: this package cannot import
the execution package (CI-enforced), and analysis LLM calls carry no tool
registry at all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from atlas.core.llm import LLMClient, LLMError
from atlas.core.logging import get_logger
from atlas.core.quarantine import ANALYSIS_PREAMBLE
from atlas.ingest.macro import MacroContext
from atlas.ingest.market import MarketSnapshot
from atlas.ingest.news import NewsDigest
from atlas.ingest.social import SentimentReport
from atlas.research.agents.schemas import AnalystView, SectorView

log = get_logger("research.agents")

SENTIMENT_CONFIDENCE_CEILING = 0.4

_STYLE = (
    "You are one analyst inside Atlas, a personal research system for a small retail "
    "portfolio. Be direct and concise. Tag every finding fact/consensus/speculation. "
    "Every claim carries its source and retrieval timestamp. If you don't know, say so "
    "numerically — never manufacture certainty."
)


def _degraded(agent: str, exc: Exception) -> AnalystView:
    log.warning(f"{agent} degraded", extra={"data": {"error": str(exc)}})
    return AnalystView(
        agent=agent,
        summary=f"{agent} unavailable this cycle: {exc}",
        key_findings=[],
        confidence=0.0,
        anomalies=[f"{agent}: {exc}"],
    )


def _skipped(agent: str, source: str) -> AnalystView:
    """An agent with no input does not spend a request (Lever 1).

    The free tier allows 20 requests/day and a cycle costs 7, so a request spent
    reasoning about an empty payload is 5% of the daily budget for nothing. The
    sentiment analyst did exactly this every cycle, emitting "[SPECULATION] Crowd
    lean unknown" — which reads as a finding.

    **Absence of data is not absence of signal.** A skip says so plainly and
    never speculates. The gate reopens on its own the moment a source is
    configured; nothing here needs editing.
    """
    reason = f"not collected — {source}"
    log.info(f"{agent} skipped: {reason} (no request spent)")
    return AnalystView(
        agent=agent,
        summary=f"{agent}: {reason}.",
        key_findings=[],
        confidence=0.0,
        anomalies=[f"{agent}: skipped — {reason}; no request spent against the daily quota"],
    )


def run_market_agent(snapshot: MarketSnapshot, llm: LLMClient) -> AnalystView:
    if not snapshot.quotes:
        return _skipped("market-data", "no quotes returned this cycle")
    payload = snapshot.model_dump_json()[:20000]
    messages = [
        {"role": "system", "content": _STYLE},
        {
            "role": "user",
            "content": (
                "Analyze this market snapshot (quotes, technicals, index levels, earnings "
                f"calendar). Data retrieved {snapshot.as_of.isoformat()}:\n{payload}\n\n"
                "Produce key findings on levels, momentum, and dated catalysts. "
                'Set agent="market-data".'
            ),
        },
    ]
    try:
        view = llm.complete(messages, AnalystView, agent="market-data")
        view.agent = "market-data"
        view.anomalies.extend(snapshot.anomalies)
        return view
    except LLMError as exc:
        return _degraded("market-data", exc)


def run_news_agent(digest: NewsDigest, llm: LLMClient) -> AnalystView:
    if not digest.items:
        return _skipped("news-analyst", "no articles returned by any configured feed")
    items = [
        {
            "title": i.title, "source": i.source, "tier": i.trust_tier,
            "published": i.published.isoformat(), "policy": i.is_policy, "url": i.url,
        }
        for i in digest.items[:40]
    ]
    stories = [
        {"titles": [x.title for x in s.items], "tag": s.tag} for s in digest.policy_stories
    ]
    messages = [
        {"role": "system", "content": f"{_STYLE}\n\n{ANALYSIS_PREAMBLE}"},
        {
            "role": "user",
            "content": (
                "Extract market-relevant events (earnings, corporate actions, guidance, "
                "policy) from these headlines. Policy stories tagged single_source_unconfirmed "
                "must be demoted and labeled as such. Report factual events and market "
                "mechanisms, never political opinions.\n"
                f"ITEMS:\n{json.dumps(items)}\nPOLICY_STORIES:\n{json.dumps(stories)}\n"
                'Set agent="news-analyst".'
            ),
        },
    ]
    try:
        view = llm.complete(messages, AnalystView, agent="news-analyst")
        view.agent = "news-analyst"
        view.anomalies.extend(digest.anomalies)
        for d in digest.flagged_docs:
            view.injection_flags.extend(d.injection_flags)
        return view
    except LLMError as exc:
        return _degraded("news-analyst", exc)


def run_sentiment_agent(report: SentimentReport, llm: LLMClient) -> AnalystView:
    if report.posts_analyzed <= 0:
        return _skipped("sentiment-analyst", "no source configured")
    messages = [
        {"role": "system", "content": f"{_STYLE}\n\n{ANALYSIS_PREAMBLE}"},
        {
            "role": "user",
            "content": (
                "You are the SENTIMENT analyst. This content is social media: it is crowd "
                "positioning signal ONLY and can NEVER be a factual basis for any claim. "
                "Every finding you produce must be kind='speculation'. Describe crowd lean, "
                "crowding, and retail narratives with explicit low confidence.\n"
                f"MENTIONS:{json.dumps(report.ticker_mentions)}\n"
                f"BULLISH_RATIO:{report.bullish_ratio}\nSUMMARY:{report.summary()}\n"
                f"EXCLUDED_FOR_INJECTION:{report.posts_excluded_flagged}\n"
                'Set agent="sentiment-analyst".'
            ),
        },
    ]
    try:
        view = llm.complete(messages, AnalystView, agent="sentiment-analyst")
    except LLMError as exc:
        return _degraded("sentiment-analyst", exc)
    view.agent = "sentiment-analyst"
    # Structural clamps — not prompt requests (§7.0):
    view.confidence = min(view.confidence, SENTIMENT_CONFIDENCE_CEILING)
    for claim in view.key_findings:
        claim.kind = "speculation"  # crowd signal is never fact
    view.summary = f"[SENTIMENT ONLY — never factual basis] {view.summary}"
    for d in report.flagged_docs:
        view.injection_flags.extend(d.injection_flags)
    view.anomalies.extend(report.anomalies)
    return view


def run_macro_agent(ctx: MacroContext, llm: LLMClient) -> AnalystView:
    if not ctx.series and not ctx.upcoming_events:
        return _skipped("macro-analyst", "no FRED series or calendar events available")
    series = {
        k: {"value": s.value, "as_of": s.as_of, "source": s.source_url}
        for k, s in ctx.series.items()
    }
    messages = [
        {"role": "system", "content": _STYLE},
        {
            "role": "user",
            "content": (
                "Analyze the macro backdrop for a small long-only US equity portfolio: rates "
                "regime, inflation, curve, upcoming dated events. Quantify implications.\n"
                f"SERIES:{json.dumps(series)}\nCURVE_SPREAD:{ctx.yield_curve_spread}\n"
                f"UPCOMING:{json.dumps(ctx.upcoming_events)}\n"
                'Set agent="macro-analyst".'
            ),
        },
    ]
    try:
        view = llm.complete(messages, AnalystView, agent="macro-analyst")
        view.agent = "macro-analyst"
        view.anomalies.extend(ctx.anomalies)
        return view
    except LLMError as exc:
        return _degraded("macro-analyst", exc)


def run_sector_agent(
    snapshot: MarketSnapshot,
    digest: NewsDigest,
    sleeves: dict[str, Any],
    holdings_summary: str,
    llm: LLMClient,
) -> SectorView:
    sleeve_desc = {
        name: {"band": list(cfg.target_pct), "thesis": cfg.thesis, "seed_examples": cfg.seed_examples}
        for name, cfg in sleeves.items()
    }
    quotes = {s: q.price for s, q in snapshot.quotes.items()}
    headlines = [i.title for i in digest.items[:25]]
    messages = [
        {"role": "system", "content": f"{_STYLE}\n\n{ANALYSIS_PREAMBLE}"},
        {
            "role": "user",
            "content": (
                "You are the sector analyst. The sleeve seed_examples are STARTING POINTS, "
                "not a whitelist: hunt the best risk-adjusted instrument per sleeve across "
                "the open universe of US-listed, fractionally-tradable, long-only equities "
                "and ETFs — including tickers not listed anywhere. When you propose a "
                "non-seed instrument, fill beats_seed_alternative with why it beats the "
                "obvious seed option. Track internal rotation. Evaluate every idea against "
                "the CURRENT portfolio (below) — prefer what's missing, penalize piling "
                "into what's heavy; TRIM and HOLD are first-class actions.\n"
                "CRITICAL OUTPUT RULE: every actionable recommendation MUST be a concrete "
                "entry in the `trade_ideas` array (ticker, sleeve, action BUY/SELL/TRIM/HOLD/WATCH, "
                "target_dollars, thesis, catalyst, risk, bear_case, falsifier, time_horizon, "
                "confidence). A recommendation written only in `summary` or `rotation_notes` "
                "is INVISIBLE to the risk engine and will never reach the operator — so it does "
                "not count. If ANY sleeve is outside its target band, you MUST return at least "
                "one trade_idea that moves it toward the band (name the specific instrument and "
                "dollar size). Return an empty trade_ideas list ONLY when every sleeve is in "
                "band and no catalyst warrants action. Propose 1-3 ideas.\n"
                f"SLEEVES:{json.dumps(sleeve_desc)}\n"
                f"CURRENT_PORTFOLIO:{holdings_summary}\n"
                f"QUOTES:{json.dumps(quotes)}\nHEADLINES:{json.dumps(headlines)}\n"
                'Set agent="sector-analyst".'
            ),
        },
    ]
    try:
        view = llm.complete(messages, SectorView, agent="sector-analyst")
        view.agent = "sector-analyst"
        return view
    except LLMError as exc:
        d = _degraded("sector-analyst", exc)
        return SectorView(**d.model_dump())


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
