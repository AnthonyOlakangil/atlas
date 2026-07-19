"""Phase 0 gate: config loading, models create/insert, settings defaults."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from atlas.core.models import (
    Base,
    Brief,
    Document,
    Mode,
    OrderType,
    Position,
    Score,
    Snapshot,
    Ticket,
    TicketStatus,
    TradeAction,
    TrustTier,
)
from atlas.core.settings import OperatorConfig, Settings, load_sources


def test_operator_config_loads() -> None:
    cfg = OperatorConfig.load()
    assert cfg.objective["benchmark"] == "SPY"
    assert set(cfg.sleeves) == {"core", "satellite_ai_semis", "diversifier", "speculative"}
    core = cfg.sleeves["core"]
    assert core.target_pct == (0.50, 0.60)
    assert cfg.risk.max_single_position_pct == 0.25
    assert cfg.universe.startswith("open")


def test_sources_registry_loads_with_tiers() -> None:
    sources = load_sources()
    tiers = {s["trust_tier"] for s in sources.values()}
    assert tiers == {"primary", "secondary", "untrusted"}
    # every source declares the required keys
    for name, src in sources.items():
        assert "rate_limit" in src, name
        assert "parser" in src, name
        assert "enabled" in src, name
    # social defaults to untrusted
    assert sources["reddit_wallstreetbets"]["trust_tier"] == "untrusted"
    assert sources["reddit_wallstreetbets"]["noise"] is True


def test_settings_defaults_paper_mode() -> None:
    s = Settings(_env_file=None)
    assert s.atlas_mode == "PAPER"
    assert s.llm_provider == "gemini"
    assert s.llm_model == "gemini-3-flash"
    assert s.db_url.startswith("sqlite:///")


def test_models_roundtrip_in_memory() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        brief = Brief(brief_date=date(2026, 7, 18), path="briefs/2026-07-18.md", content_md="# x")
        ticket = Ticket(
            ticker="VOO",
            action=TradeAction.BUY,
            sleeve="core",
            order_type=OrderType.MARKET,
            quantity=Decimal("0.2415"),
            dollar_amount=Decimal("150.00"),
            is_fractional=True,
            cash_before=Decimal("250.00"),
            cash_after=Decimal("100.00"),
            thesis="t",
            catalyst="c",
            risk="r",
            bear_case="b",
            falsifier="f",
            time_horizon="years",
            sizing_rationale="s",
            portfolio_impact="p",
            brief=brief,
        )
        session.add_all(
            [
                brief,
                ticket,
                Position(ticker="VOO", sleeve="core", shares=Decimal("0.2415"), cost_basis=Decimal("150")),
                Document(
                    source_name="reuters",
                    source_url="https://example.com/a",
                    trust_tier=TrustTier.secondary,
                    raw_text="text",
                    sha256="0" * 64,
                ),
                Snapshot(
                    total_value=Decimal("250"),
                    settled_cash=Decimal("100"),
                    unsettled_cash=Decimal("0"),
                ),
            ]
        )
        session.commit()
        session.add(
            Score(
                ticket_id=ticket.id,
                window="+1d",
                ticket_return_pct=Decimal("1.2"),
                spy_return_pct=Decimal("0.4"),
                alpha_pct=Decimal("0.8"),
            )
        )
        session.commit()

        t = session.scalars(select(Ticket)).one()
        assert t.status == TicketStatus.PENDING_APPROVAL  # tickets are born unapproved (§1.2)
        assert t.mode == Mode.PAPER  # and in paper mode (§1.3)
        assert t.brief is not None and t.brief.brief_date == date(2026, 7, 18)
        assert t.scores[0].alpha_pct == Decimal("0.8")
        assert t.quantity == Decimal("0.2415")
