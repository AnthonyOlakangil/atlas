"""Ticker dialect translation.

The operator writes tickers the way Robinhood shows them (`BRK.B`). Yahoo spells
class shares with a hyphen (`BRK-B`) and returns "possibly delisted" for the dot
form — which produced a silent price of 0, a $0.00 valuation, and a position
that was held but invisible in the sleeve table.
"""

from __future__ import annotations


def to_yahoo(ticker: str) -> str:
    """Canonical ticker -> Yahoo Finance dialect.

    Index symbols (`^TNX`) pass through untouched; only the class-share
    separator differs between the two dialects.
    """
    symbol = ticker.strip().upper()
    if symbol.startswith("^"):
        return symbol
    return symbol.replace(".", "-")
