"""Bug 1 (P0): day change is a per-position price return, never total-vs-total.

A total-vs-total diff cannot tell a price move from a deposit, a new position or
a composition change — that is exactly how the 2026-08-16 brief printed
"+94.86%" after portfolio.yaml was replaced. Every test here reproduces one of
those confusions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from atlas.portfolio.performance import (
    TRIPWIRE_PCT,
    ImplausibleReturnError,
    compute_day_change,
)

PRIOR_DATE = date(2026, 8, 15)


def snap(**tickers: tuple[str, str]) -> dict[str, dict[str, str]]:
    """{'VOO': ('1.0', '700.00')} -> stored snapshot shape (shares, price, value)."""
    return {
        t: {
            "shares": sh,
            "price": px,
            "value": str(Decimal(sh) * Decimal(px)),
        }
        for t, (sh, px) in tickers.items()
    }


def test_price_return_is_value_weighted_across_positions() -> None:
    prior = snap(VOO=("1.0", "700.00"), F=("10.0", "10.00"))  # 700 + 100 = 800
    current = snap(VOO=("1.0", "707.00"), F=("10.0", "10.00"))  # VOO +1%, F flat
    result = compute_day_change(prior, current, PRIOR_DATE)
    # 700/800 weight on +1%, 100/800 weight on 0% => +0.875%
    assert result.pct == Decimal("0.875")


def test_buying_more_shares_is_not_a_return() -> None:
    """Share count doubles at an unchanged price: 0% return, not +100%."""
    prior = snap(VOO=("1.0", "700.00"))
    current = snap(VOO=("2.0", "700.00"))
    assert compute_day_change(prior, current, PRIOR_DATE).pct == Decimal("0")


def test_deposit_during_window_does_not_move_the_return() -> None:
    """Cash never enters the calculation, so a deposit cannot masquerade as alpha."""
    prior = snap(VOO=("1.0", "700.00"))
    current = snap(VOO=("1.0", "700.00"))
    result = compute_day_change(prior, current, PRIOR_DATE)
    assert result.pct == Decimal("0")
    assert result.available


def test_composition_change_degrades_loudly_and_names_the_tickers() -> None:
    prior = snap(VOO=("1.0", "700.00"), SMH=("1.0", "500.00"))
    current = snap(VOO=("1.0", "700.00"), ANET=("2.0", "200.00"))
    result = compute_day_change(prior, current, PRIOR_DATE)
    assert not result.available
    assert result.pct is None
    assert result.added == ["ANET"]
    assert result.removed == ["SMH"]
    rendered = result.render(PRIOR_DATE)
    assert "n/a" in rendered
    assert "composition changed" in rendered
    assert "ANET" in rendered and "SMH" in rendered
    assert "2026-08-15" in rendered


def test_position_going_to_zero_is_a_composition_change_not_a_100pct_loss() -> None:
    prior = snap(VOO=("1.0", "700.00"), F=("10.0", "10.00"))
    current = snap(VOO=("1.0", "700.00"))
    result = compute_day_change(prior, current, PRIOR_DATE)
    assert not result.available
    assert result.removed == ["F"]


def test_first_ever_snapshot_has_no_prior_and_says_so() -> None:
    result = compute_day_change(None, snap(VOO=("1.0", "700.00")), None)
    assert not result.available
    assert "first tracked day" in result.render(None)


def test_legacy_value_only_snapshot_degrades_instead_of_lying() -> None:
    """Pre-fix rows stored {ticker: "value"} with no price — not comparable."""
    # Same ticker set as `current`, so this isolates the format check rather than
    # tripping the composition check first.
    legacy = {"VOO": "748.87"}
    result = compute_day_change(legacy, snap(VOO=("1.0", "700.00")), PRIOR_DATE)
    assert not result.available
    assert "incompatible" in result.render(PRIOR_DATE).lower()


def test_implausible_return_halts_the_render_rather_than_printing() -> None:
    """A retail book of index funds and megacaps does not move 94% in a day."""
    prior = snap(VOO=("1.0", "700.00"))
    current = snap(VOO=("1.0", "1400.00"))  # +100%
    with pytest.raises(ImplausibleReturnError) as exc:
        compute_day_change(prior, current, PRIOR_DATE)
    assert "100" in str(exc.value)


def test_tripwire_boundary_is_inclusive_and_passes_at_the_limit() -> None:
    prior = snap(VOO=("1.0", "100.00"))
    current = snap(VOO=("1.0", str(100 + TRIPWIRE_PCT)))
    assert compute_day_change(prior, current, PRIOR_DATE).pct == TRIPWIRE_PCT


def test_zero_prior_value_cannot_divide_by_zero() -> None:
    prior = snap(VOO=("0.0", "0.00"))
    result = compute_day_change(prior, snap(VOO=("0.0", "0.00")), PRIOR_DATE)
    assert not result.available


def test_the_actual_2026_08_16_regression() -> None:
    """The exact shape that produced +94.86%: book replaced, 4 names -> 12."""
    prior = snap(VOO=("0.233655", "683.17"), SMH=("0.050234", "547.00"),
                 XLV=("0.200852", "161.00"), NVDA=("0.123310", "202.00"))
    current = snap(VOO=("1.049410", "713.61"), DIA=("0.194089", "536.80"),
                   ANET=("2.0", "198.82"))
    result = compute_day_change(prior, current, PRIOR_DATE)
    assert not result.available, "a replaced book must never print a return"
    assert "94" not in result.render(PRIOR_DATE)
