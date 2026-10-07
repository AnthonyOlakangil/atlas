"""Task C: one mechanism for "this brief was built on less than full research".

A brief stamped APPROVED / critic PASS asserts that something adversarial
reviewed it. Twice the critic's LLM layer failed, fell back to structural checks,
and still reported PASS — indistinguishable from a real review. That is a false
guarantee, and unlike a thin section it is invisible.

The cause varies — a dead agent, an exhausted daily quota, an input-gated skip, a
budget-dropped analyst, a starved critic — but the consequence for the reader is
identical, so all of them route through one type and surface the same way.
"""

from __future__ import annotations

from atlas.research.integrity import UNREVIEWED_LABEL, ResearchIntegrity


def test_a_clean_cycle_is_complete() -> None:
    assert ResearchIntegrity().complete
    assert ResearchIntegrity().tldr_banner() is None


def test_an_unreviewed_brief_is_never_labelled_pass_or_approved() -> None:
    integrity = ResearchIntegrity(adversarially_reviewed=False)
    verdict = integrity.verdict(structural_pass=True)
    assert verdict == UNREVIEWED_LABEL
    assert "PASS" not in verdict
    assert "APPROVED" not in verdict
    assert "NOT ADVERSARIALLY REVIEWED" in verdict


def test_a_reviewed_passing_brief_keeps_its_approved_verdict() -> None:
    assert ResearchIntegrity().verdict(structural_pass=True) == "APPROVED"


def test_a_reviewed_failing_brief_is_rejected() -> None:
    assert ResearchIntegrity().verdict(structural_pass=False) == "REJECTED"


def test_structural_failure_outranks_the_unreviewed_label() -> None:
    """If structural checks themselves fail, that is a rejection regardless."""
    integrity = ResearchIntegrity(adversarially_reviewed=False)
    assert integrity.verdict(structural_pass=False) == "REJECTED"


def test_quota_exhaustion_marks_the_brief_incomplete() -> None:
    assert not ResearchIntegrity(quota_exhausted=True).complete


def test_a_degraded_agent_marks_the_brief_incomplete() -> None:
    assert not ResearchIntegrity(degraded=("market-data",)).complete


def test_a_budget_dropped_agent_marks_the_brief_incomplete() -> None:
    assert not ResearchIntegrity(dropped=("macro-analyst",)).complete


def test_an_input_gated_skip_does_not_by_itself_mean_partial_research() -> None:
    """A source that is not configured is a known, stable absence — the brief
    says "not collected" and that is the honest full picture, not a degradation."""
    assert ResearchIntegrity(skipped=("sentiment-analyst",)).complete


def test_the_tldr_banner_names_every_cause() -> None:
    """§1, because the operator reads TL;DR at 06:30 and §9 last, if at all."""
    banner = ResearchIntegrity(
        adversarially_reviewed=False,
        degraded=("market-data",),
        dropped=("macro-analyst",),
        quota_exhausted=True,
    ).tldr_banner()
    assert banner is not None
    assert "market-data" in banner and "macro-analyst" in banner
    assert "quota" in banner.lower()
    assert "not adversarially reviewed" in banner.lower()
    assert banner.startswith("> ⚠️"), "must render as a callout, not an aside"


def test_anomaly_lines_are_produced_for_section_9() -> None:
    lines = ResearchIntegrity(
        adversarially_reviewed=False, quota_exhausted=True
    ).anomaly_lines()
    assert any("adversarial" in ln.lower() for ln in lines)
    assert any("quota" in ln.lower() for ln in lines)
    assert all(ln.startswith("[high]") for ln in lines)


def test_tickets_are_ineligible_for_stamping_when_unreviewed() -> None:
    assert not ResearchIntegrity(adversarially_reviewed=False).tickets_stampable
    assert ResearchIntegrity().tickets_stampable


def test_a_degraded_agent_does_not_block_ticket_stamping() -> None:
    """Thin research is visible to the reader; an unreviewed ticket is not. Only
    the missing review gates the approval path."""
    assert ResearchIntegrity(degraded=("news-analyst",)).tickets_stampable
