"""Brief synthesis (§8): the daily deliverable, built to the reference quality bar.

Deterministic template assembly around structured LLM narrative. The critic
reviews every draft; rejection feeds defects back into re-synthesis, max 2
rounds. The pipeline terminates at PENDING_APPROVAL tickets — this module
cannot import the execution package (CI-enforced).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.format import money, pct, shares
from atlas.core.howto import robinhood_howto
from atlas.core.llm import LLMClient, LLMError
from atlas.core.logging import get_logger
from atlas.core.models import (
    Brief,
    GlossaryTerm,
    Mode,
    OrderType,
    Snapshot,
    Ticket,
    TradeAction,
)
from atlas.core.settings import BRIEFS_DIR, OperatorConfig, get_operator_config
from atlas.ingest.macro import MacroContext
from atlas.ingest.market import MarketSnapshot
from atlas.portfolio.performance import compute_day_change, position_map
from atlas.portfolio.scoring import track_record_section
from atlas.portfolio.state import PortfolioState, validate_allocation
from atlas.research.agents.schemas import ApprovedIdea, ResearchBundle, RiskAssessment
from atlas.research.critic import critique_brief
from atlas.research.integrity import UNREVIEWED_LABEL, ResearchIntegrity

log = get_logger("research.synthesis")

FOOTER = (
    "\n---\n*Educational analysis produced by an automated system. Not personalized "
    "investment advice. All investment decisions and their consequences are the operator's.*\n"
)

# v1 exit-plan policy (DECISIONS.md): satellites/speculative get protective stops;
# core/diversifier are band-rebalanced holdings, no stop.
STOP_PCT = Decimal("0.92")
TARGET_PCT = Decimal("1.20")
STOPPED_SLEEVES = ("satellite_ai_semis", "speculative")


class SynthesisNarrative(BaseModel):
    tldr: list[str] = Field(min_length=3, max_length=5)
    teaching_note: str
    no_trade_rationale: str = ""


def _narrative(
    bundle: ResearchBundle, assessment: RiskAssessment, llm: LLMClient | None,
    defects: list[str] | None = None,
) -> SynthesisNarrative:
    fallback = SynthesisNarrative(
        tldr=[
            "Research cycle completed; see Key Findings below.",
            f"{len(assessment.approved)} proposed trade(s); {len(assessment.watch_items)} watch item(s).",
            f"Settled cash ${assessment.settled_cash:.2f}; see cash math on each ticket.",
        ],
        teaching_note="Review each ticket's Learning note.",
        no_trade_rationale="No setups cleared the risk gate today." if not assessment.approved else "",
    )
    if llm is None:
        return fallback
    defect_text = (
        "\nA previous draft was REJECTED by the critic for: " + "; ".join(defects) + ". Fix these."
        if defects
        else ""
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You write the TL;DR of a daily brief for an early-stage retail investor. "
                "Direct, concise, no filler, no hedging theater. What changed, what it means "
                "for THIS portfolio, what to do. 'No trades today' is a valid, often-correct "
                "answer — never manufacture activity or certainty."
            ),
        },
        {
            "role": "user",
            "content": (
                "\n".join(f"- {v.agent}: {v.summary[:250]}" for v in bundle.all_views())
                + f"\nAPPROVED: {[(a.idea.ticker, a.sized_dollars) for a in assessment.approved]}"
                + f"\nWATCH: {assessment.watch_items[:5]}"
                + f"\nVETOED: {[(v.ticker, v.reason[:80]) for v in assessment.vetoed]}"
                + f"\nDRIFT: {assessment.drift_notes}"
                + defect_text
                + "\n3-5 TL;DR bullets, one teaching_note (one concept today's cycle teaches), "
                + "and no_trade_rationale if there are zero approved trades."
            ),
        },
    ]
    try:
        return llm.complete(messages, SynthesisNarrative, agent="synthesizer")
    except LLMError as exc:
        log.warning(f"synthesizer narrative degraded: {exc}")
        return fallback


def _fmt_claim(i: int, claim: Any) -> str:
    return (
        f"{i}. [{claim.kind.upper()}] {claim.text} "
        f"(source: {claim.source}, retrieved {claim.retrieved_at})"
    )


def _robinhood_howto(a: ApprovedIdea) -> str:
    return robinhood_howto(
        action=a.idea.action,
        ticker=a.idea.ticker,
        order_type=a.order_type,
        is_fractional=a.is_fractional,
        quantity=a.quantity,
        dollar_amount=a.sized_dollars,
        limit_price=a.limit_price,
    )


def _exit_plan(a: ApprovedIdea) -> tuple[Decimal | None, Decimal | None, str]:
    if a.idea.action in ("SELL", "TRIM"):
        return None, None, "n/a — this ticket IS an exit."
    ref = Decimal(str(a.limit_price)) if a.limit_price else Decimal(str(a.sized_dollars)) / Decimal(a.quantity)
    if a.idea.sleeve in STOPPED_SLEEVES:
        stop = (ref * STOP_PCT).quantize(Decimal("0.01"))
        target = (ref * TARGET_PCT).quantize(Decimal("0.01"))
        return stop, target, (
            f"Stop {money(stop)} (-8%), target {money(target)} (+20%). ⚠️ Stops are app-managed: Atlas "
            "monitors and proposes the exit — they are only enforced while Atlas is running "
            "(Robinhood has no bracket orders)."
        )
    return None, None, (
        "No stop — core/diversifier holding managed by sleeve-band rebalancing, not stops. "
        "Falsifier above defines the exit condition."
    )


def _ticket_md(n: int, a: ApprovedIdea) -> str:
    i = a.idea
    frac_note = (
        f"{a.quantity} shares (fractional) — **market order, forced**: the quantity is "
        "fractional and Robinhood forbids fractional limit orders, so there is no price cap "
        "on this fill"
        if a.is_fractional
        else f"{a.quantity} whole share(s) — **{a.order_type} order** at {money(a.limit_price)}"
    )
    stop, target, exit_text = _exit_plan(a)
    lines = [
        f"### {n}. {i.action} {i.ticker} — ${a.sized_dollars:.2f} ({i.sleeve})",
        "",
        f"- **Order**: {frac_note}",
        f"- **Cash check**: ${a.cash_before:.2f} settled → ${a.sized_dollars:.2f} this trade → "
        f"${a.cash_after:.2f} remaining settled" + (
            " (sale proceeds settle T+1 — not spendable until then)" if i.action in ("SELL", "TRIM") else ""
        ),
        f"- **Thesis**: {i.thesis}",
        f"- **Catalyst**: {i.catalyst}",
        f"- **Risk**: {i.risk}",
        f"- **Bear case**: {i.bear_case}",
        f"- **What would falsify this**: {i.falsifier}",
        f"- **Time horizon**: {i.time_horizon}",
        f"- **Sizing rationale**: {'; '.join(a.sizing_notes) or 'within all caps and bands'}",
        f"- **Portfolio impact**: {a.portfolio_impact}",
        f"- **Exit plan**: {exit_text}",
        f"- **Robinhood how-to**: {_robinhood_howto(a)}",
        f"- **Learning note**: {_learning_note(a)}",
    ]
    if i.beats_seed_alternative:
        lines.insert(3, f"- **Why this over the seed option**: {i.beats_seed_alternative}")
    if i.confidence:
        lines.append(f"- **Confidence**: {i.confidence:.0%}")
    return "\n".join(lines)


def _learning_note(a: ApprovedIdea) -> str:
    if a.is_fractional:
        return (
            "Fractional shares let small accounts own expensive tickers, but on Robinhood they "
            "trade as market orders only — you trade price control for accessibility."
        )
    if a.order_type == "limit":
        return (
            "A limit order caps what you pay: it fills at your price or better, but may not "
            "fill at all if the market runs away."
        )
    return (
        "A stop-limit exit triggers at your stop price and then works as a limit order — "
        "protection with a floor, but fast gaps can jump past it."
    )


def _portfolio_status(state: PortfolioState, cfg: OperatorConfig, session: Session) -> str:
    # Backstop before anything renders: if declared sleeves plus cash do not
    # account for the whole book, a holding is invisible to the band math and the
    # concentration numbers cannot be trusted. Halt rather than publish (Task 1a).
    validate_allocation(state, cfg)
    total = state.total_value
    prev = session.scalars(
        select(Snapshot).where(Snapshot.mode == Mode.PAPER).order_by(Snapshot.taken_at.desc())
    ).first()
    # Per-position price return, never total-vs-total: a total diff cannot tell a
    # price move from a deposit or a composition change (Bug 1, P0).
    prior_on = prev.taken_at.date() if prev else None
    day_change = compute_day_change(
        prev.positions if prev else None, position_map(state.positions), prior_on
    ).render(prior_on)
    weights = state.sleeve_weights()
    rows = ["| Sleeve | Actual | Target band | Status |", "|---|---|---|---|"]
    for name, sleeve in cfg.sleeves.items():
        actual = float(weights.get(name, Decimal(0)))
        lo, hi = sleeve.target_pct
        status = "in band" if lo <= actual <= hi else ("**BELOW**" if actual < lo else "**ABOVE**")
        if actual == 0 and lo == 0:
            status = "in band"
        rows.append(f"| {name} | {pct(Decimal(str(actual)) * 100)} | {lo:.0%}–{hi:.0%} | {status} |")
    positions = (
        "\n".join(
            f"- {p.ticker} ({p.sleeve}): {shares(p.shares)} sh @ {money(p.price)}"
            f" = {money(p.value)}"
            for p in state.positions
        )
        or "- (no positions yet)"
    )
    return (
        f"- **Total value**: {money(total)} | **Day change**: {day_change}\n"
        f"- **Settled cash**: {money(state.cash.settled)}"
        f" | **Unsettled**: {money(state.cash.unsettled)}\n\n"
        + "\n".join(rows)
        + "\n\n**Positions**\n"
        + positions
    )


def _calendar_section(snapshot: MarketSnapshot | None, macro: MacroContext | None) -> str:
    events: list[tuple[str, str]] = []
    if macro:
        events += [(e["date"], e["event"]) for e in macro.upcoming_events]
    if snapshot:
        events += [
            (str(e.get("date", "")), f"{e.get('symbol', '?')} earnings")
            for e in snapshot.earnings_calendar
            if e.get("date")
        ]
    events = sorted(set(events))[:14]
    if not events:
        return "(no dated catalysts available this cycle)"
    return "\n".join(f"- **{d}** — {name}" for d, name in events)


def _glossary_section(brief_body: str, session: Session) -> str:
    terms = session.scalars(select(GlossaryTerm)).all()
    shown: list[str] = []
    now = datetime.now(UTC)
    for t in terms:
        if t.first_shown_at is not None:
            continue  # already taught in an earlier brief
        if re.search(re.escape(t.term), brief_body, re.I):
            shown.append(f"- **{t.term}**: {t.definition}")
            t.first_shown_at = now
    return "\n".join(shown) if shown else "(no new terms this brief)"


def render_brief(
    *,
    brief_date: str,
    bundle: ResearchBundle,
    assessment: RiskAssessment,
    state: PortfolioState,
    narrative: SynthesisNarrative,
    session: Session,
    cfg: OperatorConfig,
    snapshot: MarketSnapshot | None = None,
    macro: MacroContext | None = None,
    integrity: ResearchIntegrity | None = None,
) -> str:
    integrity = integrity or ResearchIntegrity()
    claims = [c for v in bundle.all_views() for c in v.key_findings]
    findings = "\n".join(_fmt_claim(i + 1, c) for i, c in enumerate(claims[:15]))

    if assessment.approved:
        trades = "\n\n".join(_ticket_md(i + 1, a) for i, a in enumerate(assessment.approved))
    else:
        rationale = narrative.no_trade_rationale or "No setups cleared the risk gate."
        trades = (
            f"**No trades today.** {rationale}\n\n"
            "Atlas is not rewarded for activity — holding is a position too."
        )
    if assessment.watch_items:
        trades += "\n\n**Watch items** (not tickets — mostly affordability or caps):\n" + "\n".join(
            f"- {w}" for w in assessment.watch_items
        )
    # §7 must never show "No trades today" beside a stated band breach with no
    # reasoning. Silence is not an option (Task B).
    trades += assessment.breach_explanation
    if assessment.vetoed:
        trades += "\n\n**Vetoed by portfolio-risk**:\n" + "\n".join(
            f"- {v.ticker}: {v.reason}" for v in assessment.vetoed
        )

    # Ticket rejections live on the assessment, not the bundle. They must appear
    # in §9: a silently dropped ticket is worse than a bad one (Bug 3).
    anomalies = sorted(
        set(bundle.anomalies) | set(assessment.anomalies) | set(integrity.anomaly_lines())
    )
    injection = sorted({f for v in bundle.all_views() for f in v.injection_flags})
    anomaly_lines = [f"- {a}" for a in anomalies]
    if injection:
        anomaly_lines.append(f"- ⚠️ injection flags on scraped content (excluded from scoring): {injection}")
    if assessment.gfv_status and "0 GFV" not in assessment.gfv_status:
        anomaly_lines.append(f"- {assessment.gfv_status}")
    anomalies_md = "\n".join(anomaly_lines) or "- none this cycle"

    # §1 disclosure: the operator reads TL;DR at 06:30 and §9 last, if at all, so
    # anything that changes how far the brief can be trusted goes here too.
    banner = integrity.tldr_banner()
    integrity_banner = f"{banner}\n\n" if banner else ""

    body = f"""# Atlas Daily Brief — {brief_date}

