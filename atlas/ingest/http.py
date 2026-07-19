"""Shared fetch machinery for all ingest modules (§7.3).

Every outbound request: rate-limited per source, cached to disk with per-source
TTL, robots.txt-respecting for HTML scrapes, descriptive User-Agent, graceful
degradation (SourceUnavailable — never retry-hammer). Every fetched body comes
back wrapped in a QuarantinedDocument.
"""

from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse

import httpx

from atlas.core.logging import get_logger
from atlas.core.quarantine import QuarantinedDocument, TrustTierName
from atlas.core.settings import REPO_ROOT

log = get_logger("ingest.http")

USER_AGENT = (
    "atlas-research/0.1 (personal single-user research agent; contact: operator; "
    "respects robots.txt and rate limits)"
)
CACHE_DIR = REPO_ROOT / ".cache" / "ingest"


class SourceUnavailable(RuntimeError):
    """A source failed or refused; callers degrade gracefully and log an anomaly."""

    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


@dataclass
class RateLimiter:
    """Simple token bucket: `rate` requests per 60s window."""

    rate: int
    _stamps: list[float] = field(default_factory=list)

    def acquire(self, now: float | None = None, sleep: object = time.sleep) -> None:
        now = now if now is not None else time.monotonic()
        self._stamps = [s for s in self._stamps if now - s < 60]
        if len(self._stamps) >= self.rate:
            wait = 60 - (now - self._stamps[0])
            log.info("rate limit wait", extra={"data": {"seconds": round(wait, 1)}})
            sleep(wait)  # type: ignore[operator]
        self._stamps.append(now if now is not None else time.monotonic())


_limiters: dict[str, RateLimiter] = {}
_robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}


def _limiter_for(source: str, rate: int) -> RateLimiter:
    if source not in _limiters:
        _limiters[source] = RateLimiter(rate)
    return _limiters[source]


def _cache_path(url: str) -> Path:
    return CACHE_DIR / f"{sha256(url.encode()).hexdigest()}.body"


def _cache_get(url: str, ttl: int) -> str | None:
    p = _cache_path(url)
    if p.exists() and (time.time() - p.stat().st_mtime) < ttl:
        return p.read_text()
    return None


def _cache_put(url: str, body: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(body)


def robots_allows(url: str, client: httpx.Client | None = None) -> bool:
    host = urlparse(url).scheme + "://" + urlparse(url).netloc
    if host not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            c = client or httpx.Client(timeout=10, headers={"User-Agent": USER_AGENT})
            resp = c.get(host + "/robots.txt")
            rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except httpx.HTTPError:
            rp.parse([])  # unreachable robots.txt → assume allowed, stay polite via rate limits
        _robots_cache[host] = rp
    return _robots_cache[host].can_fetch(USER_AGENT, url)


def fetch_document(
    *,
    source_name: str,
    url: str,
    trust_tier: TrustTierName,
    rate_limit: int = 10,
    cache_ttl: int = 3600,
    check_robots: bool = False,
    client: httpx.Client | None = None,
) -> QuarantinedDocument:
    """Fetch one URL under the source's discipline; return it quarantined."""
    cached = _cache_get(url, cache_ttl)
    if cached is not None:
        return QuarantinedDocument.wrap(
            source_url=url, trust_tier=trust_tier, raw_text=cached, source_name=source_name
        )

    if check_robots and not robots_allows(url, client):
        raise SourceUnavailable(source_name, f"robots.txt disallows {url}")

    _limiter_for(source_name, rate_limit).acquire()
    c = client or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    try:
        resp = c.get(url)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(source_name, f"network error: {exc}") from exc
    if resp.status_code != 200:
        raise SourceUnavailable(source_name, f"HTTP {resp.status_code} for {url}")
    _cache_put(url, resp.text)
    return QuarantinedDocument.wrap(
        source_url=url, trust_tier=trust_tier, raw_text=resp.text, source_name=source_name
    )


def reset_for_tests() -> None:
    _limiters.clear()
    _robots_cache.clear()
