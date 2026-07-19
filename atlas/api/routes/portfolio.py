"""Portfolio Overview + Positions Table + Sector Heat data (§9 views 1, 3, 8)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from atlas.api.deps import QuoteFn, get_quotes, get_session
from atlas.core.settings import get_operator_config, get_settings
from atlas.portfolio.drift import compute_drift
from atlas.portfolio.state import load_portfolio_state

router = APIRouter(tags=["portfolio"])


@router.get("/meta")
def meta() -> dict[str, str]:
    s = get_settings()
    return {"broker_mode": s.broker_mode, "atlas_mode": s.atlas_mode}


@router.get("/portfolio")
def portfolio(
    session: Session = Depends(get_session), quotes: QuoteFn = Depends(get_quotes)
) -> dict[str, Any]:
    cfg = get_operator_config()
    now = datetime.now(UTC)
    state = load_portfolio_state(session, {}, now.date())
    prices = quotes([p.ticker for p in state.positions])
    state = load_portfolio_state(session, prices, now.date())

    drift = {
        d.sleeve: {
            "actual": float(d.actual_pct),
            "band": [float(d.band_lo), float(d.band_hi)],
            "status": d.status.value,
            "delta_to_band": float(d.delta_to_band),
        }
        for d in compute_drift(state, cfg)
    }
    positions = []
    for p in state.positions:
        pl = p.value - p.cost_basis
        positions.append(
            {
                "ticker": p.ticker,
                "sleeve": p.sleeve,
                "shares": float(p.shares),
                "cost_basis": float(p.cost_basis),
                "price": float(p.price),
                "value": float(p.value),
                "unrealized_pl": float(pl),
                "unrealized_pl_pct": float(pl / p.cost_basis * 100) if p.cost_basis else 0.0,
                "weight": float(state.position_weight(p.ticker)),
                "stale": p.price == Decimal(0),
            }
        )
    return {
        "as_of": now.isoformat(),
        "total_value": float(state.total_value),
        "settled_cash": float(state.cash.settled),
        "unsettled_cash": float(state.cash.unsettled),
        "gfv_count": state.ledger.gfv_count(),
        "sleeves": drift,
        "positions": positions,
    }
