"""Market data: Robinhood MCP (primary) → yfinance (fallback/enrichment).

The MCP reader is duck-typed (`MarketReader`) and injected at the composition
root — this module never imports atlas.execution (§1.2).
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from atlas.core.logging import get_logger
from atlas.ingest.symbols import to_yahoo

log = get_logger("ingest.market")


class MarketReader(Protocol):
    """Read-only quote source (satisfied by MCPClient, injected — never imported)."""

    def call(self, tool: str, **arguments: Any) -> Any: ...


class Quote(BaseModel):
    symbol: str
    price: float
    source: str
    as_of: datetime


class Indicators(BaseModel):
    symbol: str
    rsi_14: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    bollinger_upper: float | None = None
    bollinger_lower: float | None = None
    source: str = "computed"


class MarketSnapshot(BaseModel):
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    quotes: dict[str, Quote] = Field(default_factory=dict)
    indicators: dict[str, Indicators] = Field(default_factory=dict)
    index_levels: dict[str, float] = Field(default_factory=dict)
    earnings_calendar: list[dict[str, Any]] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)


# --- indicator math (pure; MCP has these but yfinance fallback needs them) ----


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes[-period - 1 : -1], closes[-period:], strict=True):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_loss = sum(losses) / period
    avg_gain = sum(gains) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 4)


def _ema(values: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(closes: list[float]) -> tuple[float, float] | None:
    if len(closes) < 35:
        return None
    fast, slow = _ema(closes, 12), _ema(closes, 26)
    line = [f - s for f, s in zip(fast, slow, strict=True)]
    signal = _ema(line[-35:], 9)
    return round(line[-1], 4), round(signal[-1], 4)


def bollinger(closes: list[float], period: int = 20) -> tuple[float, float] | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    sd = statistics.pstdev(window)
    return round(mid + 2 * sd, 4), round(mid - 2 * sd, 4)


def compute_indicators(symbol: str, closes: list[float]) -> Indicators:
    m = macd(closes)
    b = bollinger(closes)
    return Indicators(
        symbol=symbol,
        rsi_14=rsi(closes),
        sma_20=sma(closes, 20),
        sma_50=sma(closes, 50),
        sma_200=sma(closes, 200),
        macd=m[0] if m else None,
        macd_signal=m[1] if m else None,
        bollinger_upper=b[0] if b else None,
        bollinger_lower=b[1] if b else None,
    )


# --- yfinance fallback (patchable seam for tests) -----------------------------


def _yf_closes(symbol: str, period: str = "1y") -> list[float]:
    import yfinance as yf

    # Single translation point: every yfinance path (quotes and indicators)
    # funnels through here, so class shares like BRK.B -> BRK-B are handled once.
    hist = yf.Ticker(to_yahoo(symbol)).history(period=period)
    return [float(c) for c in hist["Close"].tolist()]


def _yf_price(symbol: str) -> float:
    closes = _yf_closes(symbol, period="5d")
    if not closes:
        raise ValueError(f"yfinance returned no data for {symbol}")
    return closes[-1]


# --- snapshot builder ---------------------------------------------------------


def build_market_snapshot(
    tickers: list[str],
    reader: MarketReader | None = None,
    index_symbols: tuple[str, ...] = ("SPY", "QQQ", "DIA", "SOXX"),
) -> MarketSnapshot:
    """Primary: MCP reader. Fallback per ticker: yfinance. Failures degrade to anomalies."""
    snap = MarketSnapshot()
    now = datetime.now(UTC)

    mcp_quotes: dict[str, Any] = {}
    if reader is not None:
        try:
            batch = list(dict.fromkeys([*tickers, *index_symbols]))[:20]
            mcp_quotes = reader.call("get_equity_quotes", symbols=batch) or {}
        except Exception as exc:  # noqa: BLE001 — degrade, never block the cycle
            snap.anomalies.append(f"MCP quotes unavailable: {exc}")

    for symbol in dict.fromkeys([*tickers, *index_symbols]):
        entry = mcp_quotes.get(symbol) if isinstance(mcp_quotes, dict) else None
        if entry and entry.get("last_trade_price"):
            snap.quotes[symbol] = Quote(
                symbol=symbol, price=float(entry["last_trade_price"]),
                source="robinhood_mcp", as_of=now,
            )
        else:
            try:
                snap.quotes[symbol] = Quote(
                    symbol=symbol, price=_yf_price(symbol), source="yfinance", as_of=now
                )
            except Exception as exc:  # noqa: BLE001
                snap.anomalies.append(f"no quote for {symbol}: {exc}")

    for symbol in tickers:
        try:
            closes = _yf_closes(symbol)
            if closes:
                snap.indicators[symbol] = compute_indicators(symbol, closes)
        except Exception as exc:  # noqa: BLE001
            snap.anomalies.append(f"no indicator history for {symbol}: {exc}")

    for symbol in index_symbols:
        if symbol in snap.quotes:
            snap.index_levels[symbol] = snap.quotes[symbol].price

    if reader is not None:
        try:
            cal = reader.call("get_earnings_calendar")
            if isinstance(cal, list):
                snap.earnings_calendar = cal
        except Exception as exc:  # noqa: BLE001
            snap.anomalies.append(f"earnings calendar unavailable: {exc}")

    return snap
