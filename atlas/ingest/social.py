"""Sentiment-only feeds: Reddit, StockTwits, YouTube (§7.0 rule 2).

Everything here is `untrusted`: sentiment/crowd-positioning signal ONLY, never
a factual basis. Highest injection risk in the system — every post is
quarantined and flagged posts never reach scoring (§1.1).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

from atlas.core.logging import get_logger
from atlas.core.quarantine import QuarantinedDocument, filter_scoring_eligible
from atlas.ingest.http import SourceUnavailable, fetch_document

log = get_logger("ingest.social")

TICKER_PATTERN = re.compile(r"\$([A-Z]{1,5})\b|\b(NVDA|AMD|INTC|MU|TSM|SPY|VOO|QQQ|SOXX|SMH|IBIT|GLD)\b")

BULLISH = frozenset("buy long calls moon rocket bullish undervalued accumulate dip adding".split())
BEARISH = frozenset("sell short puts crash bearish overvalued dump bagholder drilling tank".split())


class RedditClient(Protocol):
    """Duck-typed PRAW surface; tests inject a fake, runtime injects praw.Reddit."""

    def subreddit(self, name: str) -> Any: ...


@dataclass
class SocialPost:
    text: str
    source: str
    url: str
    score: int  # upvotes/likes — crowd weight, not truth weight
    doc: QuarantinedDocument = field(repr=False, default=None)  # type: ignore[assignment]


@dataclass
class SentimentReport:
    """Crowd positioning. Explicitly low-confidence; can NEVER put a number in the brief."""

    posts_analyzed: int = 0
    posts_excluded_flagged: int = 0
    ticker_mentions: dict[str, int] = field(default_factory=dict)
    bullish_ratio: float | None = None  # None = not enough signal
    noise_sources_discounted: list[str] = field(default_factory=list)
    flagged_docs: list[QuarantinedDocument] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)
    confidence: str = "low"  # structurally low: this is social media

    def summary(self) -> str:
        lean = "unknown"
        if self.bullish_ratio is not None:
            lean = "bullish" if self.bullish_ratio > 0.6 else "bearish" if self.bullish_ratio < 0.4 else "mixed"
        return (
            f"Crowd lean: {lean} (confidence: {self.confidence}; {self.posts_analyzed} posts; "
            f"{self.posts_excluded_flagged} excluded for injection flags). "
            "Sentiment only — no factual weight."
        )


def _post_to_doc(text: str, url: str, source: str) -> QuarantinedDocument:
    return QuarantinedDocument.wrap(
        source_url=url, trust_tier="untrusted", raw_text=text, source_name=source
    )


def fetch_reddit_posts(
    source_name: str, cfg: dict[str, Any], client: RedditClient, limit: int = 25
) -> list[SocialPost]:
    try:
        sub = client.subreddit(str(cfg["subreddit"]))
        posts = []
        for submission in sub.hot(limit=limit):
            text = f"{submission.title}\n{getattr(submission, 'selftext', '')}"[:4000]
            posts.append(
                SocialPost(
                    text=text,
                    source=source_name,
                    url=f"https://reddit.com{submission.permalink}",
                    score=int(getattr(submission, "score", 0)),
                    doc=_post_to_doc(text, f"https://reddit.com{submission.permalink}", source_name),
                )
            )
        return posts
    except Exception as exc:  # noqa: BLE001 — degrade, never block
        raise SourceUnavailable(source_name, f"reddit fetch failed: {exc}") from exc


def fetch_stocktwits_trending(cfg: dict[str, Any]) -> list[SocialPost]:
    doc = fetch_document(
        source_name="stocktwits_trending",
        url=str(cfg["url"]),
        trust_tier="untrusted",
        rate_limit=int(cfg.get("rate_limit", 10)),
        cache_ttl=int(cfg.get("cache_ttl", 3600)),
    )
    try:
        payload = json.loads(doc.raw_text)
        symbols = payload.get("symbols", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        raise SourceUnavailable("stocktwits_trending", f"bad payload: {exc}") from exc
    text = "trending: " + ", ".join(s.get("symbol", "?") for s in symbols)
    return [SocialPost(text=text, source="stocktwits_trending", url=str(cfg["url"]), score=0,
                       doc=_post_to_doc(text, str(cfg["url"]), "stocktwits_trending"))]


def analyze_sentiment(
    posts: list[SocialPost], noise_sources: frozenset[str] = frozenset({"reddit_wallstreetbets"})
) -> SentimentReport:
    """Sentiment scoring over quarantine-clean posts only (§1.1c)."""
    report = SentimentReport()
    docs = [p.doc for p in posts]
    clean_docs = set(id(d) for d in filter_scoring_eligible(docs))
    report.flagged_docs = [d for d in docs if id(d) not in clean_docs]
    report.posts_excluded_flagged = len(report.flagged_docs)

    mentions: Counter[str] = Counter()
    bull = bear = 0
    analyzed = 0
    for post in posts:
        if id(post.doc) not in clean_docs:
            continue
        analyzed += 1
        weight = 0.3 if post.source in noise_sources else 1.0
        if post.source in noise_sources and post.source not in report.noise_sources_discounted:
            report.noise_sources_discounted.append(post.source)
        for m in TICKER_PATTERN.finditer(post.text):
            mentions[m.group(1) or m.group(2)] += 1
        words = set(re.findall(r"[a-z]+", post.text.lower()))
        if words & BULLISH:
            bull += weight
        if words & BEARISH:
            bear += weight

    report.posts_analyzed = analyzed
    report.ticker_mentions = dict(mentions.most_common(15))
    total = bull + bear
    report.bullish_ratio = round(bull / total, 3) if total >= 3 else None
    return report
