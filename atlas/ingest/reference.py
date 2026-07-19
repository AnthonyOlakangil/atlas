"""Reference corpus: Investopedia + Robinhood Learn → glossary (§7 reference).

Powers the teaching layer: when a brief uses a term the operator hasn't seen,
a one-line definition is injected inline and recorded in GlossaryTerm.
Built once, cached 7 days, refreshed monthly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from selectolax.parser import HTMLParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.logging import get_logger
from atlas.core.models import GlossaryTerm
from atlas.ingest.http import SourceUnavailable, fetch_document

log = get_logger("ingest.reference")

# Seed glossary: term → (source_url, fallback one-liner used if fetch fails).
# The fallback keeps the teaching layer working offline; fetched copy replaces it.
GLOSSARY_SEED: dict[str, tuple[str, str]] = {
    "limit order": (
        "https://www.investopedia.com/terms/l/limitorder.asp",
        "An order to buy/sell only at a specified price or better — you control price, not fill certainty.",
    ),
    "market order": (
        "https://www.investopedia.com/terms/m/marketorder.asp",
        "An order that executes immediately at the best available price — you control fill, not price.",
    ),
    "stop-limit order": (
        "https://www.investopedia.com/terms/s/stop-limitorder.asp",
        "A stop price triggers a limit order — protective exit with a price floor, but it can miss fast moves.",
    ),
    "settlement (T+1)": (
        "https://www.investopedia.com/terms/t/tplus1.asp",
        "Trades finalize the next business day; sale proceeds aren't spendable until then in a cash account.",
    ),
    "good faith violation": (
        "https://learn.robinhood.com/articles/good-faith-violations/",
        "Buying with unsettled funds and selling before they settle; three in 12 months restricts the account.",
    ),
    "expense ratio": (
        "https://www.investopedia.com/terms/e/expenseratio.asp",
        "The annual fund fee as a % of assets — 0.19% means $1.90/year per $1,000 invested.",
    ),
    "RSI": (
        "https://www.investopedia.com/terms/r/rsi.asp",
        "Relative Strength Index (0-100): momentum gauge; >70 often read as overbought, <30 oversold.",
    ),
    "MACD": (
        "https://www.investopedia.com/terms/m/macd.asp",
        "Moving Average Convergence Divergence: trend/momentum signal from two EMAs and a signal line.",
    ),
    "drawdown": (
        "https://www.investopedia.com/terms/d/drawdown.asp",
        "The peak-to-trough decline of an investment — how much pain you'd have felt buying the top.",
    ),
    "alpha": (
        "https://www.investopedia.com/terms/a/alpha.asp",
        "Return above the benchmark — Atlas's alpha is its return minus SPY's over the same window.",
    ),
    "fractional shares": (
        "https://learn.robinhood.com/articles/fractional-shares/",
        "Owning a slice of one share (e.g. 0.25 of VOO); on Robinhood these must be market orders.",
    ),
    "core-satellite": (
        "https://www.investopedia.com/terms/c/coresatellite.asp",
        "Portfolio structure: a broad low-cost core plus smaller higher-conviction satellite positions.",
    ),
    "sector concentration": (
        "https://www.investopedia.com/terms/c/concentrationrisk.asp",
        "Too much exposure to one theme — two different tickers can still be the same bet.",
    ),
    "P/E ratio": (
        "https://www.investopedia.com/terms/p/price-earningsratio.asp",
        "Price divided by earnings per share — how many dollars you pay per dollar of annual profit.",
    ),
    "ETF": (
        "https://www.investopedia.com/terms/e/etf.asp",
        "Exchange-traded fund: a basket of securities that trades like a single stock.",
    ),
}


@dataclass
class ReferenceBuildResult:
    fetched: int = 0
    fallback: int = 0
    anomalies: list[str] | None = None


def _extract_definition(html: str) -> str | None:
    tree = HTMLParser(html)
    for selector in ("meta[name=description]", "meta[property='og:description']"):
        node = tree.css_first(selector)
        if node and node.attributes.get("content"):
            return str(node.attributes["content"])[:300]
    p = tree.css_first("article p, main p, p")
    if p and len(p.text(strip=True)) > 40:
        return p.text(strip=True)[:300]
    return None


def build_reference_corpus(session: Session, cfg: dict[str, Any] | None = None) -> ReferenceBuildResult:
    """Idempotent: upserts glossary terms; live fetch preferred, fallback kept offline."""
    cfg = cfg or {}
    result = ReferenceBuildResult(anomalies=[])
    existing = {t.term for t in session.scalars(select(GlossaryTerm))}
    for term, (url, fallback) in GLOSSARY_SEED.items():
        definition, fetched = fallback, False
        try:
            doc = fetch_document(
                source_name="reference",
                url=url,
                trust_tier="primary",
                rate_limit=int(cfg.get("rate_limit", 5)),
                cache_ttl=int(cfg.get("cache_ttl", 604800)),
                check_robots=True,
            )
            if not doc.flagged:
                extracted = _extract_definition(doc.raw_text)
                if extracted:
                    definition, fetched = extracted, True
        except SourceUnavailable as exc:
            assert result.anomalies is not None
            result.anomalies.append(str(exc))
        if fetched:
            result.fetched += 1
        else:
            result.fallback += 1
        if term not in existing:
            session.add(GlossaryTerm(term=term, definition=definition, source_url=url))
        else:
            row = session.scalars(select(GlossaryTerm).where(GlossaryTerm.term == term)).one()
            if fetched:
                row.definition = definition
                row.source_url = url
    session.flush()
    return result


def lookup_definition(session: Session, term: str) -> str | None:
    row = session.scalars(
        select(GlossaryTerm).where(GlossaryTerm.term == term.lower())
    ).first() or session.scalars(select(GlossaryTerm).where(GlossaryTerm.term == term)).first()
    return row.definition if row else None
