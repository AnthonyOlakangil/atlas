"""Return math for the brief's performance lines (Bug 1, P0).

The rule: **day change is a value-weighted per-position price return, never a
total-vs-total diff.** A total-vs-total diff cannot distinguish a price move
from a deposit, a new position, or a wholesale composition change — which is how
the 2026-08-16 brief printed "+94.86%" after portfolio.yaml was replaced.

Two consequences fall out of computing it per-position:

* **Cash is structurally excluded.** Only positions enter the sum, so deposits
  and withdrawals cannot masquerade as return. Manual mode has no deposit
  ledger (portfolio.yaml just states `settled_cash`), so separating flows from
  returns is impossible at the total level — working per-position sidesteps that
  entirely rather than approximating it.
* **Share-count changes are immune.** The return is driven by price ratios
  weighted by prior value, so buying more of something you already hold is 0%,
  not +100%.

Anything we cannot compute honestly degrades loudly with the specific reason and
the specific tickers. A named gap beats a confident wrong number (§2.1).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

from pydantic import BaseModel

# A retail book of index funds and megacaps does not move this much in a day. If
# the math says it did, the math is broken — halt rather than print.
TRIPWIRE_PCT = Decimal("25")

_QUANT = Decimal("0.001")


class ImplausibleReturnError(Exception):
    """A computed return breached the sanity tripwire — the render must halt."""


class DayChange(BaseModel):
    """Either a real number, or an explicit reason there isn't one."""

    pct: Decimal | None = None
    reason: str | None = None
    added: list[str] = []
    removed: list[str] = []

    @property
    def available(self) -> bool:
        return self.pct is not None

    def render(self, prior_on: date | None) -> str:
        if self.pct is not None:
            since = f" since {prior_on.isoformat()}" if prior_on else ""
            return f"{self.pct:+.2f}%{since}"
        detail = self.reason or "unavailable"
        if self.added or self.removed:
            moves = []
            if self.added:
                moves.append(f"added {', '.join(self.added)}")
            if self.removed:
                moves.append(f"removed {', '.join(self.removed)}")
            detail = f"{detail} ({'; '.join(moves)})"
        since = f" since {prior_on.isoformat()}" if prior_on else ""
        return f"n/a — {detail}{since}"


class _HasPosition(Protocol):
    ticker: str
    shares: Decimal
    price: Decimal
    value: Decimal


def position_map(positions: Iterable[_HasPosition]) -> dict[str, object]:
    """The stored snapshot shape. Both the write site and the diff use this, so
    the two can never drift into disagreeing about the format."""
    return {
        p.ticker: {"shares": str(p.shares), "price": str(p.price), "value": str(p.value)}
        for p in positions
    }


def _priced(raw: object) -> tuple[Decimal, Decimal] | None:
    """(price, value) from a stored position, or None if the row predates prices.

    Pre-fix snapshots stored `{ticker: "748.87"}` — a bare value string with no
    price, which cannot yield a price return.
    """
    if not isinstance(raw, dict):
        return None
    try:
        price = Decimal(str(raw["price"]))
        value = Decimal(str(raw["value"]))
    except (KeyError, TypeError, InvalidOperation):
        return None
    return price, value


def compute_day_change(
    prior: dict[str, object] | None,
    current: dict[str, object],
    prior_on: date | None,
) -> DayChange:
    """Value-weighted price return between two stored position maps.

    Raises `ImplausibleReturnError` if the result breaches `TRIPWIRE_PCT`.
    """
    if prior is None:
        return DayChange(reason="first tracked day")
    if not prior:
        return DayChange(reason="prior snapshot recorded no positions")

    prior_keys, current_keys = set(prior), set(current)
    if prior_keys != current_keys:
        return DayChange(
            reason="portfolio composition changed",
            added=sorted(current_keys - prior_keys),
            removed=sorted(prior_keys - current_keys),
        )

    weighted = Decimal(0)
    base = Decimal(0)
    for ticker in sorted(prior_keys):
        was, now = _priced(prior[ticker]), _priced(current[ticker])
        if was is None or now is None:
            return DayChange(
                reason="prior snapshot is an incompatible format (no per-position price)"
            )
        prior_price, prior_value = was
        current_price, _ = now
        if prior_price == 0:
            continue
        weighted += prior_value * (current_price / prior_price - 1)
        base += prior_value

    if base == 0:
        return DayChange(reason="prior snapshot holds no priced value")

    pct = (weighted / base * 100).quantize(_QUANT)
    if abs(pct) > TRIPWIRE_PCT:
        raise ImplausibleReturnError(
            f"computed day change {pct:+.2f}% exceeds the ±{TRIPWIRE_PCT}% sanity "
            "tripwire — refusing to render. The snapshot history is probably "
            "inconsistent with the current book."
        )
    return DayChange(pct=pct)
