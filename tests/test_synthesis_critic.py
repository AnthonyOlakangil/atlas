"""Phase 6 gate: brief from fixture bundle; critic catches ≥3 seeded defects."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import atlas.research.synthesis as synthesis
from atlas.core.models import Brief, GlossaryTerm, Snapshot, Ticket, TicketStatus
from atlas.research.agents.risk import run_risk_agent
from atlas.research.agents.schemas import RiskAssessment
from atlas.research.critic import critique_brief, structural_review
from atlas.research.synthesis import synthesize_and_persist
from tests.fakes import FakeLLMClient
from tests.test_research_agents import (
    CFG,
    PRICES,
    build_offline_bundle,
    fixture_snapshot,
    fixture_state,
)


@pytest.fixture(autouse=True)
def briefs_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "briefs"
    monkeypatch.setattr(synthesis, "BRIEFS_DIR", d)
    return d


def _synthesize(session: Session, assessment: RiskAssessment | None = None) -> Brief:
    bundle = build_offline_bundle()
    state = fixture_state()
    assessment = assessment or run_risk_agent(bundle, state, CFG, PRICES)
    return synthesize_and_persist(
        session, bundle, assessment, state,
        snapshot=fixture_snapshot(), llm=FakeLLMClient(),
    )


# --- the Phase 6 gate ---------------------------------------------------------


def test_brief_generated_from_fixture_bundle_and_approved(
    db_session: Session, briefs_in_tmp: Path
) -> None:
    brief = _synthesize(db_session)
    assert brief.status == "APPROVED"
    assert brief.critic_rounds == 1

    md = brief.content_md
    for section in (
        "## TL;DR", "## Portfolio Status", "## Key Findings", "## Today's Proposed Trades",
        "## Anomalies", "## Calendar", "## Track Record", "## Glossary",
    ):
        assert section in md, f"missing {section}"
    assert "Educational analysis produced by an automated system" in md
    assert "[FACT]" in md and "[CONSENSUS]" in md and "[SPECULATION]" in md
    assert "retrieved" in md  # citations carry timestamps

    files = list(briefs_in_tmp.glob("*.md"))
    assert len(files) == 1 and files[0].read_text() == md


def test_critic_catches_seeded_defects() -> None:
    """The deliberately-flawed brief: the critic must catch ≥3 seeded defects."""
    flawed = """# Atlas Daily Brief — 2026-07-17

## TL;DR
- only one bullet

## Portfolio Status
stuff

## Key Findings
1. NVDA is going to $300 for sure
2. [FACT] SPY at 748.30 (source: mcp, retrieved 2026-07-17T13:30:00Z)

## Today's Proposed Trades
### 1. BUY MOON — $500.00 (speculative)
- **Order**: 2.5 shares (fractional) — limit price: $12.50
- **Thesis**: it will go up

## Anomalies
- none

## Calendar
- none

## Track Record
n/a

