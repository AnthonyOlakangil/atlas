"""Market Pulse (§9 view 4): index levels, 10Y, VIX, gold, oil, BTC."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from atlas.api.deps import QuoteFn, get_quotes

router = APIRouter(tags=["market"])

PULSE_SYMBOLS: list[tuple[str, str]] = [
    ("SPY", "S&P 500 ETF"),
    ("QQQ", "Nasdaq-100 ETF"),
    ("DIA", "Dow ETF"),
    ("SOXX", "Semiconductors ETF"),
    ("^TNX", "10-Year Treasury Yield (×10)"),
    ("^VIX", "VIX (fear gauge)"),
    ("GLD", "Gold ETF"),
    ("CL=F", "WTI Crude Oil"),
    ("BTC-USD", "Bitcoin"),
]


@router.get("/market-pulse")
def market_pulse(quotes: QuoteFn = Depends(get_quotes)) -> dict[str, Any]:
    fetched_at = datetime.now(UTC).isoformat()
    prices = quotes([s for s, _ in PULSE_SYMBOLS])
    rows = []
    for symbol, label in PULSE_SYMBOLS:
        price = prices.get(symbol)
        rows.append(
            {
                "symbol": symbol,
                "label": label,
                "price": float(price) if price is not None else None,
                "stale": price is None,
                "as_of": fetched_at,
            }
        )
    return {"as_of": fetched_at, "rows": rows}
