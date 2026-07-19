"""The paper book: PAPER mode intercepts `place_*` and fills orders here (§1.3).

Mirrors the broker's own hard rules so paper results stay honest:
- fractional quantities require MARKET orders (Robinhood platform rule, §2.1)
- fills record T+1 settlement dates so cash math behaves like a real cash account
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.core.calendar import next_business_day
from atlas.core.models import OrderType, PaperOrder, Position, TradeAction

PriceProvider = Callable[[str], Decimal]


class PaperOrderRejected(RuntimeError):
    """The paper book rejects what the real broker would reject."""


def _is_fractional(quantity: Decimal) -> bool:
    return quantity != quantity.to_integral_value()


class PaperBook:
    def __init__(self, session: Session, price_provider: PriceProvider) -> None:
        self._session = session
        self._price = price_provider

    def place_equity_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal | None = None,
        amount: Decimal | None = None,
        limit_price: Decimal | None = None,
        ticket_id: int | None = None,
    ) -> dict[str, Any]:
        action = TradeAction(side.upper())
        otype = OrderType(order_type)
        price = self._price(symbol)
        if price <= 0:
            raise PaperOrderRejected(f"no valid price for {symbol}")

        if quantity is None:
            if amount is None:
                raise PaperOrderRejected("must provide quantity or amount")
            quantity = (amount / price).quantize(Decimal("0.000001"))

        # Broker rule: fractional quantities are MARKET-only (§2.1).
        if _is_fractional(quantity) and otype is not OrderType.MARKET:
            raise PaperOrderRejected(
                f"fractional quantity {quantity} requires a market order — Robinhood "
                f"does not support fractional {otype.value} orders"
            )
        if otype in (OrderType.LIMIT, OrderType.STOP_LIMIT) and limit_price is None:
            raise PaperOrderRejected(f"{otype.value} order requires limit_price")

        fill_price = price
        if otype is OrderType.LIMIT and limit_price is not None:
            # paper fill model: limit fills at min(market, limit) for buys, max for sells
            fill_price = min(price, limit_price) if action is TradeAction.BUY else max(price, limit_price)

        notional = (quantity * fill_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        placed_at = datetime.now(UTC)
        order = PaperOrder(
            placed_at=placed_at,
            ticker=symbol,
            side=action,
            order_type=otype,
            quantity=quantity,
            notional=notional,
            limit_price=limit_price,
            fill_price=fill_price,
            status="filled",
            settles_on=next_business_day(placed_at.date()),
            ticket_id=ticket_id,
        )
        self._session.add(order)
        self._apply_fill(symbol, action, quantity, notional)
        self._session.flush()
        return {
            "order_id": f"paper-{order.id}",
            "state": "filled",
            "symbol": symbol,
            "side": action.value,
            "order_type": otype.value,
            "quantity": str(quantity),
            "fill_price": str(fill_price),
            "notional": str(notional),
            "settles_on": order.settles_on.isoformat(),
            "account": "paper",
        }

    def cancel_equity_order(self, *, order_id: str) -> dict[str, Any]:
        pk = int(order_id.removeprefix("paper-"))
        order = self._session.get(PaperOrder, pk)
        if order is None:
            raise PaperOrderRejected(f"unknown paper order {order_id}")
        # paper fills are immediate, so cancel only flips unfilled states; report honestly
        return {"order_id": order_id, "state": order.status}

    def _apply_fill(
        self, symbol: str, action: TradeAction, quantity: Decimal, notional: Decimal
    ) -> None:
        pos = self._session.scalars(
            select(Position).where(
                Position.ticker == symbol,
                Position.account == "paper",
                Position.closed_at.is_(None),
            )
        ).first()
        if action is TradeAction.BUY:
            if pos is None:
                self._session.add(
                    Position(
                        ticker=symbol,
                        sleeve="unassigned",
                        shares=quantity,
                        cost_basis=notional,
                        account="paper",
                    )
                )
            else:
                pos.shares = pos.shares + quantity
                pos.cost_basis = pos.cost_basis + notional
        else:
            if pos is None or pos.shares < quantity:
                held = Decimal(0) if pos is None else pos.shares
                raise PaperOrderRejected(
                    f"cannot sell {quantity} {symbol}: paper book holds {held} (long-only, no shorting)"
                )
            fraction_kept = (pos.shares - quantity) / pos.shares
            pos.cost_basis = (pos.cost_basis * fraction_kept).quantize(Decimal("0.01"))
            pos.shares = pos.shares - quantity
            if pos.shares == 0:
                pos.closed_at = datetime.now(UTC)