## Glossary
(none)
"""
    defects = structural_review(flawed)
    codes = {d.code for d in defects}
    # seeded: 1-bullet TL;DR, untagged+uncited finding, fractional-with-limit,
    # and a ticket missing bear case/falsifier/cash-check/portfolio-impact/etc.
    assert len(defects) >= 3
    assert "tldr_bullets" in codes
    assert "untagged_claim" in codes
    assert "fractional_with_limit" in codes
    assert "ticket_missing_field" in codes
    assert "missing_disclaimer" in codes


def test_critic_passes_generated_brief(db_session: Session) -> None:
    brief = _synthesize(db_session)
    report = critique_brief(brief.content_md, build_offline_bundle(), None, FakeLLMClient())
    assert report.passes, report.summary()


def test_critic_does_not_flag_trim_proceeds_as_cash_violation() -> None:
    """Regression: a TRIM's proceeds ($85) exceed settled cash ($45), but that is
    money RECEIVED, not spent — the critic must not call it a cash_violation.
    Surfaced by the 2026-07-20 live run rejecting a legitimate SMH trim twice."""
    from atlas.research.agents.schemas import ApprovedIdea, RiskAssessment, TradeIdea

    trim = ApprovedIdea(
        idea=TradeIdea(
            ticker="SMH", sleeve="satellite_ai_semis", action="TRIM", target_dollars=85.0,
            thesis="t", catalyst="c", risk="r", bear_case="b", falsifier="f",
            time_horizon="weeks", confidence=0.7,
        ),
        sized_dollars=85.0, quantity="0.2000", order_type="market", is_fractional=True,
        cash_before=45.0, cash_after=45.0,  # proceeds unsettled → cash unchanged today
        portfolio_impact="trims overweight SMH; proceeds settle T+1",
    )
    codes = {d.code for d in structural_review("", RiskAssessment(approved=[trim]))}
    assert "cash_violation" not in codes

    # a real BUY overspend still fires
    overspend = trim.model_copy(deep=True)
    overspend.idea.action = "BUY"
    codes2 = {d.code for d in structural_review("", RiskAssessment(approved=[overspend]))}
    assert "cash_violation" in codes2


# --- tickets ------------------------------------------------------------------


def test_tickets_persist_pending_approval_only(db_session: Session) -> None:
    _synthesize(db_session)
    tickets = db_session.scalars(select(Ticket)).all()
    assert tickets, "approved ideas must persist as tickets"
    assert all(t.status is TicketStatus.PENDING_APPROVAL for t in tickets)  # §1.2: born unapproved
    voo = next(t for t in tickets if t.ticker == "VOO")
    assert voo.exit_stop is None  # core holding: band-rebalanced, no stop
    qqqm = next(t for t in tickets if t.ticker == "QQQM")
    assert qqqm.exit_stop is not None and qqqm.exit_target is not None  # satellite gets stops
    assert qqqm.is_fractional and qqqm.limit_price is None  # never a limit price on fractional


def test_fractional_ticket_says_market_forced(db_session: Session) -> None:
    brief = _synthesize(db_session)
    assert "market order, forced" in brief.content_md
    assert "no price cap" in brief.content_md.lower()
    assert "Buy in Dollars" in brief.content_md  # the Robinhood how-to fork


# --- no-trades day ------------------------------------------------------------


def test_no_trades_day_is_explicit_and_passes(db_session: Session) -> None:
    empty = RiskAssessment(settled_cash=250.0, total_value=250.0)
    brief = _synthesize(db_session, assessment=empty)
    assert "No trades today." in brief.content_md
    assert brief.status == "APPROVED"
    assert not db_session.scalars(select(Ticket)).all()


# --- critic rejection loop ----------------------------------------------------


def test_rejection_feeds_back_and_retries_once(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.research.critic import CriticReport, Defect

    calls: list[int] = []

    def flaky_critic(brief_md: str, bundle: object, assessment: object, llm: object) -> CriticReport:
        calls.append(1)
        if len(calls) == 1:
            return CriticReport(defects=[Defect("seeded", "first draft rejected")])
        return CriticReport(adversarially_reviewed=True)

    monkeypatch.setattr(synthesis, "critique_brief", flaky_critic)
    brief = _synthesize(db_session)
    assert brief.critic_rounds == 2 and brief.status == "APPROVED"
    assert len(calls) == 2


def test_double_rejection_ships_marked_rejected(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from atlas.research.critic import CriticReport, Defect

    monkeypatch.setattr(
        synthesis, "critique_brief",
        lambda *a, **k: CriticReport(defects=[Defect("seeded", "always rejected")]),
    )
    brief = _synthesize(db_session)
    assert brief.status == "REJECTED"
    assert "CRITIC REJECTED" in brief.content_md  # shipped honestly, not silently


# --- glossary + snapshot ------------------------------------------------------


def test_glossary_first_use_then_suppressed(db_session: Session) -> None:
    db_session.add(
        GlossaryTerm(term="limit order", definition="An order that fills at your price or better.")
    )
    db_session.flush()
    brief1 = _synthesize(db_session)
    assert "**limit order**: An order that fills" in brief1.content_md  # first use taught

    brief2 = _synthesize(db_session)
    assert "**limit order**: An order that fills" not in brief2.content_md  # already shown


def test_snapshot_written_per_brief(db_session: Session) -> None:
    _synthesize(db_session)
    snaps = db_session.scalars(select(Snapshot)).all()
    assert len(snaps) == 1
    assert snaps[0].total_value == Decimal("250.00")
    assert snaps[0].spy_close == Decimal("748.30")


def test_day_change_never_derives_a_return_from_totals_alone(db_session: Session) -> None:
    """Bug 1 (P0). This test previously asserted "+4.17% since last snapshot",
    computed as (250-240)/240 straight off `total_value` — the same total-vs-total
    mechanism that printed "+94.86%" on 2026-08-16 after portfolio.yaml was
    replaced. `total_value` includes cash, so that figure counted a deposit as
    return. The prior snapshot here carries no per-position prices, so the only
    honest output is a named degradation, not a number."""
    db_session.add(
        Snapshot(
            taken_at=datetime(2026, 7, 16, 20, 0, tzinfo=UTC),
            total_value=Decimal("240.00"), settled_cash=Decimal("240.00"),
            unsettled_cash=Decimal("0"),
        )
    )
    db_session.flush()
    brief = _synthesize(db_session)
    assert "Day change**: n/a" in brief.content_md
    assert "+4.17%" not in brief.content_md
