"""Allocation drift vs. the operator's sleeve bands — bands, never ticker lists (§2.0)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from atlas.core.settings import OperatorConfig
from atlas.portfolio.state import PortfolioState


class BandStatus(StrEnum):
    BELOW = "below"
    IN_BAND = "in_band"
    ABOVE = "above"


@dataclass(frozen=True)
class SleeveDrift:
    sleeve: str
    actual_pct: Decimal
    band_lo: Decimal
    band_hi: Decimal
    status: BandStatus
    delta_to_band: Decimal  # dollars to move to re-enter the band (signed; 0 if in band)


def compute_drift(state: PortfolioState, cfg: OperatorConfig) -> list[SleeveDrift]:
    total = state.total_value
    weights = state.sleeve_weights()
    out: list[SleeveDrift] = []
    for name, sleeve in cfg.sleeves.items():
        actual = weights.get(name, Decimal(0))
        lo = Decimal(str(sleeve.target_pct[0]))
        hi = Decimal(str(sleeve.target_pct[1]))
        if actual < lo:
            status, delta = BandStatus.BELOW, ((lo - actual) * total)
        elif actual > hi:
            status, delta = BandStatus.ABOVE, -((actual - hi) * total)
        else:
            status, delta = BandStatus.IN_BAND, Decimal(0)
        out.append(
            SleeveDrift(
                sleeve=name,
                actual_pct=actual.quantize(Decimal("0.0001")),
                band_lo=lo,
                band_hi=hi,
                status=status,
                delta_to_band=delta.quantize(Decimal("0.01")),
            )
        )
    return out
