"""News ingestion: RSS/API fetchers for primary + secondary sources (§7).

Includes the political-balance rule (§7.2): a policy story needs ≥2 outlets of
differing editorial lean, or it is tagged `single_source_unconfirmed` and
demoted in the brief.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import feedparser

from atlas.core.logging import get_logger
from atlas.core.quarantine import QuarantinedDocument
from atlas.ingest.http import SourceUnavailable, fetch_document

log = get_logger("ingest.news")

POLICY_KEYWORDS = re.compile(
    r"\b(tariff|congress|senate|house\s+bill|white\s+house|president|regulat|sanction|"
    r"appoint|nominee|executive\s+order|legislation|federal\s+agency|antitrust|subsid)\w*\b",
    re.I,
)
_STOPWORDS = frozenset(
    "a an and are as at be by for from has he in is it its of on that the to was were will with "
    "this after over under new says said".split()
)


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    published: datetime
    source: str
    trust_tier: str
    editorial_lean: str | None = None
    is_policy: bool = False
    summary: str = ""
    doc: QuarantinedDocument | None = None


@dataclass
class PolicyStory:
    """One policy event, possibly covered by several outlets (§7.2)."""

    items: list[NewsItem]

    @property
    def leans(self) -> set[str]:
        return {i.editorial_lean for i in self.items if i.editorial_lean}

    @property
    def confirmed(self) -> bool:
        return len(self.leans) >= 2

    @property
    def tag(self) -> str:
        return "multi_lean_confirmed" if self.confirmed else "single_source_unconfirmed"


@dataclass
class NewsDigest:
    items: list[NewsItem] = field(default_factory=list)
    policy_stories: list[PolicyStory] = field(default_factory=list)
    flagged_docs: list[QuarantinedDocument] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)


def _parse_feed(doc: QuarantinedDocument, source: str, cfg: dict[str, Any]) -> list[NewsItem]:
    parsed = feedparser.parse(doc.raw_text)
    items: list[NewsItem] = []
    for entry in parsed.entries[:30]:
        title = getattr(entry, "title", "").strip()
        if not title:
            continue
        published = datetime.now(UTC)
        if getattr(entry, "published_parsed", None):
            published = datetime(*entry.published_parsed[:6], tzinfo=UTC)
        summary = getattr(entry, "summary", "")[:500]
        items.append(
            NewsItem(
                title=title,
                url=getattr(entry, "link", doc.source_url),
                published=published,
                source=source,
                trust_tier=str(cfg.get("trust_tier", "secondary")),
                editorial_lean=cfg.get("editorial_lean"),
                is_policy=bool(POLICY_KEYWORDS.search(title + " " + summary)),
                summary=summary,
                doc=doc,
            )
        )
    return items


def _title_tokens(title: str) -> frozenset[str]:
    return frozenset(
        w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in _STOPWORDS and len(w) > 2
    )


def build_policy_stories(items: list[NewsItem], overlap: float = 0.35) -> list[PolicyStory]:
    """Cluster policy items by token overlap; the two-lean rule applies per cluster."""
    policy = [i for i in items if i.is_policy]
    stories: list[list[NewsItem]] = []
    for item in policy:
        toks = _title_tokens(item.title)
        for cluster in stories:
            ref = _title_tokens(cluster[0].title)
            union = toks | ref
            if union and len(toks & ref) / len(union) >= overlap:
                cluster.append(item)
                break
        else:
            stories.append([item])
    return [PolicyStory(items=c) for c in stories]


def fetch_news(sources: dict[str, dict[str, Any]]) -> NewsDigest:
    """Fetch every enabled rss/html news source; degrade per source, never block."""
    digest = NewsDigest()
    for name, cfg in sources.items():
        if not cfg.get("enabled") or cfg.get("kind") not in ("rss", "html"):
            continue
        try:
            doc = fetch_document(
                source_name=name,
                url=cfg["url"],
                trust_tier=cfg.get("trust_tier", "secondary"),
                rate_limit=int(cfg.get("rate_limit", 10)),
                cache_ttl=int(cfg.get("cache_ttl", 86400)),
            )
        except SourceUnavailable as exc:
            digest.anomalies.append(str(exc))
            continue
        if doc.flagged:
            digest.flagged_docs.append(doc)
            digest.anomalies.append(f"{name}: injection flags {doc.injection_flags} — excluded")
            continue
        digest.items.extend(_parse_feed(doc, name, cfg))
    digest.policy_stories = build_policy_stories(digest.items)
    digest.items.sort(key=lambda i: i.published, reverse=True)
    return digest
