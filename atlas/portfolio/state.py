"""Portfolio state: positions, settled/unsettled cash, GFV tracking (§2.1, §5 gap 5).

Robinhood exposes no settled-vs-unsettled breakdown, so Atlas derives it from
order history with a chronological ledger. The same ledger detects Good Faith
Violations: buying with unsettled proceeds and selling that position before
those proceeds settle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.models import CashEvent, PaperOrder, Position, TradeAction
from atlas.core.settings import CONFIG_DIR, get_operator_config, get_settings

GFV_RESTRICTION_THRESHOLD = 3  # three GFVs in 12 months → 90-day restriction


class LedgerOverdraw(RuntimeError):
    """The ledger went negative — a bug upstream, since proposals are settled-cash-gated."""


@dataclass(frozen=True)
class Deposit:
    on: date
    amount: Decimal


@dataclass(frozen=True)
class Fill:
    at: datetime
    ticker: str
    side: TradeAction
    quantity: Decimal
    notional: Decimal
    settles_on: date  # for sells: when proceeds become settled


@dataclass
class _Pending:
    settles_on: date
    amount: Decimal
    source_ticker: str


@dataclass
class _BuyLot:
    ticker: str
    quantity: Decimal
    bought_at: datetime
    funding_settles_on: date | None  # None = funded entirely by settled cash


@dataclass(frozen=True)
class GFVEvent:
    ticker: str
    at: datetime


@dataclass
class CashState:
    settled: Decimal
    unsettled: Decimal

    @property
    def total(self) -> Decimal:
        return self.settled + self.unsettled

    def can_fund(self, amount: Decimal) -> bool:
        """§2.1: a buy is fundable only by SETTLED cash."""
        return amount <= self.settled


@dataclass
class Ledger:
    cash: CashState
    gfv_events: list[GFVEvent]
    open_lots: list[_BuyLot]

    def gfv_count(self, within_days: int = 365, as_of: date | None = None) -> int:
        if as_of is None:
            return len(self.gfv_events)
        return sum(1 for g in self.gfv_events if (as_of - g.at.date()).days <= within_days)

    @property
    def gfv_restricted(self) -> bool:
        return len(self.gfv_events) >= GFV_RESTRICTION_THRESHOLD

    def would_cause_gfv(self, ticker: str, sell_on: date) -> bool:
        """Would selling *ticker* on *sell_on* liquidate a lot whose funding hasn't settled?"""
        return any(
            lot.ticker == ticker
            and lot.funding_settles_on is not None
            and lot.funding_settles_on > sell_on
            for lot in self.open_lots
        )


def derive_ledger(deposits: list[Deposit], fills: list[Fill], as_of: date) -> Ledger:
    """Chronologically replay deposits and fills up to *as_of* (inclusive)."""
    settled = Decimal(0)
    pending: list[_Pending] = []
    lots: list[_BuyLot] = []
    gfvs: list[GFVEvent] = []

    events: list[tuple[date, int, object]] = [(d.on, 0, d) for d in deposits if d.on <= as_of]
    events += [(f.at.date(), 1, f) for f in fills if f.at.date() <= as_of]
    events.sort(key=lambda e: (e[0], e[1], getattr(e[2], "at", datetime.min)))

    def settle_through(day: date) -> None:
        nonlocal settled
        still: list[_Pending] = []
        for p in pending:
            if p.settles_on <= day:
                settled += p.amount
            else:
                still.append(p)
        pending[:] = still

    for day, _, ev in events:
        settle_through(day)
        if isinstance(ev, Deposit):
            settled += ev.amount
        elif isinstance(ev, Fill) and ev.side is TradeAction.BUY:
            funding_settles: date | None = None
            if ev.notional <= settled:
                settled -= ev.notional
            else:
                shortfall = ev.notional - settled
                settled = Decimal(0)
                # consume unsettled proceeds FIFO by settle date
                for p in sorted(pending, key=lambda p: p.settles_on):
                    if shortfall <= 0:
                        break
                    take = min(p.amount, shortfall)
                    p.amount -= take
                    shortfall -= take
                    funding_settles = max(funding_settles or p.settles_on, p.settles_on)
                pending[:] = [p for p in pending if p.amount > 0]
                if shortfall > 0:
                    raise LedgerOverdraw(
                        f"buy of {ev.notional} {ev.ticker} on {day} exceeds settled+unsettled cash"
                    )
            lots.append(_BuyLot(ev.ticker, ev.quantity, ev.at, funding_settles))
        elif isinstance(ev, Fill):  # SELL
            remaining = ev.quantity
            for lot in [lot for lot in lots if lot.ticker == ev.ticker]:
                if remaining <= 0:
                    break
                take = min(lot.quantity, remaining)
                if lot.funding_settles_on is not None and lot.funding_settles_on > day:
                    gfvs.append(GFVEvent(ticker=ev.ticker, at=ev.at))
                lot.quantity -= take
                remaining -= take
            lots[:] = [lot for lot in lots if lot.quantity > 0]
            pending.append(_Pending(ev.settles_on, ev.notional, ev.ticker))

    settle_through(as_of)
    return Ledger(
        cash=CashState(settled=settled, unsettled=sum((p.amount for p in pending), Decimal(0))),
        gfv_events=gfvs,
        open_lots=lots,
    )


# --- assembling state from the paper book ------------------------------------


@dataclass
class PositionView:
    ticker: str
    sleeve: str
    shares: Decimal
    cost_basis: Decimal
    price: Decimal
    value: Decimal


