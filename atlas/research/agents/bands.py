"""Band-breach enforcement in code, not in a prompt (Task B).

The sector analyst's prompt instructs it to propose remediation whenever a sleeve
sits outside its band. That instruction is a *request*: the model honoured it for
weeks, then silently stopped, and the 2026-08-16 brief reported
`satellite_ai_semis 25.98% ABOVE 20%–25%` alongside "No trades today" with no
explanation. Nothing in the system noticed.

This is the same defect shape as the sleeve-validation bug — a check that only
works when the input happens to be well-formed. So the invariant moves into code
and runs *after* the agent returns, where it cannot be argued away.

The rule is direction-aware: a BUY only remediates an underweight sleeve, and a
SELL/TRIM only remediates an overweight one. Buying more of something already
over its band moves it further out, so it does not count as addressing the
breach.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from atlas.portfolio.drift import BandStatus

# Actions that genuinely move a sleeve back toward its band, by direction.
_REMEDIES: dict[str, frozenset[str]] = {
    BandStatus.ABOVE.value: frozenset({"SELL", "TRIM"}),
    BandStatus.BELOW.value: frozenset({"BUY"}),
}


@dataclass(frozen=True)
class UnaddressedBreach:
    sleeve: str
    status: str
    actual_pct: Decimal
    band_lo: Decimal
    band_hi: Decimal


def find_unaddressed_breaches(
    drifts: list[Any], trade_ideas: list[Any]
) -> list[UnaddressedBreach]:
    """Sleeves outside their band that no proposed trade moves back toward it."""
    breaches: list[UnaddressedBreach] = []
    for d in drifts:
        status = d.status.value if hasattr(d.status, "value") else str(d.status)
        remedies = _REMEDIES.get(status)
        if remedies is None:  # in band
            continue
        addressed = any(
            getattr(i, "sleeve", None) == d.sleeve
            and str(getattr(i, "action", "")).upper() in remedies
            for i in trade_ideas
        )
        if not addressed:
            breaches.append(
                UnaddressedBreach(
                    sleeve=d.sleeve,
                    status=status,
                    actual_pct=d.actual_pct,
                    band_lo=d.band_lo,
                    band_hi=d.band_hi,
                )
            )
    return breaches


def breach_anomalies(breaches: list[UnaddressedBreach]) -> list[str]:
    """§9 lines. `[high]` because concentration is the number the operator relies
    on most, and an unremediated breach reported without comment under-states it."""
    return [
        f"[high] {b.sleeve} is {float(b.actual_pct):.2%} — {b.status} its "
        f"{float(b.band_lo):.0%}–{float(b.band_hi):.0%} band — and no remediation was "
        "proposed this cycle. Either a ticket should address it or §7 must state "
        "why inaction is correct despite the breach."
        for b in breaches
    ]


def breach_explanation(breaches: list[UnaddressedBreach]) -> str:
    """§7 text. "No trades today" is a legitimate output, but not while a stated
    band breach goes unaddressed with no reasoning — silence is not an option."""
    if not breaches:
        return ""
    lines = [
        "\n\n**⚠️ Unaddressed band breach.** The following sleeve(s) sit outside "
        "their target band and no proposed trade moves them back toward it. No "
        "reasoning for inaction was supplied by the research pass, so this gap is "
        "reported rather than hidden:"
    ]
    lines += [
        f"- **{b.sleeve}**: {float(b.actual_pct):.2%} vs "
        f"{float(b.band_lo):.0%}–{float(b.band_hi):.0%} ({b.status} band)"
        for b in breaches
    ]
    return "\n".join(lines)
