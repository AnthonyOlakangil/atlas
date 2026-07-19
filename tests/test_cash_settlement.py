"""Phase 3 gate: T+1 settlement and GFV edge cases (§2.1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from atlas.core.calendar import next_business_day
from atlas.core.models import TradeAction
from atlas.portfolio.state import (
    Deposit,
    Fill,
    LedgerOverdraw,
    derive_ledger,
)

D = Decimal


def _buy(day: date, ticker: str, qty: str, notional: str, hour: int = 10) -> Fill:
    at = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    return Fill(at=at, ticker=ticker, side=TradeAction.BUY, quantity=D(qty), notional=D(notional),
                settles_on=next_business_day(day))


def _sell(day: date, ticker: str, qty: str, notional: str, hour: int = 11) -> Fill:
    at = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    return Fill(at=at, ticker=ticker, side=TradeAction.SELL, quantity=D(qty), notional=D(notional),
                settles_on=next_business_day(day))


MON = date(2026, 7, 13)
TUE = date(2026, 7, 14)
WED = date(2026, 7, 15)
THU = date(2026, 7, 16)
FRI = date(2026, 7, 17)


def test_buy_consumes_settled_cash_immediately() -> None:
    ledger = derive_ledger([Deposit(MON, D("250"))], [_buy(MON, "VOO", "0.2", "150")], MON)
    assert ledger.cash.settled == D("100")
    assert ledger.cash.unsettled == 0


def test_sale_proceeds_unsettled_same_day_settled_next_business_day() -> None:
    deposits = [Deposit(MON, D("100"))]
    fills = [_buy(MON, "NVDA", "0.5", "100"), _sell(TUE, "NVDA", "0.5", "102")]
    same_day = derive_ledger(deposits, fills, TUE)
    assert same_day.cash.settled == 0
    assert same_day.cash.unsettled == D("102")
    next_day = derive_ledger(deposits, fills, WED)
    assert next_day.cash.settled == D("102")
    assert next_day.cash.unsettled == 0


def test_friday_sale_settles_monday() -> None:
    deposits = [Deposit(MON, D("100"))]
    fills = [_buy(MON, "VOO", "1", "100"), _sell(FRI, "VOO", "1", "105")]
    saturday = derive_ledger(deposits, fills, date(2026, 7, 18))
    assert saturday.cash.unsettled == D("105")
    monday = derive_ledger(deposits, fills, date(2026, 7, 20))
    assert monday.cash.settled == D("105")


def test_holiday_pushes_settlement() -> None:
    # Thu Jul 2 2026 sell → Fri Jul 3 is a market holiday → settles Mon Jul 6
    thu = date(2026, 7, 2)
    deposits = [Deposit(date(2026, 7, 1), D("50"))]
    fills = [_buy(date(2026, 7, 1), "SOXX", "0.1", "50"), _sell(thu, "SOXX", "0.1", "51")]
    friday = derive_ledger(deposits, fills, date(2026, 7, 3))
    assert friday.cash.unsettled == D("51")
    monday = derive_ledger(deposits, fills, date(2026, 7, 6))
    assert monday.cash.settled == D("51")


def test_can_fund_only_counts_settled() -> None:
    ledger = derive_ledger(
        [Deposit(MON, D("100"))],
        [_buy(MON, "VOO", "1", "100"), _sell(TUE, "VOO", "1", "105")],
        TUE,
    )
    assert not ledger.cash.can_fund(D("50"))  # $105 exists but none of it settled
    assert ledger.cash.total == D("105")


def test_gfv_detected_and_would_cause_blocks() -> None:
    # classic GFV: sell A, buy B with unsettled proceeds, sell B before A settles
    deposits = [Deposit(MON, D("100"))]
    fills = [
        _buy(MON, "AAA", "1", "100", hour=9),
        _sell(TUE, "AAA", "1", "100", hour=10),
        _buy(TUE, "BBB", "1", "100", hour=11),  # funded by unsettled AAA proceeds
    ]
    ledger = derive_ledger(deposits, fills, TUE)
    assert ledger.gfv_count() == 0  # not yet — violation happens on the early sell
    assert ledger.would_cause_gfv("BBB", TUE) is True  # hard-block this proposal (§2.1)
    assert ledger.would_cause_gfv("BBB", WED) is False  # settled by then — fine

    ledger2 = derive_ledger(deposits, [*fills, _sell(TUE, "BBB", "1", "101", hour=15)], TUE)
    assert ledger2.gfv_count() == 1
    assert not ledger2.gfv_restricted


def test_three_gfvs_trigger_restriction_flag() -> None:
    deposits = [Deposit(MON, D("100"))]
    fills: list[Fill] = []
    day = MON
    for i in range(3):
        t = f"T{i}"
        fills += [
            _buy(day, t, "1", "100", hour=9),
            _sell(next_business_day(day), t, "1", "100", hour=10),
            _buy(next_business_day(day), f"U{i}", "1", "100", hour=11),
            _sell(next_business_day(day), f"U{i}", "1", "100", hour=12),  # GFV
        ]
        # U-position sold; proceeds settle, then cycle repeats two days later
        day = next_business_day(next_business_day(day))
    ledger = derive_ledger(deposits, fills, date(2026, 8, 3))
    assert ledger.gfv_count() == 3
    assert ledger.gfv_restricted


def test_buy_settled_funded_never_flags_gfv() -> None:
    deposits = [Deposit(MON, D("500"))]
    fills = [
        _buy(MON, "VOO", "1", "200"),
        _sell(TUE, "VOO", "1", "210"),
        _buy(TUE, "NVDA", "1", "200"),  # still fundable from remaining settled $300
        _sell(TUE, "NVDA", "1", "205", hour=15),
    ]
    ledger = derive_ledger(deposits, fills, WED)
    assert ledger.gfv_count() == 0


def test_overdraw_raises() -> None:
    with pytest.raises(LedgerOverdraw):
        derive_ledger([Deposit(MON, D("50"))], [_buy(MON, "VOO", "1", "100")], MON)


def test_same_day_round_trip_burns_settlement_but_no_gfv_when_settled_funded() -> None:
    deposits = [Deposit(MON, D("100"))]
    fills = [_buy(MON, "MU", "1", "100", hour=9), _sell(MON, "MU", "1", "99", hour=15)]
    ledger = derive_ledger(deposits, fills, MON)
    assert ledger.gfv_count() == 0
    assert ledger.cash.settled == 0
    assert ledger.cash.unsettled == D("99")  # proceeds locked until T+1
