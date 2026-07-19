"""Literal Robinhood app paths for a ticket (§8.4) — the manual-execution manual.

Shared by the brief synthesizer and the API so the dashboard's
"place this yourself" panel and the brief never drift apart.
"""

from __future__ import annotations

from decimal import Decimal


def robinhood_howto(
    *,
    action: str,
    ticker: str,
    order_type: str,
    is_fractional: bool,
    quantity: Decimal | float | str,
    dollar_amount: Decimal | float,
    limit_price: Decimal | float | None,
) -> str:
    selling = action.upper() in ("SELL", "TRIM")
    if selling:
        base = f'Open Robinhood → Search "{ticker}" → ticker page → **Trade** → **Sell**. '
        if is_fractional:
            return base + (
                "Choose **Sell in Dollars** — Robinhood runs fractional sells as **market** "
                "orders (no price field), because fractional quantities are market-only."
            )
        return base + (
            "Tap the order-type selector (top-right) → **Stop Limit Order** → set stop "
            f"${limit_price} / limit a few cents below → **Shares**: {quantity} → Review → Submit."
        )
    base = (
        f'Open Robinhood → Search "{ticker}" → check the detail page (price vs 52-wk '
        "range, expense ratio if ETF, P/E if stock) → **Trade** → **Buy**. "
    )
    if is_fractional:
        return base + (
            f"Robinhood switches to **Buy in Dollars** for ${float(dollar_amount):.2f} — this is a "
            "**market** order with **no price field**, because fractional shares are "
            "market-only on Robinhood. You get the next market price; there is no price cap."
        )
    return base + (
        f"Tap the order-type selector → **Limit Order** → limit price ${limit_price} → "
        f"**Shares**: {quantity} → good-for-day → Review → Submit. The order only fills at "
        f"${limit_price} or better."
    )