@dataclass
class PortfolioState:
    as_of: date
    positions: list[PositionView]
    cash: CashState
    ledger: Ledger

    @property
    def total_value(self) -> Decimal:
        return self.cash.total + sum((p.value for p in self.positions), Decimal(0))

    def sleeve_weights(self) -> dict[str, Decimal]:
        total = self.total_value
        if total == 0:
            return {}
        weights: dict[str, Decimal] = {}
        for p in self.positions:
            weights[p.sleeve] = weights.get(p.sleeve, Decimal(0)) + p.value / total
        return weights

    def position_weight(self, ticker: str) -> Decimal:
        total = self.total_value
        if total == 0:
            return Decimal(0)
        held = sum((p.value for p in self.positions if p.ticker == ticker), Decimal(0))
        return held / total


# --- manual broker mode: the book lives in portfolio.yaml --------------------


class PortfolioConfigError(RuntimeError):
    """portfolio.yaml missing or malformed — the error says exactly what to fix."""


def load_manual_state(
    prices: dict[str, Decimal], as_of: date, path: Path | None = None
) -> PortfolioState:
    """BROKER_MODE=manual: holdings and cash come from a hand-maintained yaml.

    Same PortfolioState type as every other source — sizing, drift, and the
    settled/unsettled-cash rules operate on these numbers unchanged. There is
    no order history, so the GFV ledger is empty (the broker tracks GFVs for
    trades the operator places by hand).
    """
    path = path or (CONFIG_DIR / "portfolio.yaml")
    if not path.exists():
        raise PortfolioConfigError(
            f"{path} not found — copy {path.parent / 'portfolio.example.yaml'} to "
            "portfolio.yaml and fill in your holdings (BROKER_MODE=manual reads it)"
        )
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise PortfolioConfigError(f"{path} is not valid YAML: {exc}") from exc

    cfg = get_operator_config()

    def infer_sleeve(ticker: str) -> str:
        for name, sleeve in cfg.sleeves.items():
            if ticker in sleeve.seed_examples:
                return name
        return "unassigned"

    positions: list[PositionView] = []
    for i, h in enumerate(raw.get("holdings") or []):
        try:
            ticker = str(h["ticker"]).upper()
            shares = Decimal(str(h["shares"]))
            cost_basis = Decimal(str(h["cost_basis"]))  # TOTAL dollars paid for the lot
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise PortfolioConfigError(
                f"{path} holdings[{i}] needs ticker/shares/cost_basis: {h!r}"
            ) from exc
        if shares < 0 or cost_basis < 0:
            raise PortfolioConfigError(f"{path} holdings[{i}]: negative shares/cost_basis")
        price = prices.get(ticker, Decimal(0))
        positions.append(
            PositionView(
                ticker=ticker,
                sleeve=str(h.get("sleeve") or infer_sleeve(ticker)),
                shares=shares,
                cost_basis=cost_basis,
                price=price,
                value=(shares * price).quantize(Decimal("0.01")),
            )
        )

    try:
        cash = CashState(
            settled=Decimal(str(raw.get("settled_cash", 0))),
            unsettled=Decimal(str(raw.get("unsettled_cash", 0))),
        )
    except ArithmeticError as exc:
        raise PortfolioConfigError(f"{path}: settled_cash/unsettled_cash must be numbers") from exc
    if cash.settled < 0 or cash.unsettled < 0:
        raise PortfolioConfigError(f"{path}: cash amounts cannot be negative")

    ledger = Ledger(cash=cash, gfv_events=[], open_lots=[])
    return PortfolioState(as_of=as_of, positions=positions, cash=cash, ledger=ledger)


def load_portfolio_state(
    session: Session,
    prices: dict[str, Decimal],
    as_of: date,
    *,
    broker_mode: str | None = None,
    portfolio_path: Path | None = None,
) -> PortfolioState:
    """The one interface every consumer uses; BROKER_MODE picks the source."""
    mode = broker_mode or get_settings().broker_mode
    if mode == "manual":
        return load_manual_state(prices, as_of, portfolio_path)
    return load_paper_state(session, prices, as_of)


def load_paper_state(
    session: Session, prices: dict[str, Decimal], as_of: date
) -> PortfolioState:
    deposits = [
        Deposit(on=e.on, amount=e.amount)
        for e in session.scalars(select(CashEvent).where(CashEvent.on <= as_of))
    ]
    fills = [
        Fill(
            at=o.placed_at,
            ticker=o.ticker,
            side=o.side,
            quantity=o.quantity,
            notional=o.notional,
            settles_on=o.settles_on,
        )
        for o in session.scalars(
            select(PaperOrder).where(PaperOrder.status == "filled").order_by(PaperOrder.placed_at)
        )
    ]
    ledger = derive_ledger(deposits, fills, as_of)
    positions = [
        PositionView(
            ticker=p.ticker,
            sleeve=p.sleeve,
            shares=p.shares,
            cost_basis=p.cost_basis,
            price=prices.get(p.ticker, Decimal(0)),
            value=(p.shares * prices.get(p.ticker, Decimal(0))).quantize(Decimal("0.01")),
        )
        for p in session.scalars(
            select(Position).where(Position.account == "paper", Position.closed_at.is_(None))
        )
    ]
    return PortfolioState(as_of=as_of, positions=positions, cash=ledger.cash, ledger=ledger)
