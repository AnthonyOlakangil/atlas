"""Research integrity: one mechanism for "this brief is less than complete".

A brief stamped `APPROVED` with `critic: PASS` makes a promise — that something
adversarial reviewed it before it reached the operator. On 2026-08-16 the
critic's LLM layer failed twice, fell back to structural checks, and reported
PASS both times. The label was indistinguishable from a real review.

That is worse than a thin brief. A degraded analyst produces something visibly
sparser and the reader discounts it; a degraded critic produces something
*equally confident*. So the guarantee is represented explicitly here rather than
inferred from a non-blocking defect.

The causes differ — dead agent, exhausted daily quota, budget-dropped analyst,
starved critic — but the consequence for the reader is the same, so they share
one type and one disclosure path. Built once, routed everywhere:

* **§1 TL;DR** — the operator reads this at 06:30
* **the verdict line** — where the claim is actually made
* **§9 Anomalies** — read last, if at all

An input-gated *skip* is deliberately not a degradation: a source that was never
configured is a stable, known absence, and "not collected" is the complete and
honest picture rather than a shortfall.
"""

from __future__ import annotations

from dataclasses import dataclass, field

UNREVIEWED_LABEL = "STRUCTURAL CHECKS ONLY — NOT ADVERSARIALLY REVIEWED"


@dataclass(frozen=True)
class ResearchIntegrity:
    adversarially_reviewed: bool = True
    degraded: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    skipped: tuple[str, ...] = field(default=())
    quota_exhausted: bool = False

    @property
    def complete(self) -> bool:
        """Whether the brief rests on the full research pass it implies."""
        return (
            self.adversarially_reviewed
            and not self.degraded
            and not self.dropped
            and not self.quota_exhausted
        )

    @property
    def tickets_stampable(self) -> bool:
        """Only the missing review gates the approval path.

        Thin research is visible on the page and the operator can discount it. An
        unreviewed ticket looks identical to a reviewed one, so that distinction
        needs consequences beyond reading: such tickets are UNREVIEWED and cannot
        be stamped via `atlas filled` without an explicit override.
        """
        return self.adversarially_reviewed

    def verdict(self, *, structural_pass: bool) -> str:
        """The status stamped on the brief.

        A structural failure outranks everything: it is a rejection whether or
        not the adversarial layer ran.
        """
        if not structural_pass:
            return "REJECTED"
        if not self.adversarially_reviewed:
            return UNREVIEWED_LABEL
        return "APPROVED"

    def _causes(self) -> list[str]:
        causes: list[str] = []
        if not self.adversarially_reviewed:
            causes.append(
                "this brief was NOT adversarially reviewed — the critic fell back "
                "to structural checks only"
            )
        if self.quota_exhausted:
            causes.append("the daily free-tier request quota was exhausted mid-cycle")
        if self.degraded:
            causes.append(f"agent(s) failed after retries: {', '.join(self.degraded)}")
        if self.dropped:
            causes.append(
                f"agent(s) dropped for request budget: {', '.join(self.dropped)}"
            )
        return causes

    def tldr_banner(self) -> str | None:
        """§1 callout. Returns None when the cycle was complete."""
        causes = self._causes()
        if not causes:
            return None
        return (
            "> ⚠️ **This brief was built on partial research.** "
            + "; ".join(causes)
            + ". Weigh it accordingly — sections below may rest on incomplete inputs."
        )

    def anomaly_lines(self) -> list[str]:
        """§9 lines, `[high]` because each one changes how far the brief can be trusted."""
        return [f"[high] {c}" for c in self._causes()]
