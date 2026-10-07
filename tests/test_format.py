"""Bug 2 (P0): one formatter, used everywhere. Round at the boundary only.

`$713.6099853515625` shipped in the 2026-08-16 brief because renderers
interpolated raw floats with bare f-strings. Presentation rules live here so a
new renderer cannot reinvent them wrongly.
"""

from __future__ import annotations

from decimal import Decimal

from atlas.core.format import bps, money, pct, shares


def test_money_is_two_places_with_thousands_separators() -> None:
    assert money(Decimal("713.6099853515625")) == "$713.61"
    assert money(Decimal("4379.71")) == "$4,379.71"
    assert money(Decimal("0")) == "$0.00"


def test_money_accepts_float_without_leaking_binary_artifacts() -> None:
    assert money(713.6099853515625) == "$713.61"


def test_money_handles_negatives_with_the_sign_outside_the_symbol() -> None:
    assert money(Decimal("-6.28")) == "-$6.28"


def test_pct_is_two_places() -> None:
    assert pct(Decimal("26.0138")) == "26.01%"
    assert pct(Decimal("0")) == "0.00%"


def test_tiny_but_nonzero_weight_never_renders_as_zero() -> None:
    """A held position must never disappear into 0.00%. BRK.B is ~0.12% of the
    book; a smaller promo lot would otherwise round out of existence."""
    assert pct(Decimal("0.004")) == "<0.01%"
    assert pct(Decimal("-0.004")) == ">-0.01%"


def test_exact_zero_is_still_zero_not_the_epsilon_label() -> None:
    assert pct(Decimal("0.0")) == "0.00%"


def test_shares_are_six_places_per_2_1_rule_8() -> None:
    assert shares(Decimal("10.105932")) == "10.105932"
    assert shares(Decimal("2")) == "2.000000"


def test_shares_never_round_away_the_operators_actual_fill() -> None:
    assert shares(Decimal("0.010345")) == "0.010345"


def test_bps_are_integers() -> None:
    assert bps(Decimal("0.0425")) == "4 bps"


def test_money_rounds_half_up_not_bankers() -> None:
    """Decimal's default ROUND_HALF_EVEN would give $0.12 here; money must not
    silently shave a cent off the operator's basis."""
    assert money(Decimal("0.125")) == "$0.13"
