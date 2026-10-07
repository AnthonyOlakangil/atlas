"""Task B [high]: an unaddressed band breach is a detected condition, not silence.

The sector prompt says "if ANY sleeve is outside its target band you MUST return
at least one trade_idea that moves it toward the band". A prompt instruction is a
request, not an invariant: the model complied for weeks and then didn't, and
nothing noticed. The 2026-08-16 brief showed satellite_ai_semis at 25.98% against
a 20–25% band and proposed nothing, with no explanation.

Same shape as the sleeve-validation bug — a check that only works when the input
happens to be well-formed. Enforce after the agent returns.
"""

from __future__ import annotations

from decimal import Decimal

from atlas.core.settings import OperatorConfig
from atlas.portfolio.drift import BandStatus
from atlas.research.agents.bands import (
    UnaddressedBreach,
    breach_anomalies,
    find_unaddressed_breaches,
)

CFG = OperatorConfig.load()


class _Drift:
    def __init__(self, sleeve: str, actual: str, lo: str, hi: str, status: BandStatus) -> None:
        self.sleeve, self.status = sleeve, status
        self.actual_pct = Decimal(actual)
        self.band_lo, self.band_hi = Decimal(lo), Decimal(hi)
        self.delta_to_band = Decimal("-44.25")


class _Idea:
    def __init__(self, sleeve: str, action: str) -> None:
        self.sleeve, self.action = sleeve, action
        self.ticker = "X"


OVER = _Drift("satellite_ai_semis", "0.2598", "0.20", "0.25", BandStatus.ABOVE)
UNDER = _Drift("core", "0.1945", "0.50", "0.60", BandStatus.BELOW)
IN_BAND = _Drift("speculative", "0.0", "0.0", "0.05", BandStatus.IN_BAND)


def test_over_band_sleeve_with_no_ideas_at_all_is_a_breach() -> None:
    """The exact 2026-08-16 failure: 25.98% above band, "No trades today"."""
    breaches = find_unaddressed_breaches([OVER], [])
    assert [b.sleeve for b in breaches] == ["satellite_ai_semis"]
    assert breaches[0].status == "above"


def test_a_trim_on_the_over_band_sleeve_addresses_it() -> None:
    ideas = [_Idea("satellite_ai_semis", "TRIM")]
    assert find_unaddressed_breaches([OVER], ideas) == []


def test_a_buy_on_an_over_band_sleeve_does_not_address_it() -> None:
    """Buying more of what is already overweight moves it further out of band."""
    ideas = [_Idea("satellite_ai_semis", "BUY")]
    assert len(find_unaddressed_breaches([OVER], ideas)) == 1


def test_a_buy_addresses_an_under_band_sleeve() -> None:
    assert find_unaddressed_breaches([UNDER], [_Idea("core", "BUY")]) == []


def test_a_trim_does_not_address_an_under_band_sleeve() -> None:
    assert len(find_unaddressed_breaches([UNDER], [_Idea("core", "TRIM")])) == 1


def test_an_idea_for_a_different_sleeve_does_not_address_the_breach() -> None:
    assert len(find_unaddressed_breaches([OVER], [_Idea("core", "BUY")])) == 1


def test_in_band_sleeves_are_never_breaches() -> None:
    assert find_unaddressed_breaches([IN_BAND], []) == []


def test_hold_and_watch_do_not_count_as_remediation() -> None:
    """A WATCH is not a trade; the sleeve stays out of band either way."""
    for action in ("HOLD", "WATCH"):
        assert len(find_unaddressed_breaches([OVER], [_Idea("satellite_ai_semis", action)])) == 1


def test_multiple_breaches_are_all_reported() -> None:
    assert len(find_unaddressed_breaches([OVER, UNDER], [])) == 2


def test_anomaly_names_sleeve_percentage_band_and_the_absence_of_remediation() -> None:
    notes = breach_anomalies(find_unaddressed_breaches([OVER], []))
    assert len(notes) == 1
    note = notes[0]
    assert "satellite_ai_semis" in note
    assert "25.98%" in note or "26.0%" in note
    assert "20%" in note and "25%" in note
    assert "no remediation" in note.lower()
    assert note.startswith("[high]"), "severity must be visible in §9"


def test_breach_record_carries_what_section_7_needs_to_explain_itself() -> None:
    b = find_unaddressed_breaches([OVER], [])[0]
    assert isinstance(b, UnaddressedBreach)
    assert b.actual_pct == Decimal("0.2598")
    assert (b.band_lo, b.band_hi) == (Decimal("0.20"), Decimal("0.25"))
