"""APScheduler daily loop (§10 Phase 9). Lives OUTSIDE atlas/research so the
research package never imports execution (the stop monitor does).

Schedule (America/Los_Angeles):
- 06:30 Mon-Fri  pre-open research cycle → brief + tickets
- 13:15 Mon-Fri  post-close light update
- every 10 min 06:30-13:00 Mon-Fri  app-managed stop monitor (proposes exits)
- 13:45 Mon-Fri  outcome scoring (+1/+5/+20d vs SPY)
- 05:00 daily    stale-ticket expiry

⚠️ Stops are only enforced while this process runs (RUNBOOK §3).
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from atlas.core.calendar import is_trading_day
from atlas.core.db import session_scope
from atlas.core.logging import get_logger, setup_logging
from atlas.core.models import Ticket, TicketStatus

log = get_logger("scheduler")
PT = ZoneInfo("America/Los_Angeles")


def _guarded(name: str):  # noqa: ANN202 — decorator
    """Jobs never crash the scheduler; failures are logged and the loop survives."""

    def wrap(fn):  # noqa: ANN001, ANN202
        def inner() -> None:
            if not is_trading_day(datetime.now(PT).date()) and name != "expire_tickets":
                log.info(f"{name}: not a trading day — skipped")
                return
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — a broken cycle must not kill tomorrow's
                log.error(f"{name} failed: {exc}", extra={"data": {"job": name}})

        inner.__name__ = f"job_{name}"
        return inner

    return wrap


@_guarded("preopen_cycle")
def job_preopen_cycle() -> None:
    from atlas.research.orchestrator import run_daily_cycle

    run_daily_cycle(kind="preopen")


@_guarded("postclose_update")
def job_postclose_update() -> None:
    from atlas.research.orchestrator import run_daily_cycle

    run_daily_cycle(kind="postclose")


def _live_price_lookup(ticker: str, on: date) -> Decimal | None:
    """Close price for a ticker on a date, via yfinance (patchable seam)."""
    try:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(
            start=on.isoformat(), end=(on.replace(day=on.day)).isoformat(), period=None
        )
        if hist.empty:
            hist = yf.Ticker(ticker).history(period="1mo")
            hist = hist[hist.index.date <= on]  # type: ignore[attr-defined]
        if hist.empty:
            return None
        return Decimal(str(round(float(hist["Close"].iloc[-1]), 4)))
    except Exception:  # noqa: BLE001
        return None


@_guarded("stop_monitor")
def job_stop_monitor() -> None:
    from atlas.execution.tickets import check_stops
    from atlas.ingest.market import _yf_price

    with session_scope() as session:
        tickers = {
            t.ticker
            for t in session.scalars(
                select(Ticket).where(Ticket.status == TicketStatus.EXECUTED)
            )
            if t.exit_stop is not None or t.exit_target is not None
        }
        if not tickers:
            return
        prices: dict[str, Decimal] = {}
        for t in tickers:
            try:
                prices[t] = Decimal(str(_yf_price(t)))
            except Exception:  # noqa: BLE001 — missing quote just skips that ticker
                continue
        proposals = check_stops(session, prices)
        if proposals:
            log.info(f"stop monitor proposed {len(proposals)} exit ticket(s) — approval required")


@_guarded("scoring")
def job_scoring() -> None:
    from atlas.portfolio.scoring import score_due_tickets

    with session_scope() as session:
        n = score_due_tickets(session, datetime.now(UTC).date(), _live_price_lookup)
        if n:
            log.info(f"scored {n} ticket window(s)")


@_guarded("expire_tickets")
def job_expire_tickets() -> None:
    from atlas.execution.tickets import expire_stale_tickets

    with session_scope() as session:
        n = expire_stale_tickets(session)
        if n:
            log.info(f"expired {n} stale ticket(s)")


def build_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=PT)
    sched.add_job(job_preopen_cycle, CronTrigger(day_of_week="mon-fri", hour=6, minute=30, timezone=PT), id="preopen_cycle")
    sched.add_job(job_postclose_update, CronTrigger(day_of_week="mon-fri", hour=13, minute=15, timezone=PT), id="postclose_update")
    sched.add_job(job_stop_monitor, CronTrigger(day_of_week="mon-fri", hour="6-13", minute="*/10", timezone=PT), id="stop_monitor")
    sched.add_job(job_scoring, CronTrigger(day_of_week="mon-fri", hour=13, minute=45, timezone=PT), id="scoring")
    sched.add_job(job_expire_tickets, CronTrigger(hour=5, minute=0, timezone=PT), id="expire_tickets")
    return sched


def start_scheduler() -> None:
    setup_logging()
    sched = build_scheduler()
    sched.start()
    log.info("Atlas scheduler running (Ctrl-C to stop). Stops are only enforced while this runs.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        sched.shutdown()
        log.info("scheduler stopped — nothing is watching stop levels now")
