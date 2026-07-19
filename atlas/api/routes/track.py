"""Performance + Track Record chart data (§9 views 2, 7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from atlas.api.deps import get_session
from atlas.portfolio.scoring import track_record_stats
from atlas.viz.charts import alpha_histogram, drawdown_series, performance_series

router = APIRouter(tags=["track"])

WINDOW_DAYS = {"1D": 2, "1W": 7, "1M": 31, "3M": 93, "YTD": 366, "ALL": None}


@router.get("/performance")
def performance(window: str = "ALL", session: Session = Depends(get_session)) -> dict[str, Any]:
    days = WINDOW_DAYS.get(window.upper(), None)
    return {"window": window.upper(), "series": performance_series(session, days)}


@router.get("/track-record")
def track_record(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {
        "stats": track_record_stats(session),
        "alpha_histogram": alpha_histogram(session),
        "drawdown": drawdown_series(session),
    }