## TL;DR
{integrity_banner}{chr(10).join(f"- {b}" for b in narrative.tldr)}

## Portfolio Status
{_portfolio_status(state, cfg, session)}

## Key Findings
{findings or "1. [FACT] No findings produced this cycle (source: atlas, retrieved " + brief_date + ")"}

## Today's Proposed Trades
{trades}

## Anomalies
{anomalies_md}

## Calendar
{_calendar_section(snapshot, macro)}

## Track Record
{track_record_section(session)}

**Today's teaching note**: {narrative.teaching_note}

## Glossary
"""
    body += _glossary_section(body + trades, session)
    return body + FOOTER


def persist_tickets(session: Session, brief_row: Brief, assessment: RiskAssessment) -> list[Ticket]:
    """Every approved idea becomes a PENDING_APPROVAL ticket — never anything more (§1.2)."""
    tickets = []
    for a in assessment.approved:
        stop, target, _ = _exit_plan(a)
        ticket = Ticket(
            ticker=a.idea.ticker,
            action=TradeAction(a.idea.action) if a.idea.action in ("BUY", "SELL") else TradeAction.SELL,
            sleeve=a.idea.sleeve,
            order_type=OrderType(a.order_type),
            quantity=Decimal(a.quantity),
            dollar_amount=Decimal(str(a.sized_dollars)),
            limit_price=Decimal(str(a.limit_price)) if a.limit_price else None,
            is_fractional=a.is_fractional,
            cash_before=Decimal(str(a.cash_before)),
            cash_after=Decimal(str(a.cash_after)),
            thesis=a.idea.thesis,
            catalyst=a.idea.catalyst,
            risk=a.idea.risk,
            bear_case=a.idea.bear_case,
            falsifier=a.idea.falsifier,
            time_horizon=a.idea.time_horizon,
            sizing_rationale="; ".join(a.sizing_notes),
            portfolio_impact=a.portfolio_impact,
            learning_note=_learning_note(a),
            exit_stop=stop,
            exit_target=target,
            brief=brief_row,
        )
        session.add(ticket)
        tickets.append(ticket)
    session.flush()
    return tickets


def _notify(title: str, message: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
            capture_output=True, timeout=5, check=False,
        )
    except Exception:  # noqa: BLE001 — notification is best-effort
        pass


def synthesize_and_persist(
    session: Session,
    bundle: ResearchBundle,
    assessment: RiskAssessment,
    state: PortfolioState,
    *,
    snapshot: MarketSnapshot | None = None,
    macro: MacroContext | None = None,
    llm: LLMClient | None = None,
    kind: str = "preopen",
    integrity: ResearchIntegrity | None = None,
) -> Brief:
    cfg = get_operator_config()
    integrity = integrity or ResearchIntegrity()
    today = state.as_of.isoformat()

    defects_feedback: list[str] | None = None
    brief_md, rounds = "", 0
    structural_pass = False
    reviewed = False
    for round_no in (1, 2):
        rounds = round_no
        narrative = _narrative(bundle, assessment, llm, defects_feedback)
        brief_md = render_brief(
            brief_date=today, bundle=bundle, assessment=assessment, state=state,
            narrative=narrative, session=session, cfg=cfg, snapshot=snapshot, macro=macro,
            integrity=integrity,
        )
        report = critique_brief(brief_md, bundle, assessment, llm)
        reviewed = report.adversarially_reviewed
        if report.passes:
            structural_pass = True
            break
        defects_feedback = [f"[{d.code}] {d.detail}" for d in report.blocking]
        log.warning(f"critic rejected round {round_no}: {defects_feedback}")

    # Task C: a brief the adversarial layer never saw is never APPROVED. The
    # label has to be trustworthy precisely when the critic fails, and on free
    # tier it will fail again — the critic is the last request in the cycle.
    integrity = replace(integrity, adversarially_reviewed=reviewed)
    status = integrity.verdict(structural_pass=structural_pass)

    if not structural_pass:
        brief_md += "\n\n> ⚠️ **CRITIC REJECTED** after 2 rounds: " + "; ".join(defects_feedback or [])
    elif not reviewed:
        brief_md += (
            "\n\n> ⚠️ **" + UNREVIEWED_LABEL + ".** The adversarial critic could not "
            "run this cycle, so this brief passed structural checks only. Its tickets "
            "are marked UNREVIEWED and cannot be stamped with `atlas filled` without "
            "an explicit override."
        )

    BRIEFS_DIR.mkdir(exist_ok=True)
    path = BRIEFS_DIR / f"{today}{'' if kind == 'preopen' else '-postclose'}.md"
    path.write_text(brief_md)

    brief_row = Brief(
        brief_date=state.as_of, kind=kind, path=str(path), content_md=brief_md,
        status=status, critic_rounds=rounds,
    )
    session.add(brief_row)
    persist_tickets(session, brief_row, assessment)

    spy = None
    if snapshot and "SPY" in snapshot.quotes:
        spy = Decimal(str(snapshot.quotes["SPY"].price))
    session.add(
        Snapshot(
            mode=Mode.PAPER,
            total_value=state.total_value,
            settled_cash=state.cash.settled,
            unsettled_cash=state.cash.unsettled,
            spy_close=spy,
            positions=position_map(state.positions),
        )
    )
    session.flush()
    _notify("Atlas", f"Brief ready: {len(assessment.approved)} proposed trade(s) — {path.name}")
    log.info(f"brief persisted: {path} ({status}, {rounds} round(s))")
    return brief_row


def latest_brief_path() -> str | None:
    if not BRIEFS_DIR.exists():
        return None
    briefs = sorted(BRIEFS_DIR.glob("2*.md"))
    return str(briefs[-1]) if briefs else None
