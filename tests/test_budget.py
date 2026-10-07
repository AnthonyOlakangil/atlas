"""Levers 2 and 3: know the budget before spending it, and never starve the critic.

Free tier is 20 requests/day per project per model (quotaValue confirmed live).
A cycle costs 7 — six analysts plus synthesizer plus critic, less any gated by
empty input. Twice now, six analysts succeeded and the critic starved, shipping a
brief stamped PASS that was never adversarially reviewed.

The counter is derived from usage.json, which logs only SUCCESSFUL calls. Failed
calls still consume Google's quota, so the local count systematically
under-reports. Reconciliation is the normal path, not an edge case.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.core.budget import (
    CRITIC_RESERVE,
    FREE_TIER_RPD,
    BudgetExhaustedError,
    budget_status,
    preflight,
    record_quota_exhaustion,
    requests_today,
)

# 2026-08-16 09:00 Pacific == 16:00 UTC
NOW = datetime(2026, 8, 16, 16, 0, tzinfo=UTC)


def _usage(tmp_path: Path, *iso_timestamps: str) -> Path:
    p = tmp_path / "usage.json"
    p.write_text(json.dumps([
        {"ts": ts, "model": "gemini-3.5-flash", "agent": "x",
         "prompt_tokens": 1, "completion_tokens": 1, "attempt": "first"}
        for ts in iso_timestamps
    ]))
    return p


def test_counts_only_calls_since_midnight_pacific(tmp_path: Path) -> None:
    """The reset boundary is midnight Pacific, stated explicitly — never inherited
    from the system locale, which would silently shift the boundary on a laptop
    that travels."""
    path = _usage(
        tmp_path,
        "2026-08-16T06:59:00+00:00",  # 23:59 Pacific on the 15th — previous day
        "2026-08-16T07:01:00+00:00",  # 00:01 Pacific on the 16th — counts
        "2026-08-16T16:00:00+00:00",  # 09:00 Pacific — counts
    )
    assert requests_today(path, now=NOW) == 2


def test_missing_usage_file_counts_zero_rather_than_crashing(tmp_path: Path) -> None:
    assert requests_today(tmp_path / "nope.json", now=NOW) == 0


def test_corrupt_usage_file_counts_zero(tmp_path: Path) -> None:
    p = tmp_path / "usage.json"
    p.write_text("{ not json")
    assert requests_today(p, now=NOW) == 0


def test_status_reserves_the_critic_slot_off_the_top(tmp_path: Path) -> None:
    path = _usage(tmp_path, *["2026-08-16T16:00:00+00:00"] * 5)
    s = budget_status(path, now=NOW)
    assert s.spent_today == 5
    assert s.limit == FREE_TIER_RPD
    assert s.remaining == 15
    # The critic is allocated first and spent last, so agents may never see it.
    assert s.available_for_agents == 15 - CRITIC_RESERVE


def test_preflight_passes_when_the_whole_cycle_fits(tmp_path: Path) -> None:
    path = _usage(tmp_path, *["2026-08-16T16:00:00+00:00"] * 5)
    preflight(6, usage_path=path, now=NOW)  # 6 agents + reserved critic = 7 <= 15


def test_preflight_refuses_a_cycle_it_cannot_finish(tmp_path: Path) -> None:
    """The exact failure seen twice: enough budget to start, not enough to review."""
    path = _usage(tmp_path, *["2026-08-16T16:00:00+00:00"] * 16)  # 4 left
    with pytest.raises(BudgetExhaustedError) as exc:
        preflight(6, usage_path=path, now=NOW)
    msg = str(exc.value)
    assert "4" in msg, "name the remaining budget"
    assert "7" in msg, "name what the cycle needs"
    assert "midnight" in msg.lower() and "pacific" in msg.lower()


def test_preflight_refuses_when_only_the_critic_slot_remains(tmp_path: Path) -> None:
    path = _usage(tmp_path, *["2026-08-16T16:00:00+00:00"] * 19)
    with pytest.raises(BudgetExhaustedError):
        preflight(1, usage_path=path, now=NOW)


def test_recorded_quota_exhaustion_overrides_an_optimistic_local_count(
    tmp_path: Path,
) -> None:
    """Reconciliation. usage.json logs only successes, so after failures the local
    count reads low. A live RPD 429 is ground truth: it means the quota is gone
    regardless of what the counter believes."""
    path = _usage(tmp_path, *["2026-08-16T16:00:00+00:00"] * 3)  # local says 17 left
    record_quota_exhaustion(path, now=NOW, limit=FREE_TIER_RPD)
    s = budget_status(path, now=NOW)
    assert s.remaining == 0
    assert s.reconciled, "the discrepancy must be visible, not silently absorbed"
    with pytest.raises(BudgetExhaustedError, match="reconcil"):
        preflight(1, usage_path=path, now=NOW)


def test_exhaustion_marker_expires_at_the_next_pacific_midnight(tmp_path: Path) -> None:
    path = _usage(tmp_path)
    record_quota_exhaustion(path, now=NOW, limit=FREE_TIER_RPD)
    tomorrow = datetime(2026, 8, 17, 16, 0, tzinfo=UTC)
    s = budget_status(path, now=tomorrow)
    assert s.remaining == FREE_TIER_RPD
    assert not s.reconciled


def test_status_reports_the_reset_time_in_pacific(tmp_path: Path) -> None:
    s = budget_status(_usage(tmp_path), now=NOW)
    assert s.resets_at.tzinfo is not None
    assert s.resets_at.strftime("%H:%M") == "00:00"
    assert s.resets_at.date() == datetime(2026, 8, 17).date()


# --- Lever 3: what gets dropped when the budget is tight ----------------------

from atlas.core.budget import ESSENTIAL_AGENTS, plan_cycle  # noqa: E402


def test_a_full_budget_runs_every_candidate() -> None:
    plan = plan_cycle(["market-data", "news-analyst", "macro-analyst", "sector-analyst"],
                      available=10)
    assert plan.dropped == []
    assert len(plan.to_run) == 4


def test_a_tight_budget_drops_the_least_valuable_analyst_first() -> None:
    """Sentiment before macro before news before market-data. Never sector."""
    plan = plan_cycle(
        ["market-data", "news-analyst", "sentiment-analyst", "macro-analyst", "sector-analyst"],
        available=4,
    )
    assert plan.dropped == ["sentiment-analyst"]
    assert "sector-analyst" in plan.to_run


def test_the_critic_is_never_a_drop_candidate() -> None:
    plan = plan_cycle(
        ["market-data", "news-analyst", "sentiment-analyst", "macro-analyst", "sector-analyst"],
        available=2,
    )
    assert "critic" not in plan.dropped
    # sector + synthesizer survive because they are essential
    assert set(ESSENTIAL_AGENTS).issubset(set(plan.to_run) | {"synthesizer"})


def test_essential_agents_survive_even_a_minimal_budget() -> None:
    plan = plan_cycle(["market-data", "sector-analyst"], available=1)
    assert "sector-analyst" in plan.to_run
    assert plan.dropped == ["market-data"]


def test_every_drop_is_reported_with_its_reason() -> None:
    plan = plan_cycle(
        ["market-data", "news-analyst", "sentiment-analyst", "macro-analyst", "sector-analyst"],
        available=3,
    )
    assert plan.anomalies, "a dropped analyst must be visible in §9"
    for note in plan.anomalies:
        assert "budget" in note.lower()
        assert any(d in note for d in plan.dropped)
