"""Macro context: FRED series, Treasury yields, FOMC/CPI calendar (§7.1 primary)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from atlas.core.logging import get_logger
from atlas.ingest.http import SourceUnavailable, fetch_document

log = get_logger("ingest.macro")

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

FRED_SERIES = {
    "fed_funds_rate": "DFF",
    "cpi_yoy": "CPIAUCSL",
    "ten_year_yield": "DGS10",
    "two_year_yield": "DGS2",
    "unemployment": "UNRATE",
}

# Known upcoming macro events; refreshed by hand or future calendar source.
# Dated catalysts are surfaced in the brief's Calendar section (§8.6).
MACRO_CALENDAR_2026_H2: list[dict[str, str]] = [
    {"date": "2026-07-14", "event": "June CPI print"},
    {"date": "2026-07-28", "event": "FOMC meeting begins"},
    {"date": "2026-07-29", "event": "FOMC rate decision + press conference"},
    {"date": "2026-08-12", "event": "July CPI print"},
    {"date": "2026-09-15", "event": "FOMC meeting begins"},
    {"date": "2026-09-16", "event": "FOMC rate decision + press conference"},
    {"date": "2026-10-27", "event": "FOMC meeting begins"},
    {"date": "2026-10-28", "event": "FOMC rate decision + press conference"},
    {"date": "2026-12-08", "event": "FOMC meeting begins"},
    {"date": "2026-12-09", "event": "FOMC rate decision + press conference"},
]


@dataclass
class MacroSeries:
    name: str
    series_id: str
    value: float
    as_of: str  # observation date
    source_url: str
    fetched_at: datetime


@dataclass
class MacroContext:
    series: dict[str, MacroSeries] = field(default_factory=dict)
    upcoming_events: list[dict[str, str]] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    @property
    def yield_curve_spread(self) -> float | None:
        ten = self.series.get("ten_year_yield")
        two = self.series.get("two_year_yield")
        if ten and two:
            return round(ten.value - two.value, 3)
        return None


def fetch_fred_series(series_id: str, api_key: str, cfg: dict[str, Any]) -> MacroSeries:
    url = (
        f"{FRED_BASE}?series_id={series_id}&api_key={api_key}"
        f"&file_type=json&sort_order=desc&limit=1"
    )
    doc = fetch_document(
        source_name="fred",
        url=url,
        trust_tier="primary",
        rate_limit=int(cfg.get("rate_limit", 10)),
        cache_ttl=int(cfg.get("cache_ttl", 3600)),
    )
    try:
        payload = json.loads(doc.raw_text)
        obs = payload["observations"][0]
        return MacroSeries(
            name=series_id,
            series_id=series_id,
            value=float(obs["value"]),
            as_of=str(obs["date"]),
            source_url=f"https://fred.stlouisfed.org/series/{series_id}",
            fetched_at=doc.fetched_at,
        )
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
        raise SourceUnavailable("fred", f"bad payload for {series_id}: {exc}") from exc


def build_macro_context(
    api_key: str, cfg: dict[str, Any], today: date | None = None
) -> MacroContext:
    ctx = MacroContext()
    today = today or datetime.now(UTC).date()
    if not api_key:
        ctx.anomalies.append("fred: no FRED_API_KEY configured — macro series unavailable")
    else:
        for name, series_id in FRED_SERIES.items():
            try:
                series = fetch_fred_series(series_id, api_key, cfg)
                series.name = name
                ctx.series[name] = series
            except SourceUnavailable as exc:
                ctx.anomalies.append(str(exc))
    ctx.upcoming_events = [
        e for e in MACRO_CALENDAR_2026_H2
        if 0 <= (date.fromisoformat(e["date"]) - today).days <= 45
    ]
    return ctx
