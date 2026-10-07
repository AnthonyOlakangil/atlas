"""The watchlist is derived from the book, never hardcoded.

`DEFAULT_WATCHLIST` was a fixed ten tickers. Holdings outside it were topped up
by a second `build_market_snapshot` call whose `.indicators` were computed and
then discarded — only `.quotes` and `.anomalies` merged back. Seven of twelve
holdings therefore had no RSI/MACD while Atlas paid the yfinance cost anyway,
and v2 §2 ("every holding, individually") was unachievable for them.

Two tiers, because they cost differently:
  * analysis  — quotes AND indicators (1y history per name). Every holding.
  * reference — quotes only (5d history). Research candidates and indices.
"""

from __future__ import annotations

from atlas.core.settings import OperatorConfig
from atlas.research.orchestrator import build_watchlist

CFG = OperatorConfig.load()
BOOK = ["VOO", "DIA", "SMH", "NVDA", "ANET", "AVGO", "MU", "XLV", "XLF", "NLR",
        "NFLX", "F", "BRK.B"]


def test_every_holding_is_in_the_analysis_tier() -> None:
    """The regression: no holding may be left without indicators."""
    wl = build_watchlist(BOOK, CFG)
    missing = [t for t in BOOK if t not in wl.analysis]
    assert not missing, f"holdings absent from the analysis tier: {missing}"


def test_previously_dropped_holdings_are_now_covered() -> None:
    wl = build_watchlist(BOOK, CFG)
    for ticker in ("ANET", "AVGO", "DIA", "F", "MU", "NFLX", "NLR"):
        assert ticker in wl.analysis


def test_benchmark_is_always_present_even_when_not_held() -> None:
    wl = build_watchlist(["VOO"], CFG)
    assert "SPY" in wl.analysis, "SPY is the benchmark every window is measured against"


def test_seed_examples_do_not_enter_the_fetch_universe() -> None:
    """Superseded an earlier assertion that seeds populated the reference tier.
    Task 2.5 draws the line differently and more correctly: `seed_examples` are
    illustrative sleeve starting points (§2.0) and never order data. The set the
    operator actually wants priced is `watchlist:` in operator.yaml. Coverage of
    the new contract lives in tests/test_universe.py."""
    wl = build_watchlist(BOOK, CFG)
    assert "GLDM" not in wl.analysis and "GLDM" not in wl.reference


def test_a_held_seed_stays_in_analysis_and_is_not_duplicated_into_reference() -> None:
    wl = build_watchlist(BOOK, CFG)
    assert "XLV" in wl.analysis  # held, and also a diversifier seed
    assert "XLV" not in wl.reference


def test_no_duplicates_across_or_within_tiers() -> None:
    wl = build_watchlist(BOOK + ["VOO", "voo"], CFG)
    assert len(wl.analysis) == len(set(wl.analysis))
    assert len(wl.reference) == len(set(wl.reference))
    assert not set(wl.analysis) & set(wl.reference)


def test_tickers_are_case_normalised() -> None:
    wl = build_watchlist(["voo", "smh"], CFG)
    assert "VOO" in wl.analysis and "voo" not in wl.analysis


def test_order_is_deterministic() -> None:
    assert build_watchlist(BOOK, CFG) == build_watchlist(BOOK, CFG)


def test_an_empty_book_still_yields_a_usable_watchlist() -> None:
    wl = build_watchlist([], CFG)
    assert "SPY" in wl.analysis
    assert wl.reference, "research candidates do not depend on holdings"


def test_off_seed_holding_is_covered_without_being_a_seed() -> None:
    """§2.0: the book drives coverage, not membership of any seed list. NLR and
    NFLX appear in no seed_examples list and must still be analysed."""
    seeds = {s for sl in CFG.sleeves.values() for s in sl.seed_examples}
    assert "NLR" not in seeds and "NFLX" not in seeds
    wl = build_watchlist(BOOK, CFG)
    assert "NLR" in wl.analysis and "NFLX" in wl.analysis
