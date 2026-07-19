"""US equity market calendar helpers (T+1 settlement math needs business days)."""

from __future__ import annotations

from datetime import date, timedelta

# NYSE full-closure holidays. Extend annually; scoring flags dates outside coverage.
US_MARKET_HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2026
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        # 2027
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),
        date(2027, 7, 5),
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),
    }
)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in US_MARKET_HOLIDAYS


def next_business_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def add_trading_days(d: date, n: int) -> date:
    cur = d
    for _ in range(n):
        cur = next_business_day(cur)
    return cur
