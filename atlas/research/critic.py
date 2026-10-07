"""The adversarial critic (§6): red-teams every brief, authority to reject.

Structural defects are caught in code — they cannot be argued away. The LLM
adds a judgment pass on top. Max 2 rejection rounds (orchestrated in
synthesis.py), then the brief ships marked REJECTED rather than silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from atlas.core.llm import LLMClient, LLMError
from atlas.core.logging import get_logger
from atlas.research.agents.schemas import ResearchBundle, RiskAssessment

log = get_logger("research.critic")

REQUIRED_SECTIONS = [
    "## TL;DR",
    "## Portfolio Status",
    "## Key Findings",
    "## Today's Proposed Trades",
    "## Anomalies",
    "## Calendar",
    "## Track Record",
    "## Glossary",
]
DISCLAIMER = "Educational analysis produced by an automated system"

TICKET_REQUIRED_FIELDS = [
    "**Cash check**",
    "**Thesis**",
    "**Catalyst**",
    "**Risk**",
    "**Bear case**",
    "**What would falsify this**",
    "**Time horizon**",
    "**Sizing rationale**",
    "**Portfolio impact**",
    "**Exit plan**",
    "**Robinhood how-to**",
    "**Learning note**",
]


@dataclass
class Defect:
    code: str
    detail: str
    blocking: bool = True


@dataclass
class CriticReport:
    defects: list[Defect] = field(default_factory=list)
    # Whether the adversarial LLM layer actually ran. A structural-only pass is
    # NOT a pass (Task C) — it must never be reported as one.
    adversarially_reviewed: bool = False

    @property
    def blocking(self) -> list[Defect]:
        return [d for d in self.defects if d.blocking]

    @property
    def passes(self) -> bool:
        return not self.blocking

    def summary(self) -> str:
        if self.passes and not self.adversarially_reviewed:
            return "critic: STRUCTURAL CHECKS ONLY — NOT ADVERSARIALLY REVIEWED"
        if self.passes:
            return "critic: PASS"
        return "critic: REJECT — " + "; ".join(f"[{d.code}] {d.detail}" for d in self.blocking)


class LLMCritique(BaseModel):
    defects: list[str] = Field(default_factory=list)  # empty = clean
    verdict: str = ""


def _ticket_blocks(brief_md: str) -> list[str]:
    """Split the Proposed Trades section into per-ticket blocks (### headers)."""
    m = re.search(r"## Today's Proposed Trades\n(.*?)(?=\n## )", brief_md, re.S)
    if not m:
        return []
    body = m.group(1)
    blocks = re.split(r"\n(?=### )", body)
    return [b for b in blocks if b.strip().startswith("### ")]


def structural_review(
    brief_md: str, assessment: RiskAssessment | None = None
) -> list[Defect]:
    defects: list[Defect] = []

    for section in REQUIRED_SECTIONS:
        if section not in brief_md:
            defects.append(Defect("missing_section", f"required section {section!r} absent"))

    if DISCLAIMER not in brief_md:
        defects.append(Defect("missing_disclaimer", "footer disclaimer absent (§1.4)"))

    tldr = re.search(r"## TL;DR\n(.*?)(?=\n## )", brief_md, re.S)
    if tldr:
        bullets = [line for line in tldr.group(1).splitlines() if line.strip().startswith("-")]
        if not 3 <= len(bullets) <= 5:
            defects.append(Defect("tldr_bullets", f"TL;DR needs 3-5 bullets, found {len(bullets)}"))

    blocks = _ticket_blocks(brief_md)
    for block in blocks:
        header = block.splitlines()[0]
        for fld in TICKET_REQUIRED_FIELDS:
            if fld not in block:
                defects.append(Defect("ticket_missing_field", f"{header}: {fld} missing"))
        impact = re.search(r"\*\*Portfolio impact\*\*[:\s]*(.+)", block)
        if impact and len(impact.group(1).strip()) < 40:
            defects.append(
                Defect("handwavy_portfolio_impact", f"{header}: portfolio impact too thin — "
                       "must state sleeve delta, correlation, cash delta (§2.0.1)")
            )
        if re.search(r"fractional", block, re.I) and re.search(r"limit price[:\s]*\$?\d", block, re.I):
            defects.append(
                Defect("fractional_with_limit", f"{header}: fractional ticket shows a limit price "
                       "— Robinhood rejects fractional limit orders (§2.1)")
            )

    if not blocks and "no trades today" not in brief_md.lower():
        defects.append(
            Defect("no_trades_unexplained", "zero tickets but no explicit 'No trades today' "
                   "explanation — over-trading avoidance must be stated, not silent (§8)")
        )

    findings = re.search(r"## Key Findings\n(.*?)(?=\n## )", brief_md, re.S)
    if findings:
        numbered = re.findall(r"^\s*\d+\.\s+(.*)$", findings.group(1), re.M)
        for line in numbered:
            if not re.search(r"\[(FACT|CONSENSUS|SPECULATION)\]", line):
                defects.append(Defect("untagged_claim", f"finding lacks tag: {line[:80]!r}"))
            if not re.search(r"\(.*\d{4}-\d{2}-\d{2}.*\)|\[.*retrieved.*\]|source:", line, re.I):
                defects.append(Defect("uncited_claim", f"finding lacks source+timestamp: {line[:80]!r}"))

    if assessment is not None:
        for a in assessment.approved:
            # A cash violation only applies to BUYs: sized_dollars is money SPENT.
            # For SELL/TRIM, sized_dollars is proceeds RECEIVED (unsettled T+1),
            # so comparing it to settled cash is meaningless.
            if a.idea.action == "BUY" and a.sized_dollars > a.cash_before + 0.01:
                defects.append(
                    Defect("cash_violation", f"{a.idea.ticker}: spend {a.sized_dollars} exceeds "
                           f"settled cash {a.cash_before} (§2.1)")
                )
            if a.is_fractional and a.limit_price is not None:
                defects.append(
                    Defect("fractional_with_limit", f"{a.idea.ticker}: sized fractional with a "
                           "limit price — broker will reject")
                )

    return defects


def llm_review(
    brief_md: str, bundle: ResearchBundle, llm: LLMClient
) -> list[Defect]:
    messages = [
        {
            "role": "system",
            "content": (
                "You are Atlas's adversarial critic. Red-team this daily brief: unsupported "
                "claims, hindsight bias, manufactured certainty, tickets evaluated in "
                "isolation instead of against the current book, theses contradicting the "
                "research bundle, missing uncertainty. Report DEFECTS ONLY — an empty list "
                "means clean. Be specific and terse."
            ),
        },
        {
            "role": "user",
            "content": (
                "RESEARCH BUNDLE SUMMARIES:\n"
                + "\n".join(f"- {v.agent}: {v.summary[:300]}" for v in bundle.all_views())
                + f"\n\nBRIEF:\n{brief_md[:24000]}"
            ),
        },
    ]
    try:
        critique = llm.complete(messages, LLMCritique, agent="critic")
        return [Defect("llm_critique", d, blocking=False) for d in critique.defects[:10]]
    except LLMError as exc:
        log.warning("LLM critic unavailable — structural checks only", extra={"data": {"error": str(exc)}})
        return [Defect("critic_degraded", f"LLM critic unavailable: {exc}", blocking=False)]


def critique_brief(
    brief_md: str,
    bundle: ResearchBundle,
    assessment: RiskAssessment | None,
    llm: LLMClient | None,
) -> CriticReport:
    report = CriticReport(defects=structural_review(brief_md, assessment))
    if llm is not None:
        llm_defects = llm_review(brief_md, bundle, llm)
        report.defects.extend(llm_defects)
        # The layer ran only if it did not fall back. `critic_degraded` is the
        # sentinel llm_review returns when the call failed.
        report.adversarially_reviewed = not any(
            d.code == "critic_degraded" for d in llm_defects
        )
    log.info(report.summary())
    return report
