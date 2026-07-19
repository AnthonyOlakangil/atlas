"""Phase 3 gate: no proposal can violate sleeve bands or risk caps — and the
seed lists are NOT a whitelist (§2.0)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from atlas.core.models import OrderType
from atlas.core.settings import OperatorConfig
from atlas.portfolio.drift import BandStatus, compute_drift
from atlas.portfolio.sizing import MIN_TICKET_USD, size_buy
from atlas.portfolio.state import CashState, Ledger, PortfolioState, PositionView

D = Decimal
CFG = OperatorConfig.load()


def make_state(
    positions: list[PositionView] | None = None, settled: str = "250", unsettled: str = "0"
) -> PortfolioState:
    cash = CashState(settled=D(settled), unsettled=D(unsettled))
    return PortfolioState(
        as_of=date(2026, 7, 17),
        positions=positions or [],
        cash=cash,
        ledger=Ledger(cash=cash, gfv_events=[], open_lots=[]),
    )


def pos(ticker: str, sleeve: str, value: str, price: str = "100") -> PositionView:
    return PositionView(
        ticker=ticker, sleeve=sleeve, shares=D(value) / D(price), cost_basis=D(value),
        price=D(price), value=D(value),
    )


# --- the §2.0 non-negotiable: seed lists are not a whitelist -----------------


def test_non_seed_instrument_is_accepted_and_sized_normally() -> None:
    """QQQM appears in NO seed_examples list — it must size exactly like a seed ticker."""
    all_seeds = {t for s in CFG.sleeves.values() for t in s.seed_examples}
    assert "QQQM" not in all_seeds, "test premise: QQQM must not be a seed ticker"
    decision = size_buy(
        ticker="QQQM", sleeve="satellite_ai_semis", price=D("230.50"),
        target_dollars=D("60"), state=make_state(), cfg=CFG,
    )
    assert decision.approved
    assert decision.dollar_amount == D("60.00")
    assert decision.is_fractional and decision.order_type is OrderType.MARKET


def test_unknown_unmapped_ticker_still_sizes_with_review_flag() -> None:
    """A ticker Atlas has never heard of (no sector map entry) is sized, not rejected."""
    decision = size_buy(
        ticker="ZZZT", sleeve="speculative", price=D("12.00"),
        target_dollars=D("12"), state=make_state(), cfg=CFG,
    )
    assert decision.approved
    assert any("sector unknown" in r for r in decision.reasons)


# --- risk caps and sleeve bands ----------------------------------------------


def test_single_position_cap_clamps_spend() -> None:
    state = make_state(
        positions=[pos("VOO", "core", "150"), pos("NVDA", "satellite_ai_semis", "50")],
        settled="800",
    )  # total = 1000
    decision = size_buy(
        ticker="NVDA", sleeve="satellite_ai_semis", price=D("204.12"),
        target_dollars=D("400"), state=state, cfg=CFG,
    )
    # cap: 25% of 1000 = 250; already 50 → max 200 more
    assert decision.dollar_amount <= D("200")
    assert any("single-position cap" in r for r in decision.reasons)


def test_sleeve_band_clamps_spend() -> None:
    state = make_state(
        positions=[pos("SOXX", "satellite_ai_semis", "200")], settled="800",
    )  # total 1000; satellite band hi = 25% → max 250 → 50 more
    decision = size_buy(
        ticker="SMH", sleeve="satellite_ai_semis", price=D("84.00"),
        target_dollars=D("300"), state=state, cfg=CFG,
    )
    assert decision.dollar_amount <= D("50")
    assert any("sleeve band" in r for r in decision.reasons)


def test_sector_concentration_cap_spans_sleeves() -> None:
    # semis spread across sleeves still count as one sector bet
    state = make_state(
        positions=[pos("NVDA", "satellite_ai_semis", "220"), pos("SMH", "speculative", "220")],
        settled="560",
    )  # total 1000; tech_semis = 440; cap 45% = 450 → only 10 more
    decision = size_buy(
        ticker="TSM", sleeve="satellite_ai_semis", price=D("436.00"),
        target_dollars=D("100"), state=state, cfg=CFG,
    )
    # sleeve band clamps 100 → 30, then the cross-sleeve sector cap clamps 30 → 10
    assert decision.dollar_amount == D("10.00")
    assert any("sector-concentration" in r for r in decision.reasons)


# --- §2.1 platform + cash rules ----------------------------------------------


def test_fractional_buy_is_forced_market_no_limit_price() -> None:
    decision = size_buy(
        ticker="VOO", sleeve="core", price=D("686.90"),
        target_dollars=D("150"), state=make_state(), cfg=CFG,
    )
    assert decision.approved and decision.is_fractional
    assert decision.order_type is OrderType.MARKET
    assert decision.limit_price is None  # NEVER a limit price on a fractional ticket
    assert any("no price cap" in r for r in decision.reasons)


def test_whole_share_buy_defaults_to_limit() -> None:
    decision = size_buy(
        ticker="GLDM", sleeve="diversifier", price=D("80.15"),
        target_dollars=D("170"), state=make_state(settled="2000"), cfg=CFG,
    )
    assert decision.approved and not decision.is_fractional
    assert decision.order_type is OrderType.LIMIT
    assert decision.quantity == D("2")
    assert decision.limit_price is not None and decision.limit_price > D("80.15")


def test_unaffordable_thesis_becomes_watch_item_not_ticket() -> None:
    decision = size_buy(
        ticker="VOO", sleeve="core", price=D("686.90"),
        target_dollars=D("150"), state=make_state(settled="20", unsettled="500"), cfg=CFG,
    )
    assert not decision.approved
    assert decision.watch_item  # unsettled $500 exists — still not fundable (§2.1)
    assert any("watch item" in r for r in decision.reasons)


def test_cash_math_shown_in_decision() -> None:
    decision = size_buy(
        ticker="VOO", sleeve="core", price=D("686.90"),
        target_dollars=D("150"), state=make_state(settled="250"), cfg=CFG,
        is_diversified_fund=True,  # core ETF: sleeve band governs, not the single-issuer cap
    )
    assert decision.cash_before == D("250")
    assert decision.cash_after == D("100.00")


def test_minimum_ticket_enforced() -> None:
    decision = size_buy(
        ticker="VOO", sleeve="core", price=D("686.90"),
        target_dollars=D("9.99"), state=make_state(), cfg=CFG,
    )
    assert not decision.approved
    assert MIN_TICKET_USD == D("10")


def test_fractional_untradable_rejected() -> None:
    decision = size_buy(
        ticker="BRK.A", sleeve="core", price=D("780000"),
        target_dollars=D("100"), state=make_state(), cfg=CFG, fractional_tradable=False,
    )
    assert not decision.approved


# --- invariant sweep: nothing size_buy approves can violate the rules --------


def test_invariant_no_approved_decision_violates_caps() -> None:
    state = make_state(
        positions=[
            pos("VOO", "core", "300"), pos("NVDA", "satellite_ai_semis", "120"),
            pos("GLDM", "diversifier", "60"),
        ],
        settled="520",
    )  # total 1000
    total = state.total_value
    for ticker, sleeve in [
        ("NVDA", "satellite_ai_semis"), ("ANET", "satellite_ai_semis"), ("XLV", "diversifier"),
        ("IBIT", "speculative"), ("RSP", "core"), ("ZZZZ", "speculative"),
    ]:
        for target in ("15", "50", "120", "400", "5000"):
            d = size_buy(
                ticker=ticker, sleeve=sleeve, price=D("97.31"),
                target_dollars=D(target), state=state, cfg=CFG,
            )
            if not d.approved:
                continue
            assert d.dollar_amount <= state.cash.settled, (ticker, target)
            assert d.dollar_amount >= MIN_TICKET_USD
            if d.is_fractional:
                assert d.order_type is OrderType.MARKET and d.limit_price is None
            held = state.position_weight(ticker) * total
            assert held + d.dollar_amount <= CFG.risk_decimal("max_single_position_pct") * total + D("0.01")
            sleeve_now = state.sleeve_weights().get(sleeve, D(0)) * total
            band_hi = D(str(CFG.sleeves[sleeve].target_pct[1]))
            assert sleeve_now + d.dollar_amount <= band_hi * total + D("0.01")


# --- drift -------------------------------------------------------------------


def test_drift_report_flags_bands() -> None:
    state = make_state(
        positions=[pos("VOO", "core", "700"), pos("NVDA", "satellite_ai_semis", "100")],
        settled="200",
    )  # total 1000: core 70% (above 60%), satellite 10% (below 20%), others 0
    drift = {d.sleeve: d for d in compute_drift(state, CFG)}
    assert drift["core"].status is BandStatus.ABOVE
    assert drift["core"].delta_to_band == D("-100.00")  # trim $100 to re-enter band
    assert drift["satellite_ai_semis"].status is BandStatus.BELOW
    assert drift["satellite_ai_semis"].delta_to_band == D("100.00")
    assert drift["speculative"].status is BandStatus.IN_BAND  # band starts at 0
