"""Task 1a [high]: an unresolvable sleeve must fail the load, never default.

`state.py` fell back to `infer_sleeve()` and then to the literal "unassigned",
which `sizing.py` later rejected as an unknown sleeve — so a mislabelled holding
vanished from band math with no error. Five of twelve holdings hit this before
explicit `sleeve:` keys were added, which would have made every concentration
warning in every brief wrong.

The operator is the execution layer in manual mode. A broken limit order is
visible; a sleeve reading 24% when it is really 31% is not.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from atlas.core.settings import CONFIG_DIR, OperatorConfig
from atlas.portfolio.state import (
    PortfolioConfigError,
    load_manual_state,
    validate_allocation,
)

PRICES = {
    "VOO": Decimal("700.00"),
    "SMH": Decimal("500.00"),
    "XLV": Decimal("160.00"),
}
AS_OF = __import__("datetime").date(2026, 8, 16)


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "portfolio.yaml"
    p.write_text(body)
    return p


BASE_CASH = "settled_cash: 100.00\nunsettled_cash: 0.00\n"


def test_holding_with_no_sleeve_key_fails_the_load(tmp_path: Path) -> None:
    path = write(tmp_path, "holdings:\n  - {ticker: VOO, shares: 1.0, cost_basis: 700.00}\n" + BASE_CASH)
    with pytest.raises(PortfolioConfigError) as exc:
        load_manual_state(PRICES, AS_OF, path)
    assert "VOO" in str(exc.value)
    assert "sleeve" in str(exc.value).lower()


def test_empty_string_sleeve_fails_the_load(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "holdings:\n  - {ticker: VOO, sleeve: '', shares: 1.0, cost_basis: 700.00}\n" + BASE_CASH,
    )
    with pytest.raises(PortfolioConfigError) as exc:
        load_manual_state(PRICES, AS_OF, path)
    assert "VOO" in str(exc.value)


def test_typod_sleeve_name_fails_and_names_the_offending_value(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "holdings:\n  - {ticker: SMH, sleeve: satelite_ai_semis, shares: 1.0, cost_basis: 500.00}\n"
        + BASE_CASH,
    )
    with pytest.raises(PortfolioConfigError) as exc:
        load_manual_state(PRICES, AS_OF, path)
    message = str(exc.value)
    assert "SMH" in message
    assert "satelite_ai_semis" in message, "the error must quote the bad value"
    assert "satellite_ai_semis" in message, "and list the valid sleeves"


def test_sleeve_names_are_validated_against_operator_yaml_not_a_literal() -> None:
    """The valid set is whatever operator.yaml declares — no hardcoded list."""
    cfg = OperatorConfig.load()
    assert set(cfg.sleeves) == set(cfg.declared_sleeves())


def test_declared_sleeve_missing_its_band_is_a_load_failure(tmp_path: Path) -> None:
    """settings.py silently drops a sleeve declared without target_pct. A sleeve
    that holds positions but has no band cannot be checked for drift."""
    cfg = OperatorConfig.load()
    bandless = cfg.model_copy(deep=True)
    del bandless.sleeves["diversifier"]
    path = write(
        tmp_path,
        "holdings:\n  - {ticker: XLV, sleeve: diversifier, shares: 1.0, cost_basis: 160.00}\n"
        + BASE_CASH,
    )
    with pytest.raises(PortfolioConfigError) as exc:
        load_manual_state(PRICES, AS_OF, path, cfg=bandless)
    assert "diversifier" in str(exc.value)


def test_allocation_weights_plus_cash_must_reconcile_to_one_hundred_percent(
    tmp_path: Path,
) -> None:
    """The backstop: whatever the cause, if declared-sleeve weights plus cash do
    not sum to 100% then a holding has fallen out of the band math."""
    path = write(
        tmp_path,
        "holdings:\n"
        "  - {ticker: VOO, sleeve: core, shares: 1.0, cost_basis: 700.00}\n"
        "  - {ticker: SMH, sleeve: satellite_ai_semis, shares: 1.0, cost_basis: 500.00}\n"
        + BASE_CASH,
    )
    state = load_manual_state(PRICES, AS_OF, path)
    validate_allocation(state, OperatorConfig.load())  # must not raise


def test_allocation_backstop_fires_when_a_holding_sits_outside_declared_sleeves(
    tmp_path: Path,
) -> None:
    path = write(
        tmp_path,
        "holdings:\n  - {ticker: VOO, sleeve: core, shares: 1.0, cost_basis: 700.00}\n" + BASE_CASH,
    )
    state = load_manual_state(PRICES, AS_OF, path)
    # Simulate the original bug: a position whose sleeve is not a declared one.
    state.positions[0].sleeve = "unassigned"
    with pytest.raises(PortfolioConfigError) as exc:
        validate_allocation(state, OperatorConfig.load())
    assert "unassigned" in str(exc.value) or "reconcile" in str(exc.value).lower()


@pytest.mark.skipif(
    not (CONFIG_DIR / "portfolio.yaml").exists(),
    reason="portfolio.yaml holds real holdings and is gitignored (absent in CI)",
)
def test_the_real_portfolio_loads_clean_under_strict_validation() -> None:
    """The operator's actual book, with BRK.B, must satisfy every rule above."""
    state = load_manual_state(
        {t: Decimal("100.00") for t in
         ("VOO", "DIA", "SMH", "NVDA", "ANET", "AVGO", "MU", "XLV", "XLF", "NLR",
          "NFLX", "F", "BRK.B")},
        AS_OF,
    )
    validate_allocation(state, OperatorConfig.load())
    assert len(state.positions) == 13
