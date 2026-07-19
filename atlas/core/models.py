"""SQLAlchemy models: Position, Ticket, Brief, Document, Score, Snapshot."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


class TrustTier(enum.StrEnum):
    primary = "primary"
    secondary = "secondary"
    untrusted = "untrusted"


class TicketStatus(enum.StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class TradeAction(enum.StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(enum.StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LIMIT = "stop_limit"


class Mode(enum.StrEnum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    sleeve: Mapped[str] = mapped_column(String(40))
    shares: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    cost_basis: Mapped[Decimal] = mapped_column(Numeric(18, 4))  # total dollars paid
    account: Mapped[str] = mapped_column(String(20), default="paper")  # paper | agentic | <read-only acct#>
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    mode: Mapped[Mode] = mapped_column(default=Mode.PAPER)
    status: Mapped[TicketStatus] = mapped_column(default=TicketStatus.PENDING_APPROVAL, index=True)

    ticker: Mapped[str] = mapped_column(String(12))
    action: Mapped[TradeAction]
    sleeve: Mapped[str] = mapped_column(String(40))
    order_type: Mapped[OrderType]
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))  # fractional to 4dp
    dollar_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=None)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=None)
    is_fractional: Mapped[bool] = mapped_column(default=False)

    # Cash math shown in the brief (§2.1): settled cash before → spend → after
    cash_before: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    cash_after: Mapped[Decimal] = mapped_column(Numeric(18, 2))

    # Narrative fields — every one required in the brief (§8.4)
    thesis: Mapped[str] = mapped_column(Text)
    catalyst: Mapped[str] = mapped_column(Text)
    risk: Mapped[str] = mapped_column(Text)
    bear_case: Mapped[str] = mapped_column(Text)
    falsifier: Mapped[str] = mapped_column(Text)
    time_horizon: Mapped[str] = mapped_column(String(60))
    sizing_rationale: Mapped[str] = mapped_column(Text)
    portfolio_impact: Mapped[str] = mapped_column(Text)
    learning_note: Mapped[str] = mapped_column(Text, default="")

    # App-managed exit plan (no bracket orders on Robinhood — §5 gap 1)
    exit_stop: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=None)
    exit_target: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=None)

    review_warnings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), default=None)

    brief_id: Mapped[int | None] = mapped_column(ForeignKey("briefs.id"), default=None)
    brief: Mapped[Brief | None] = relationship(back_populates="tickets")
    scores: Mapped[list[Score]] = relationship(back_populates="ticket")


class Brief(Base):
    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(primary_key=True)
    brief_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="preopen")  # preopen | postclose
    path: Mapped[str] = mapped_column(String(255))
    content_md: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT | REJECTED | APPROVED
    critic_rounds: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    tickets: Mapped[list[Ticket]] = relationship(back_populates="brief")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(80), index=True)
    source_url: Mapped[str] = mapped_column(String(1024))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    trust_tier: Mapped[TrustTier]
    raw_text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    injection_flags: Mapped[list[str]] = mapped_column(JSON, default=list)


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    window: Mapped[str] = mapped_column(String(8))  # +1d | +5d | +20d
    ticket_return_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    spy_return_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    alpha_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ticket: Mapped[Ticket] = relationship(back_populates="scores")


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    mode: Mapped[Mode] = mapped_column(default=Mode.PAPER)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    settled_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    unsettled_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    spy_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=None)
    positions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PaperOrder(Base):
    """Paper-book order history — the paper analogue of `get_equity_orders`.

    Settled/unsettled cash (§2.1) is derived from this history, mirroring how
    live mode derives it from the MCP order history (§5 gap 5).
    """

    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    side: Mapped[TradeAction]
    order_type: Mapped[OrderType]
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    notional: Mapped[Decimal] = mapped_column(Numeric(18, 2))  # dollars in/out at fill
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), default=None)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    status: Mapped[str] = mapped_column(String(16), default="filled")  # filled | cancelled
    settles_on: Mapped[date] = mapped_column(Date)  # T+1: proceeds usable from this date
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), default=None)


class CashEvent(Base):
    """Deposits/withdrawals into the paper (or agentic) account — the ledger's cash inflows."""

    __tablename__ = "cash_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    on: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))  # negative = withdrawal
    note: Mapped[str] = mapped_column(String(200), default="")
    account: Mapped[str] = mapped_column(String(20), default="paper")


class GlossaryTerm(Base):
    """Terms already shown to the operator — powers first-use inline definitions (§7 reference corpus)."""

    __tablename__ = "glossary_terms"

    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    definition: Mapped[str] = mapped_column(Text)
    first_shown_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_url: Mapped[str] = mapped_column(String(1024), default="")
