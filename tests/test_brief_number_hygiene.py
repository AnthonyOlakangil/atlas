"""Bug 2 (P0), the catch-all: scan a fully rendered brief and reject float artifacts.

Unit tests on the formatter only prove the formatter works. This scans the
finished document, so a renderer that bypasses `atlas.core.format` and
interpolates a raw float is caught here no matter when it is added.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

BRIEFS = Path(__file__).resolve().parent.parent / "briefs"

# "$713.6099853515625" — a currency figure carrying more than two decimals.
OVERPRECISE_MONEY = re.compile(r"\$-?\d[\d,]*\.\d{3,}")

# A bare float artifact anywhere: 10+ decimal places is never a deliberate figure.
FLOAT_ARTIFACT = re.compile(r"\d\.\d{10,}")


# atlas/core/format.py landed on 2026-08-16. Briefs dated before it were rendered
# by code that interpolated raw floats, so they will always carry artifacts. They
# are exempt rather than deleted: each one is a record of what Atlas actually
# produced at that stage, and deleting history to make a test pass would destroy
# the evidence the fix was needed. The cutoff moves only if the formatter changes.
FORMATTER_LANDED = date(2026, 8, 16)


def _brief_date(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None  # not a dated brief (e.g. the reference file)


def _generated_briefs() -> list[Path]:
    """Atlas output dated on or after the formatter landed.

    The hand-written reference brief is the input quality bar, not a rendered
    artifact, so renderer rules do not apply to it.
    """
    briefs = []
    for path in sorted(BRIEFS.glob("*.md")):
        if path.name.startswith("reference-"):
            continue
        on = _brief_date(path)
        if on is None or on < FORMATTER_LANDED:
            continue
        briefs.append(path)
    return briefs


@pytest.mark.parametrize("brief", _generated_briefs(), ids=lambda p: p.name)
def test_no_currency_figure_carries_more_than_two_decimals(brief: Path) -> None:
    hits = OVERPRECISE_MONEY.findall(brief.read_text())
    assert not hits, f"{brief.name}: over-precise currency {sorted(set(hits))[:5]}"


@pytest.mark.parametrize("brief", _generated_briefs(), ids=lambda p: p.name)
def test_no_raw_float_artifacts_anywhere_in_the_document(brief: Path) -> None:
    hits = FLOAT_ARTIFACT.findall(brief.read_text())
    assert not hits, f"{brief.name}: raw float artifact {sorted(set(hits))[:5]}"
