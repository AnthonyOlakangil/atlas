"""The single presentation layer for numbers (Bug 2, P0).

Two rules, both from §2.1:

* **Round at the boundary only.** Every function here takes the full-precision
  value and rounds once, at render time. Nothing upstream rounds; nothing
  downstream re-rounds.
* **Never round away the operator's actual fills.** Share counts carry 6dp and
  money carries cents, and a held position never renders as zero.

Binary floats cannot represent cents exactly — `713.6099853515625` is what a
float32 price looks like when interpolated raw — so every entry point coerces
through `str()` into `Decimal` before quantising.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_CENTS = Decimal("0.01")
_SHARES = Decimal("0.000001")

# Below this, a nonzero percentage would render as "0.00%" and a real holding
# would look like it isn't there.
_PCT_EPSILON = Decimal("0.01")


def _dec(value: Decimal | float | int | str) -> Decimal:
    """Coerce via str so float binary artifacts never survive into output."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value: Decimal | float | int | str) -> str:
    """`$1,234.56`, sign outside the symbol. ROUND_HALF_UP — banker's rounding
    would shave a cent off values ending in exactly 5."""
    amount = _dec(value).quantize(_CENTS, rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def pct(value: Decimal | float | int | str) -> str:
    """`26.01%`. A nonzero magnitude below 0.01% renders as `<0.01%` rather than
    `0.00%`, so a small real position is never displayed out of existence."""
    raw = _dec(value)
    if raw == 0:
        return "0.00%"
    if abs(raw) < _PCT_EPSILON:
        return "<0.01%" if raw > 0 else ">-0.01%"
    return f"{raw.quantize(_CENTS, rounding=ROUND_HALF_UP):.2f}%"


def shares(value: Decimal | float | int | str) -> str:
    """6dp, per §2.1 rule 8 — the operator's fill is never rounded away."""
    return f"{_dec(value).quantize(_SHARES, rounding=ROUND_HALF_UP):.6f}"


def bps(value: Decimal | float | int | str) -> str:
    """Basis points as integers. Input is a rate (0.0425 -> `4 bps`)."""
    return f"{int(_dec(value) * 100):d} bps"
