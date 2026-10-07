"""Class-share tickers must reach Yahoo in Yahoo's dialect.

BRK.B silently resolved to price 0 — Yahoo spells it BRK-B. A zero price is far
worse than a missing one: the position stays in the book, contributes $0 to
total value, and renders as 0.0% of the portfolio, so it is invisible in the
sleeve table while still being held.
"""

from __future__ import annotations

from atlas.ingest.symbols import to_yahoo


def test_class_share_dot_becomes_a_hyphen() -> None:
    assert to_yahoo("BRK.B") == "BRK-B"
    assert to_yahoo("BF.B") == "BF-B"


def test_ordinary_tickers_are_untouched() -> None:
    for t in ("VOO", "NVDA", "SMH", "F", "ANET"):
        assert to_yahoo(t) == t


def test_already_hyphenated_is_left_alone() -> None:
    assert to_yahoo("BRK-B") == "BRK-B"


def test_index_carets_are_preserved() -> None:
    """^TNX (10Y yield) must not be mangled."""
    assert to_yahoo("^TNX") == "^TNX"


def test_case_is_normalised_upward() -> None:
    assert to_yahoo("brk.b") == "BRK-B"
