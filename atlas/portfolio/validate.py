"""Structural ticket validation at generation time (Bug 3, P0).

An invalid ticket must be unbuildable, not caught on review. In manual mode the
operator *is* the execution layer, so a ticket that cannot execute costs them a
trip to the app; one that misrepresents its own mechanics costs them money.

Every rejection names the ticker, the order type, the offending value and the
rule, so §9 Anomalies can show what was dropped and why. A silently dropped
ticket is worse than a bad one.

On marketable limits — deliberately NOT rejected by default. `sizing.py` prices
buy limits 0.5% above market so the order actually fills. That is a marketable
limit order: it executes immediately, but it *caps* slippage rather than leaving
the fill uncapped the way a market order does. Rejecting it outright would
reject every whole-share buy Atlas can generate and push the book toward
uncapped market orders. The real defect in the 2026-08-16 brief was disclosure,
not order type — it said "only fills at $87.01 or better" while quoting $86.58,
which is true but reads as patience the order does not have.

So marketable limits are disclosed, never rejected outright — but the buffer is
**bounded** at MARKETABLE_BUFFER_PCT. Below it the cap is real protection; above
it the cap protects nothing and almost certainly reflects a sizing error or a
stale quote. There is deliberately no config flag to reject marketable limits
wholesale: that would reject every whole-share buy sizing.py can generate and
push the book toward uncapped market orders, which is strictly worse.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from atlas.core.format import money
from atlas.core.models import OrderType

# A limit further than this from market is a fat finger, not a strategy. Outer
# bound for the passive (below-market buy / above-market sell) direction.
FAT_FINGER_PCT = Decimal("10")

# A marketable limit prices through the spread so the order fills, capping
# slippage rather than seeking price improvement. Legitimate while small —
# sizing.py uses 0.5%. Past this the cap protects nothing and signals an error.
MARKETABLE_BUFFER_PCT = Decimal("1")

# Idea-level actions, not the persisted TradeAction enum (which is BUY/SELL only):
# a TRIM is a partial SELL and must not be cash-checked like a buy.
_SELL_ACTIONS = frozenset({"SELL", "TRIM"})


class TicketDraft(BaseModel):
    ticker: str
    action: str
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    market_price: Decimal
    dollar_amount: Decimal
    settled_cash: Decimal
    is_fractional: bool


class ValidationResult(BaseModel):
    ok: bool
    rule: str | None = None
    message: str | None = None
    disclosure: str | None = None
    # §2.1a unpacking, required the first time a marketable limit appears: the
    # market / marketable-limit / passive-limit distinction is exactly the kind
    # of mechanism that section exists to explain.
    laymans: str | None = None


def _fail(draft: TicketDraft, rule: str, message: str) -> ValidationResult:
    return ValidationResult(
        ok=False,
        rule=rule,
        message=f"{draft.ticker} {draft.action} {draft.order_type.value}: {message}",
    )


def validate_ticket(draft: TicketDraft) -> ValidationResult:
    """Structural checks only — sizing and thesis quality live elsewhere."""
    if draft.market_price <= 0:
        return _fail(
            draft, "no_market_price",
            "no usable market price this cycle, so the order cannot be validated "
            "or priced. A held position with a zero quote is a data failure, not a $0 asset.",
        )

    if draft.is_fractional and draft.order_type is OrderType.LIMIT:
        return _fail(
            draft, "fractional_limit",
            f"quantity {draft.quantity} is fractional and Robinhood runs fractional "
            "orders as market-only — this combination cannot execute at all.",
        )

    if draft.action.upper() not in _SELL_ACTIONS and draft.dollar_amount > draft.settled_cash:
        return _fail(
            draft, "insufficient_cash",
            f"order value {money(draft.dollar_amount)} exceeds settled cash "
            f"{money(draft.settled_cash)}.",
        )

    if draft.limit_price is None:
        return ValidationResult(ok=True)

    drift_pct = (draft.limit_price - draft.market_price) / draft.market_price * 100
    if abs(drift_pct) > FAT_FINGER_PCT:
        return _fail(
            draft, "fat_finger",
            f"limit {money(draft.limit_price)} is {drift_pct:+.1f}% from the "
            f"{money(draft.market_price)} market price, beyond the ±{FAT_FINGER_PCT}% guard.",
        )

    is_buy = draft.action.upper() not in _SELL_ACTIONS
    marketable = drift_pct >= 0 if is_buy else drift_pct <= 0
    buffer_pct = abs(drift_pct)

    if marketable:
        # A marketable limit is legitimate slippage protection, but only while the
        # buffer is small. Past MARKETABLE_BUFFER_PCT it caps nothing meaningful
        # and is far more likely a sizing error or a stale quote.
        if buffer_pct > MARKETABLE_BUFFER_PCT:
            return _fail(
                draft, "excessive_marketable_buffer",
                f"limit {money(draft.limit_price)} is {buffer_pct:.1f}% "
                f"{'above' if is_buy else 'below'} the {money(draft.market_price)} "
                f"market price — beyond the {MARKETABLE_BUFFER_PCT}% marketable "
                "buffer, so it offers no meaningful protection and likely reflects "
                "a sizing error or a stale quote.",
            )
        side = "ask" if is_buy else "bid"
        disclosure = (
            f"Marketable limit: this will likely fill immediately at or near the "
            f"current {side} of {money(draft.market_price)}. The limit caps slippage "
            f"at {buffer_pct:.1f}% — it does not obtain a better price."
        )
        laymans = (
            "**So in layman's terms:** this is not a patient order waiting for a "
            "bargain. It is priced to go through right away, at roughly today's "
            f"{money(draft.market_price)}. The only thing the limit does is set a "
            f"ceiling, so if the price jumps while the order is travelling you still "
            f"pay no more than {money(draft.limit_price)}. A plain market order would "
            "have no ceiling at all."
        )
        return ValidationResult(ok=True, disclosure=disclosure, laymans=laymans)

    # Passive side: the ±10% fat-finger guard above is the outer bound here.
    condition = (
        f"at or below {money(draft.limit_price)}"
        if is_buy
        else f"at or above {money(draft.limit_price)}"
    )
    disclosure = (
        f"Passive limit: {money(draft.limit_price)} is {buffer_pct:.1f}% "
        f"{'below' if is_buy else 'above'} the {money(draft.market_price)} market "
        f"price, so it fills only if the price trades {condition}. It may not fill "
        "at all."
    )
    return ValidationResult(ok=True, disclosure=disclosure)
