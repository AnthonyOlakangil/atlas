"""Daily request budget for the free tier (Levers 2 and 3).

Gemini's free tier allows **20 requests per day, per project, per model** —
`quotaValue: "20"` for `gemini-3.5-flash`, confirmed against the live API. A full
cycle costs 7 (five analysts, synthesizer, critic), so the day holds two complete
cycles, or three once empty-input agents are gated.

Two failures motivated this module:

* **The critic starved twice.** Six analysts spent the day's allowance and the
  critic — which runs last — got a 429. The brief still stamped PASS. So the
  critic's request is *reserved off the top*: allocated first, spent last.
* **A cycle that cannot finish should never start.** Better to refuse loudly at
  06:30 than to hand over a brief that silently skipped its review.

The counter derives from `usage.json`, which logs only **successful** calls.
Failed calls still consume Google's quota, so the local count under-reports by
construction. Reconciliation against a live RPD 429 is therefore the normal path,
not an edge case — `record_quota_exhaustion` is ground truth and overrides the
optimistic local view for the rest of the Pacific day.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from atlas.core.logging import get_logger

log = get_logger("core.budget")

# §12 is Pacific throughout. Stated explicitly rather than inherited from the
# system locale, which would shift the reset boundary on a travelling laptop.
PACIFIC = ZoneInfo("America/Los_Angeles")

FREE_TIER_RPD = 20

# The critic is the guarantee that a brief was adversarially reviewed. It is the
# last call in a cycle and therefore the first to starve, so it gets its slot
# allocated before any analyst can claim one.
CRITIC_RESERVE = 1

_EXHAUSTED_MARKER = "__quota_exhausted__"


class BudgetExhaustedError(RuntimeError):
    """Not enough daily requests remain to complete a cycle."""


@dataclass(frozen=True)
class BudgetStatus:
    spent_today: int
    limit: int
    remaining: int
    available_for_agents: int
    resets_at: datetime
    reconciled: bool = False


def _pacific_day_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(PACIFIC).date(), time.min, tzinfo=PACIFIC)


def _entries(usage_path: Path) -> list[dict[str, object]]:
    try:
        data = json.loads(usage_path.read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _since_midnight(entries: list[dict[str, object]], now: datetime) -> list[dict[str, object]]:
    start = _pacific_day_start(now)
    out = []
    for e in entries:
        try:
            ts = datetime.fromisoformat(str(e.get("ts", "")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=PACIFIC)
        if ts >= start:
            out.append(e)
    return out


def requests_today(usage_path: Path, *, now: datetime | None = None) -> int:
    """Successful requests logged since midnight Pacific.

    A missing or corrupt log counts zero rather than raising: the budget check
    must never be the thing that breaks a cycle.
    """
    now = now or datetime.now(PACIFIC)
    today = _since_midnight(_entries(usage_path), now)
    return sum(1 for e in today if e.get("agent") != _EXHAUSTED_MARKER)


def record_quota_exhaustion(usage_path: Path, *, now: datetime, limit: int) -> None:
    """Persist ground truth: the API says the daily quota is gone.

    Called when a live 429 carries a PerDay quotaId. This outranks the local
    counter for the remainder of the Pacific day, and the discrepancy is logged
    rather than silently absorbed.
    """
    entries = _entries(usage_path)
    observed = requests_today(usage_path, now=now)
    log.warning(
        "daily quota exhausted per the API — local counter disagreed",
        extra={"data": {"local_count": observed, "limit": limit,
                        "undercount": max(0, limit - observed)}},
    )
    entries.append({
        "ts": now.isoformat(), "agent": _EXHAUSTED_MARKER,
        "model": "", "prompt_tokens": 0, "completion_tokens": 0, "attempt": "n/a",
        "limit": limit,
    })
    try:
        usage_path.parent.mkdir(parents=True, exist_ok=True)
        usage_path.write_text(json.dumps(entries, indent=1))
    except OSError as exc:  # pragma: no cover - disk failure must not kill the cycle
        log.warning(f"could not persist quota-exhaustion marker: {exc}")


def _exhausted_today(usage_path: Path, now: datetime) -> bool:
    return any(
        e.get("agent") == _EXHAUSTED_MARKER
        for e in _since_midnight(_entries(usage_path), now)
    )


def budget_status(usage_path: Path, *, now: datetime | None = None) -> BudgetStatus:
    now = now or datetime.now(PACIFIC)
    limit = FREE_TIER_RPD
    reconciled = _exhausted_today(usage_path, now)
    spent = limit if reconciled else requests_today(usage_path, now=now)
    remaining = max(0, limit - spent)
    return BudgetStatus(
        spent_today=spent,
        limit=limit,
        remaining=remaining,
        available_for_agents=max(0, remaining - CRITIC_RESERVE),
        resets_at=_pacific_day_start(now) + timedelta(days=1),
        reconciled=reconciled,
    )


def preflight(
    agent_requests: int, *, usage_path: Path, now: datetime | None = None
) -> BudgetStatus:
    """Refuse a cycle that cannot finish. Raises `BudgetExhaustedError` if so.

    `agent_requests` counts everything except the critic — the critic's slot is
    added here so it can never be the call that gets dropped.
    """
    status = budget_status(usage_path, now=now)
    needed = agent_requests + CRITIC_RESERVE
    if needed > status.remaining:
        detail = (
            " Local count was reconciled against a live quota error, so the API "
            "is the authority here, not usage.json."
            if status.reconciled
            else ""
        )
        raise BudgetExhaustedError(
            f"cycle needs {needed} request(s) ({agent_requests} agent(s) plus "
            f"{CRITIC_RESERVE} reserved for the critic) but only {status.remaining} "
            f"of the {status.limit}/day free-tier budget remain. Refusing to start a "
            "cycle that would starve its own review — a brief stamped PASS without "
            "adversarial review is worse than no brief. Budget resets at midnight "
            f"Pacific ({status.resets_at:%Y-%m-%d %H:%M %Z}).{detail}"
        )
    return status


# --- Lever 3: choosing what to drop when the budget is tight ------------------

# The sector analyst is the only agent that produces trade_ideas, so a cycle
# without it has nothing to propose and no reason to run. The synthesizer writes
# the brief. Neither is ever a drop candidate — nor is the critic, whose slot is
# reserved before any of this is considered.
ESSENTIAL_AGENTS = ("sector-analyst",)

# Drop order, least costly to lose first. Sentiment goes first because crowd
# positioning can never be a factual basis (§7.0) and is capped at 0.4
# confidence; market-data goes last because §2 reports every holding.
_DROP_ORDER = ("sentiment-analyst", "macro-analyst", "news-analyst", "market-data")


@dataclass(frozen=True)
class CyclePlan:
    to_run: list[str]
    dropped: list[str]
    anomalies: list[str]


def plan_cycle(candidates: list[str], *, available: int) -> CyclePlan:
    """Fit the agent list into `available` requests, dropping the cheapest first.

    `available` excludes the critic's reserved slot by construction — callers
    pass `BudgetStatus.available_for_agents` — so the review can never be the
    thing that gets cut. Essential agents survive a minimal budget; if even they
    do not fit, `preflight` will already have refused the cycle.
    """
    to_run = list(candidates)
    dropped: list[str] = []
    for agent in _DROP_ORDER:
        if len(to_run) <= available:
            break
        if agent in to_run and agent not in ESSENTIAL_AGENTS:
            to_run.remove(agent)
            dropped.append(agent)

    anomalies = [
        f"{a}: dropped — daily request budget too tight to run it this cycle "
        "(the critic's slot is reserved and never dropped)"
        for a in dropped
    ]
    return CyclePlan(to_run=to_run, dropped=dropped, anomalies=anomalies)
