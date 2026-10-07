"""BROKER_MODE gate: portfolio.yaml parsing, mode switching, execution-inert manual path."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from atlas.core.models import CashEvent, TicketStatus
from atlas.core.settings import OperatorConfig, get_settings
from atlas.execution.approval import (
    ApprovalError,
    BrokerNotConnected,
    approve_ticket,
    execute_ticket,
    record_manual_fill,
)
from atlas.portfolio.drift import compute_drift
from atlas.portfolio.sizing import size_buy
from atlas.portfolio.state import (
    PortfolioConfigError,
    load_manual_state,
    load_portfolio_state,
)
from tests.test_execution import make_ticket

CFG = OperatorConfig.load()
TODAY = date(2026, 7, 20)
PRICES = {
    "VOO": Decimal("686.90"),
    "SMH": Decimal("420.00"),
    "QQQM": Decimal("230.50"),
    "GLDM": Decimal("80.15"),
}

YAML = """\
holdings:
  - {ticker: VOO, sleeve: core, shares: 0.3500, cost_basis: 152.10}
  - {ticker: SMH, sleeve: satellite_ai_semis, shares: 0.2000, cost_basis: 84.00}
settled_cash: 45.00
unsettled_cash: 12.00
"""


@pytest.fixture
def portfolio_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "portfolio.yaml"
    p.write_text(YAML)
    return p


@pytest.fixture
def manual_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BROKER_MODE", "manual")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- portfolio.yaml parsing ---------------------------------------------------


def test_manual_state_parses_yaml(portfolio_yaml: Path) -> None:
    state = load_manual_state(PRICES, TODAY, portfolio_yaml)
    assert state.cash.settled == Decimal("45.00")
    assert state.cash.unsettled == Decimal("12.00")
    assert state.cash.total == Decimal("57.00")

    voo, smh = state.positions
    assert (voo.ticker, voo.shares, voo.cost_basis) == ("VOO", Decimal("0.3500"), Decimal("152.10"))
    assert voo.value == (Decimal("0.35") * PRICES["VOO"]).quantize(Decimal("0.01"))
    assert voo.sleeve == "core"  # explicit; inference was removed (Task 1a)
    assert smh.sleeve == "satellite_ai_semis"
    assert state.total_value == voo.value + smh.value + Decimal("57.00")


def test_off_seed_ticker_is_accepted_in_any_declared_sleeve(tmp_path: Path) -> None:
    """§2.0's open universe is preserved: validation checks the SLEEVE NAME against
    operator.yaml, never the TICKER against seed_examples. ZZZT appears in no seed
    list and still loads, because seed_examples are starting points, not a whitelist.

    This replaces an assertion that an unknown ticker resolved to "unassigned" and
    was "not rejected". That conflated two things: an off-seed ticker (fine) with a
    holding carrying no sleeve (not fine — it is what silently dropped five of the
    operator's twelve holdings out of the band math)."""
    p = tmp_path / "portfolio.yaml"
    p.write_text(
        "holdings:\n"
        "  - {ticker: ANET, shares: 1, cost_basis: 97.31, sleeve: satellite_ai_semis}\n"
        "  - {ticker: ZZZT, shares: 2, cost_basis: 20.00, sleeve: speculative}\n"
        "settled_cash: 10\n"
    )
    state = load_manual_state({"ANET": Decimal("100")}, TODAY, p)
    assert state.positions[0].sleeve == "satellite_ai_semis"
    assert state.positions[1].sleeve == "speculative"
    assert state.positions[1].value == 0  # no price this cycle → valued at 0, flagged upstream


def test_holding_without_a_sleeve_is_rejected_not_defaulted(tmp_path: Path) -> None:
    p = tmp_path / "portfolio.yaml"
    p.write_text(
        "holdings:\n  - {ticker: ZZZT, shares: 2, cost_basis: 20.00}\nsettled_cash: 10\n"
    )
    with pytest.raises(PortfolioConfigError, match="sleeve"):
        load_manual_state({"ZZZT": Decimal("10")}, TODAY, p)


def test_manual_state_missing_file_points_to_example(tmp_path: Path) -> None:
    with pytest.raises(PortfolioConfigError, match="portfolio.example.yaml"):
        load_manual_state(PRICES, TODAY, tmp_path / "portfolio.yaml")


@pytest.mark.parametrize(
    "bad,match",
    [
        ("holdings:\n  - {ticker: VOO}\nsettled_cash: 1\n", r"holdings\[0\]"),
        ("holdings: []\nsettled_cash: -5\n", "negative"),
        ("holdings:\n  - {ticker: VOO, shares: -1, cost_basis: 5}\n", "negative"),
        ("{[not yaml", "not valid YAML"),
    ],
)
def test_manual_state_malformed_yaml_errors(tmp_path: Path, bad: str, match: str) -> None:
    p = tmp_path / "portfolio.yaml"
    p.write_text(bad)
    with pytest.raises(PortfolioConfigError, match=match):
        load_manual_state(PRICES, TODAY, p)


# --- mode switching behind one interface -------------------------------------


def test_load_portfolio_state_dispatches_by_mode(
    db_session: Session, portfolio_yaml: Path
) -> None:
    db_session.add(CashEvent(on=TODAY, amount=Decimal("250")))
    db_session.flush()

    manual = load_portfolio_state(
        db_session, PRICES, TODAY, broker_mode="manual", portfolio_path=portfolio_yaml
    )
    assert manual.cash.settled == Decimal("45.00")  # yaml, not the DB

    connected = load_portfolio_state(db_session, PRICES, TODAY, broker_mode="connected")
    assert connected.cash.settled == Decimal("250")  # the paper ledger, not the yaml


def test_default_dispatch_reads_settings(
    db_session: Session, portfolio_yaml: Path, manual_mode: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = load_portfolio_state(db_session, PRICES, TODAY, portfolio_path=portfolio_yaml)
    assert state.cash.settled == Decimal("45.00")  # BROKER_MODE=manual won the dispatch


# --- sizing / drift / cash rules operate identically on manual numbers --------


def test_sizing_enforces_settled_cash_from_yaml(portfolio_yaml: Path) -> None:
    state = load_manual_state(PRICES, TODAY, portfolio_yaml)
    # empty diversifier sleeve: the band allows ~$57, but only $45 is settled —
    # cash binds, and the $12 unsettled in the yaml correctly does not help
    too_big = size_buy(
        ticker="GLDM", sleeve="diversifier", price=PRICES["GLDM"],
        target_dollars=Decimal("60"), state=state, cfg=CFG,
    )
    assert not too_big.approved and too_big.watch_item
    assert any("watch item" in r for r in too_big.reasons)

    ok = size_buy(
        ticker="GLDM", sleeve="diversifier", price=PRICES["GLDM"],
        target_dollars=Decimal("12"), state=state, cfg=CFG,
    )
    assert ok.approved and ok.is_fractional
    assert ok.cash_before == Decimal("45.00")
    assert ok.cash_after == Decimal("33.00")


def test_drift_computed_over_manual_holdings(portfolio_yaml: Path) -> None:
    state = load_manual_state(PRICES, TODAY, portfolio_yaml)
    drift = {d.sleeve: d for d in compute_drift(state, CFG)}
    assert drift["core"].actual_pct > 0
    assert drift["satellite_ai_semis"].actual_pct > 0
    # bands, statuses and dollar deltas all present — same machinery as connected
    assert all(d.band_hi > d.band_lo for d in drift.values() if d.sleeve != "speculative")


# --- execution is inert in manual mode ----------------------------------------


def test_execute_reports_broker_not_connected(
    db_session: Session, manual_mode: None, portfolio_yaml: Path
) -> None:
    ticket = make_ticket(db_session)
    approve_ticket(ticket.id, db_session)
    with pytest.raises(BrokerNotConnected) as exc:
        execute_ticket(ticket.id, session=db_session, confirm=lambda p: True)
    msg = str(exc.value)
    assert "broker not connected" in msg
    assert "Robinhood how-to" in msg  # points at the ticket's manual instructions
    assert f"atlas filled --ticket-id {ticket.id}" in msg
    assert ticket.status is TicketStatus.APPROVED  # nothing consumed, nothing placed


def test_execute_still_requires_approval_even_when_inert(
    db_session: Session, manual_mode: None
) -> None:
    ticket = make_ticket(db_session)  # PENDING_APPROVAL
    with pytest.raises(ApprovalError, match="only an APPROVED ticket"):
        execute_ticket(ticket.id, session=db_session, confirm=lambda p: True)


def test_record_manual_fill_enables_scoring(db_session: Session, manual_mode: None) -> None:
    ticket = make_ticket(db_session)
    with pytest.raises(ApprovalError, match="only an APPROVED ticket"):
        record_manual_fill(ticket.id, Decimal("686.42"), db_session)  # gate applies here too

    approve_ticket(ticket.id, db_session)
    record_manual_fill(ticket.id, Decimal("686.42"), db_session)
    assert ticket.status is TicketStatus.EXECUTED
    assert ticket.fill_price == Decimal("686.42")
    assert ticket.executed_at is not None

    with pytest.raises(ApprovalError):
        record_manual_fill(ticket.id, Decimal("1"), db_session)  # cannot double-record


# --- API surfaces the mode -----------------------------------------------------


@pytest.fixture
def api_client(db_session: Session) -> TestClient:
    from atlas.api.app import create_app
    from atlas.api.deps import get_quotes, get_session

    app = create_app()

    def override_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_quotes] = lambda: (lambda symbols: {})
    return TestClient(app)


def test_api_meta_and_manual_approve_flow(
    api_client: TestClient, db_session: Session, manual_mode: None
) -> None:
    assert api_client.get("/api/meta").json()["broker_mode"] == "manual"

    t = make_ticket(db_session)
    [row] = api_client.get("/api/tickets").json()
    assert "Robinhood" in row["robinhood_howto"]  # the place-it-yourself app path ships with the ticket
    assert "Buy in Dollars" in row["robinhood_howto"]  # fractional fork rendered

    body = api_client.post(f"/api/tickets/{t.id}/approve").json()
    assert "yourself in the Robinhood app" in body["next_step"]
    assert "atlas filled" in body["next_step"]
    assert "never places orders" in body["note"]


def test_api_meta_connected(api_client: TestClient) -> None:
    assert api_client.get("/api/meta").json()["broker_mode"] == "connected"  # conftest pin
