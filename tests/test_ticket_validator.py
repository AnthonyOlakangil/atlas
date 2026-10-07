"""Bug 3 (P0): a structurally invalid ticket must be unbuildable, not caught on review.

The operator is the execution layer in manual mode, so a ticket that cannot
execute wastes their time at best and misleads them at worst. Every rejection
carries the ticker, order type, offending value and the rule that caught it, so
it can surface in §9 Anomalies — a silently dropped ticket is worse than a bad one.
"""

from __future__ import annotations

import re
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from atlas.core.models import OrderType
from atlas.portfolio.validate import (
    FAT_FINGER_PCT,
    MARKETABLE_BUFFER_PCT,
    TicketDraft,
    validate_ticket,
)


def draft(**over: object) -> TicketDraft:
    base: dict[str, object] = {
        "ticker": "GLDM",
        "action": "BUY",
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("2"),
        "limit_price": Decimal("87.01"),
        "market_price": Decimal("86.58"),
        "dollar_amount": Decimal("174.02"),
        "settled_cash": Decimal("1283.86"),
        "is_fractional": False,
    }
    base.update(over)
    return TicketDraft(**base)  # type: ignore[arg-type]


def test_a_valid_marketable_limit_passes() -> None:
    assert validate_ticket(draft()).ok


def test_fractional_quantity_with_a_limit_order_is_rejected() -> None:
    """Robinhood is fractional-on-market-only, so this combination cannot execute."""
    result = validate_ticket(
        draft(is_fractional=True, quantity=Decimal("0.5029"), order_type=OrderType.LIMIT)
    )
    assert not result.ok
    assert result.rule == "fractional_limit"
    assert "GLDM" in result.message and "0.5029" in result.message


def test_fractional_market_order_is_fine() -> None:
    assert validate_ticket(
        draft(is_fractional=True, quantity=Decimal("0.5029"),
              order_type=OrderType.MARKET, limit_price=None)
    ).ok


def test_buy_limit_far_above_market_is_a_fat_finger() -> None:
    result = validate_ticket(draft(limit_price=Decimal("104.00")))  # +20%
    assert not result.ok
    assert result.rule == "fat_finger"
    assert "10" in result.message


def test_buy_limit_far_below_market_is_also_a_fat_finger() -> None:
    result = validate_ticket(draft(limit_price=Decimal("60.00")))  # -30%
    assert not result.ok
    assert result.rule == "fat_finger"


def test_fat_finger_boundary_is_inclusive_on_the_passive_side() -> None:
    """The ±10% guard is the outer bound for the PASSIVE direction. Above market
    the tighter 1% marketable bound binds first, so -10% is where this is tested.
    Quantize UP — rounding down would push it past the threshold and test nothing."""
    at_limit = (Decimal("86.58") * (1 - FAT_FINGER_PCT / 100)).quantize(
        Decimal("0.01"), rounding=ROUND_UP
    )
    assert validate_ticket(draft(limit_price=at_limit)).ok


def test_order_value_above_settled_cash_is_rejected() -> None:
    result = validate_ticket(draft(dollar_amount=Decimal("2000"), settled_cash=Decimal("100")))
    assert not result.ok
    assert result.rule == "insufficient_cash"
    assert "2000" in result.message or "2,000" in result.message


def test_a_sell_is_not_checked_against_settled_cash() -> None:
    """Proceeds come in; a sell never spends settled cash."""
    assert validate_ticket(
        draft(action="TRIM", dollar_amount=Decimal("2000"),
              settled_cash=Decimal("100"), order_type=OrderType.MARKET,
              limit_price=None, is_fractional=True, quantity=Decimal("0.5"))
    ).ok


def test_marketable_limit_is_disclosed_not_silently_accepted() -> None:
    """The 2026-08-16 brief said "only fills at $87.01 or better" while the quote
    was $86.58 — true but misleading, because the order fills immediately. The
    ticket must say so."""
    result = validate_ticket(draft())
    assert result.ok
    assert result.disclosure
    assert "immediately" in result.disclosure.lower()
    assert "86.58" in result.disclosure, "must name the price it will actually fill at"
    assert "0.5%" in result.disclosure, "must state what the cap is worth"


def test_marketable_buffer_beyond_the_bound_is_rejected() -> None:
    """+0.5% caps slippage; +5% caps nothing meaningful and signals a sizing
    error or a stale quote."""
    result = validate_ticket(draft(limit_price=Decimal("90.91")))  # +5%
    assert not result.ok
    assert result.rule == "excessive_marketable_buffer"


def test_marketable_buffer_at_the_bound_passes() -> None:
    at_bound = (Decimal("86.58") * (1 + MARKETABLE_BUFFER_PCT / 100)).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    assert validate_ticket(draft(limit_price=at_bound)).ok


def test_sell_limit_below_bid_is_marketable_and_bounded_the_same_way() -> None:
    sell = {"action": "TRIM", "order_type": OrderType.LIMIT, "is_fractional": False,
            "quantity": Decimal("1"), "dollar_amount": Decimal("80"),
            "settled_cash": Decimal("0")}
    assert validate_ticket(draft(limit_price=Decimal("86.15"), **sell)).ok  # -0.5%
    bad = validate_ticket(draft(limit_price=Decimal("82.25"), **sell))  # -5%
    assert not bad.ok
    assert bad.rule == "excessive_marketable_buffer"


def test_disclosure_never_implies_price_improvement() -> None:
    text = (validate_ticket(draft()).disclosure or "").lower()
    assert "does not obtain a better price" in text
    assert "or better" not in text, "the phrasing that misled on 2026-08-16"


def test_a_marketable_limit_carries_a_laymans_block() -> None:
    """§2.1a: the market / marketable-limit / passive-limit distinction is a
    mechanism, and mechanisms get unpacked."""
    result = validate_ticket(draft())
    assert result.laymans and result.laymans.startswith("**So in layman's terms:**")
    # Count sentence ends, not periods — money() emits "$86.58" and a naive
    # split on "." counts decimal points as sentence boundaries.
    sentences = len(re.findall(r"[.!?](?:\s|$)", result.laymans))
    assert 2 <= sentences <= 4, f"§2.1a allows two to four sentences, got {sentences}"


def test_passive_limit_states_its_fill_condition() -> None:
    text = (validate_ticket(draft(limit_price=Decimal("85.00"))).disclosure or "").lower()
    assert "may not fill" in text
    assert "only if the price trades" in text


def test_missing_market_price_cannot_be_validated_and_is_rejected() -> None:
    """A zero/absent quote is the BRK.B silent-zero failure mode; never wave it through."""
    result = validate_ticket(draft(market_price=Decimal("0")))
    assert not result.ok
    assert result.rule == "no_market_price"
