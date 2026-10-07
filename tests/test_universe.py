"""Task 2.5: the fetch universe is computed, and holdings coverage is a contract.

    universe = holdings(portfolio.yaml) u watchlist(operator.yaml) u benchmarks

A position the operator owns can never be absent from the fetch. §2's "every
holding, individually" is enforced, not hoped for: an unquoted holding raises
rather than quietly omitting a line.

Note the distinction this encodes: `seed_examples` are illustrative starting
points (§2.0, explicitly not a whitelist) and are NOT a fetch instruction. The
candidate set the operator actually wants priced is `watchlist:`.
"""

from __future__ import annotations

import pytest

from atlas.core.settings import OperatorConfig
from atlas.ingest.market import MarketSnapshot, Quote
from atlas.research.orchestrator import (
    BENCHMARKS,
    HoldingsCoverageError,
    assert_holdings_quoted,
    build_watchlist,
)

CFG = OperatorConfig.load()
BOOK = ["VOO", "DIA", "SMH", "NVDA", "ANET", "AVGO", "MU", "XLV", "XLF", "NLR",
        "NFLX", "F", "BRK.B"]


def _snap(*symbols: str) -> MarketSnapshot:
    from datetime import UTC, datetime

    now = datetime(2026, 8, 16, tzinfo=UTC)
    snap = MarketSnapshot()
    for s in symbols:
        snap.quotes[s] = Quote(symbol=s, price=1.0, source="test", as_of=now)
    return snap


# --- universe composition -----------------------------------------------------


def test_a_new_ticker_in_portfolio_yaml_enters_the_universe_with_no_code_change() -> None:
    wl = build_watchlist([*BOOK, "RKLB"], CFG)
    assert "RKLB" in wl.analysis


def test_operator_watchlist_is_a_fetch_source() -> None:
    assert CFG.watchlist, "operator.yaml must declare a watchlist"
    wl = build_watchlist(BOOK, CFG)
    for ticker in CFG.watchlist:
        assert ticker in wl.analysis or ticker in wl.reference


def test_seed_examples_are_not_a_fetch_instruction() -> None:
    """TSM and IBIT are seed_examples but neither held nor watchlisted, so they
    must not be fetched. Seeds illustrate a sleeve; they do not order data."""
    wl = build_watchlist(BOOK, CFG)
    for ticker in ("TSM", "IBIT"):
        assert ticker not in wl.analysis
        assert ticker not in wl.reference


def test_benchmarks_are_present_and_include_rates_vol_gold_and_crypto() -> None:
    wl = build_watchlist(BOOK, CFG)
    everything = set(wl.analysis) | set(wl.reference)
    for symbol in ("SPY", "QQQ", "SOXX", "^VIX", "^TNX", "GLD", "BTC-USD"):
        assert symbol in everything, f"{symbol} missing from the universe"


def test_a_holding_that_is_also_a_benchmark_is_fetched_once() -> None:
    """DIA is both. It must not appear in both tiers."""
    assert "DIA" in BOOK and "DIA" in BENCHMARKS
    wl = build_watchlist(BOOK, CFG)
    assert "DIA" in wl.analysis
    assert "DIA" not in wl.reference
    assert len(wl.analysis) == len(set(wl.analysis))


# --- the hard gate ------------------------------------------------------------


def test_every_quoted_holding_passes_the_gate() -> None:
    assert_holdings_quoted(BOOK, _snap(*BOOK))  # must not raise


def test_an_unquoted_holding_blocks_the_render_and_names_every_missing_ticker() -> None:
    snap = _snap(*[t for t in BOOK if t not in ("ANET", "MU")])
    with pytest.raises(HoldingsCoverageError) as exc:
        assert_holdings_quoted(BOOK, snap)
    message = str(exc.value)
    assert "ANET" in message and "MU" in message
    assert "VOO" not in message, "only the missing ones are named"


def test_a_zero_price_counts_as_unquoted() -> None:
    """The BRK.B failure mode: a symbol present but priced 0 is a data failure,
    not a $0 asset, and must not satisfy the contract."""
    from datetime import UTC, datetime

    snap = _snap(*BOOK)
    snap.quotes["MU"] = Quote(symbol="MU", price=0.0, source="test",
                              as_of=datetime(2026, 8, 16, tzinfo=UTC))
    with pytest.raises(HoldingsCoverageError, match="MU"):
        assert_holdings_quoted(BOOK, snap)


def test_a_total_fetch_failure_aborts_rather_than_reporting_every_holding_missing() -> None:
    """An empty snapshot is a fetch outage, not thirteen individually dead
    symbols — the message must say so instead of listing the whole book."""
    with pytest.raises(HoldingsCoverageError) as exc:
        assert_holdings_quoted(BOOK, _snap())
    assert "total" in str(exc.value).lower() or "no quotes" in str(exc.value).lower()


def test_an_empty_book_trivially_passes() -> None:
    assert_holdings_quoted([], _snap())
