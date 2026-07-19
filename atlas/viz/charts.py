"""Server-side chart payload builders (§9). Pure functions over DB rows."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.models import Score, Snapshot


def performance_series(session: Session, limit_days: int | None = None) -> list[dict[str, Any]]:
    """Cumulative return %, Atlas paper book vs SPY, from daily snapshots."""
    snaps = session.scalars(select(Snapshot).order_by(Snapshot.taken_at)).all()
    daily: dict[str, Snapshot] = {}
    for s in snaps:
        daily[s.taken_at.date().isoformat()] = s  # last snapshot per day wins
    rows = sorted(daily.items())
    if limit_days:
        rows = rows[-limit_days:]
    if not rows:
        return []
    base_val = rows[0][1].total_value
    base_spy = rows[0][1].spy_close
    series = []
    for day, s in rows:
        atlas_ret = float((s.total_value - base_val) / base_val * 100) if base_val else 0.0
        spy_ret = (
            float((s.spy_close - base_spy) / base_spy * 100)
            if base_spy and s.spy_close
            else None
        )
        series.append({"date": day, "atlas": round(atlas_ret, 3), "spy": round(spy_ret, 3) if spy_ret is not None else None})
    return series


def drawdown_series(session: Session) -> list[dict[str, Any]]:
    snaps = session.scalars(select(Snapshot).order_by(Snapshot.taken_at)).all()
    peak = Decimal(0)
    out = []
    seen: dict[str, Snapshot] = {}
    for s in snaps:
        seen[s.taken_at.date().isoformat()] = s
    for day, s in sorted(seen.items()):
        peak = max(peak, s.total_value)
        dd = float((s.total_value - peak) / peak * 100) if peak else 0.0
        out.append({"date": day, "drawdown": round(dd, 3)})
    return out


def alpha_histogram(session: Session, bin_width: float = 1.0) -> list[dict[str, Any]]:
    """Win/loss distribution of per-score alpha for the Track Record view."""
    scores = session.scalars(select(Score)).all()
    if not scores:
        return []
    bins: dict[int, int] = {}
    for s in scores:
        b = int(float(s.alpha_pct) // bin_width)
        bins[b] = bins.get(b, 0) + 1
    return [
        {"bin": f"{b * bin_width:+.0f}–{(b + 1) * bin_width:+.0f}%", "lo": b * bin_width, "count": n}
        for b, n in sorted(bins.items())
    ]
