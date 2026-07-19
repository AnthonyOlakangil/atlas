"""Live Gemini smoke test (§Phase 5 gate): key works, limiter holds, schema validates.

Opt-in:  uv run pytest -m live tests/test_llm_live.py
Requires GEMINI_API_KEY in .env (see RUNBOOK §1 — the operator adds it, never Claude).
"""

from __future__ import annotations

import pytest

from atlas.core.settings import get_settings

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not get_settings().gemini_api_key,
    reason="GEMINI_API_KEY not set in .env — operator adds it (RUNBOOK §1)",
)
def test_live_gemini_structured_output_and_limiter() -> None:
    from atlas.core.llm import FREE_TIER_RPM_CAP, GeminiClient, shared_limiter
    from atlas.research.agents.schemas import AnalystView

    limiter = shared_limiter()
    assert limiter._rate == FREE_TIER_RPM_CAP == 8  # ≤8/min discipline (§3.1)

    client = GeminiClient(api_key=get_settings().gemini_api_key, model=get_settings().llm_model)
    view = client.complete(
        [
            {"role": "system", "content": "You are a test analyst. Be brief."},
            {
                "role": "user",
                "content": (
                    'Return a minimal valid response with agent="live-smoke", one key finding '
                    'of kind "fact" citing source "test" retrieved_at "2026-07-18T00:00:00Z", '
                    "confidence 0.5."
                ),
            },
        ],
        AnalystView,
        agent="live-smoke",
    )
    assert view.agent == "live-smoke"
    assert 0 <= view.confidence <= 1
    assert view.key_findings and view.key_findings[0].kind in ("fact", "consensus", "speculation")
