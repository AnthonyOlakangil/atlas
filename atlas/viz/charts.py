"""Server-side chart payload builders (§9). Pure functions over DB rows."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.models import Score, Snapshot
from atlas.portfolio.performance import ImplausibleReturnError, compute_day_change


def _daily_rows(session: Session) -> list[tuple[str, Snapshot]]:
    daily: dict[str, Snapshot] = {}
    for s in session.scalars(select(Snapshot).order_by(Snapshot.taken_at)):
        daily[s.taken_at.date().isoformat()] = s  # last snapshot per day wins
    return sorted(daily.items())


def _chained_index(rows: list[tuple[str, Snapshot]]) -> list[tuple[str, Decimal | None, str]]:
    """Chain per-period position returns into a cumulative index.

    Each step is a value-weighted price return (`compute_day_change`), so cash
    flows and composition changes cannot leak in as performance. A period we
    cannot compute honestly yields `None` and restarts the chain from the new
    composition — carrying a cumulative figure across an unknown period would be
    exactly the confident-wrong-number failure this replaces.
    """
    out: list[tuple[str, Decimal | None, str]] = []
    index = Decimal(1)
    prev: Snapshot | None = None
    for day, snap in rows:
        if prev is None:
            out.append((day, Decimal(0), ""))
            prev = snap
            continue
        try:
            change = compute_day_change(prev.positions, snap.positions, prev.taken_at.date())
        except ImplausibleReturnError as exc:
            out.append((day, None, f"tripwire: {exc}"))
            index = Decimal(1)  # rebase; the period is not trustworthy
            prev = snap
            continue
        if change.available and change.pct is not None:
            index *= 1 + change.pct / 100
            out.append((day, (index - 1) * 100, ""))
        else:
            note = change.reason or "unavailable"
            if change.added or change.removed:
                bits = []
                if change.added:
                    bits.append(f"added {', '.join(change.added)}")
                if change.removed:
                    bits.append(f"removed {', '.join(change.removed)}")
                note = f"{note} ({'; '.join(bits)})"
            out.append((day, None, note))
            index = Decimal(1)  # rebase after the gap
        prev = snap
    return out


def performance_series(session: Session, limit_days: int | None = None) -> list[dict[str, Any]]:
    """Cumulative return %, Atlas book vs SPY, chained from daily snapshots.

    Never total-vs-total: `total_value` includes cash, so a deposit or a
    composition change would read as performance (Task 1b).
    """
    rows = _daily_rows(session)
    if limit_days:
        rows = rows[-limit_days:]
    if not rows:
        return []

    base_spy = rows[0][1].spy_close
    series: list[dict[str, Any]] = []
    for (day, atlas_ret, note), (_, snap) in zip(_chained_index(rows), rows, strict=True):
        # SPY is a single instrument with no composition or cash, so a direct
        # price ratio is legitimate here in a way it never is for the book.
        spy_ret = (
            float((snap.spy_close - base_spy) / base_spy * 100)
            if base_spy and snap.spy_close
            else None
        )
        row: dict[str, Any] = {
            "date": day,
            "atlas": round(float(atlas_ret), 3) if atlas_ret is not None else None,
            "spy": round(spy_ret, 3) if spy_ret is not None else None,
        }
        if note:
            row["note"] = note
        series.append(row)
    return series


def drawdown_series(session: Session) -> list[dict[str, Any]]:
    """Drawdown off the chained return index, so withdrawals are not drawdowns."""
    rows = _daily_rows(session)
    if not rows:
        return []
    peak = Decimal(1)
    out: list[dict[str, Any]] = []
    for day, ret, note in _chained_index(rows):
        if ret is None:
            entry: dict[str, Any] = {"date": day, "drawdown": None}
            if note:
                entry["note"] = note
            out.append(entry)
            peak = Decimal(1)
            continue
        index = 1 + ret / 100
        peak = max(peak, index)
        dd = (index - peak) / peak * 100 if peak else Decimal(0)
        out.append({"date": day, "drawdown": round(float(dd), 3)})
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
