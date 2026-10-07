"""API contract tests (Phase 8 backend): all views' data endpoints, offline."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from atlas.api.app import create_app
from atlas.api.deps import get_quotes, get_session
from atlas.core.models import Brief, CashEvent, GlossaryTerm, Snapshot, TicketStatus
from tests.test_execution import make_ticket

PRICES = {"VOO": Decimal("686.90"), "SPY": Decimal("748.30"), "QQQ": Decimal("601.10")}


@pytest.fixture
def client(db_session: Session) -> TestClient:
    app = create_app()

    def override_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_quotes] = lambda: (
        lambda symbols: {s: PRICES[s] for s in symbols if s in PRICES}
    )
    return TestClient(app)


def test_portfolio_endpoint(client: TestClient, db_session: Session) -> None:
    db_session.add(CashEvent(on=date(2026, 7, 15), amount=Decimal("250")))
    db_session.flush()
    resp = client.get("/api/portfolio")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_value"] == 250.0
    assert data["settled_cash"] == 250.0
    assert set(data["sleeves"]) == {"core", "satellite_ai_semis", "diversifier", "speculative"}
    assert data["sleeves"]["core"]["band"] == [0.5, 0.6]


def test_tickets_endpoint_and_approve_flow(client: TestClient, db_session: Session) -> None:
    t = make_ticket(db_session)
    resp = client.get("/api/tickets")
    assert resp.status_code == 200
    [row] = resp.json()
    assert row["ticker"] == "VOO" and row["status"] == "PENDING_APPROVAL"
    assert row["portfolio_impact"] and row["bear_case"] and row["falsifier"]

    approved = client.post(f"/api/tickets/{t.id}/approve")
    assert approved.status_code == 200
    body = approved.json()
    assert "atlas execute --ticket-id" in body["next_step"]  # browser NEVER executes
    db_session.refresh(t)
    assert t.status is TicketStatus.APPROVED

    again = client.post(f"/api/tickets/{t.id}/approve")
    assert again.status_code == 409  # already approved — idempotence guarded


def test_reject_flow(client: TestClient, db_session: Session) -> None:
    t = make_ticket(db_session)
    resp = client.post(f"/api/tickets/{t.id}/reject")
    assert resp.status_code == 200
    db_session.refresh(t)
    assert t.status is TicketStatus.REJECTED


def test_brief_endpoint(client: TestClient, db_session: Session) -> None:
    assert client.get("/api/brief/latest").status_code == 404  # honest empty state
    db_session.add(
        Brief(brief_date=date(2026, 7, 17), path="x", content_md="# Brief\nA limit order test.",
              status="APPROVED", critic_rounds=1)
    )
    db_session.add(
        GlossaryTerm(term="limit order", definition="def", first_shown_at=datetime.now(UTC))
    )
    db_session.flush()
    data = client.get("/api/brief/latest").json()
    assert data["markdown"].startswith("# Brief")
    assert data["glossary"] == {"limit order": "def"}


def test_market_pulse_marks_stale(client: TestClient) -> None:
    data = client.get("/api/market-pulse").json()
    rows = {r["symbol"]: r for r in data["rows"]}
    assert rows["SPY"]["price"] == 748.30 and rows["SPY"]["stale"] is False
    assert rows["^VIX"]["price"] is None and rows["^VIX"]["stale"] is True  # never silent staleness


def test_performance_and_track_record(client: TestClient, db_session: Session) -> None:
    """Task 1b: this previously asserted atlas == 2.0 from total_value 250 -> 255
    on snapshots carrying no positions at all. That is the total-vs-total
    mechanism — `total_value` includes the $10 cash, so the figure blended a cash
    balance into a return. Snapshots now carry real positions and the series is a
    chained per-position price return: +2%, then -0.98%, compounding to +1%."""
    prices = ["100.00", "102.00", "101.00"]
    for i, (px, spy) in enumerate(zip(prices, [748, 750, 749], strict=True)):
        db_session.add(
            Snapshot(
                taken_at=datetime(2026, 7, 14 + i, 20, 0, tzinfo=UTC),
                total_value=Decimal(px) + Decimal("10"),
                settled_cash=Decimal("10"), unsettled_cash=Decimal(0),
                spy_close=Decimal(spy),
                positions={"VOO": {"shares": "1.0", "price": px, "value": px}},
            )
        )
    db_session.flush()
    perf = client.get("/api/performance", params={"window": "ALL"}).json()
    assert len(perf["series"]) == 3
    assert perf["series"][0]["atlas"] == 0.0
    assert perf["series"][1]["atlas"] == 2.0  # 100 -> 102
    assert perf["series"][2]["atlas"] == 1.0  # 1.02 * (101/102) - 1

    track = client.get("/api/track-record").json()
    assert track["stats"]["paper_days_logged"] == 3
    assert len(track["drawdown"]) == 3
    assert track["drawdown"][2]["drawdown"] < 0  # 102 -> 101 is a real drawdown
