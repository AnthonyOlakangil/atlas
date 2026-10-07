"""Structural market calendar — a rules engine, not a scrape (Part 3 item 5).

OpEx, triple witching and quarterly index rebalances are deterministic functions
of the calendar, so deriving them beats scraping a source that can 404 on us.
This module has no network dependency and therefore no degradation path: it
cannot fail for the operator's next 14 days.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date
from enum import StrEnum

from pydantic import BaseModel

# Equity options stop trading at the closing bell: 16:00 ET = 13:00 PT (§12 is
# Pacific throughout). Quarterly index rebalances are also effective at the close.
_CLOSE_PT = "13:00 PT"

_QUARTER_END_MONTHS = frozenset({3, 6, 9, 12})


class EventType(StrEnum):
    OPEX = "opex"
    TRIPLE_WITCHING = "triple_witching"
    INDEX_REBALANCE = "index_rebalance"


class CalendarEvent(BaseModel):
    on: date
    time_pt: str
    type: EventType
    title: str
    description: str


def _third_friday(year: int, month: int) -> date:
    fridays = [
        day
        for week in _calendar.monthcalendar(year, month)
        if (day := week[_calendar.FRIDAY]) != 0
    ]
    return date(year, month, fridays[2])


def _months_between(start: date, end: date) -> list[tuple[int, int]]:
    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def structural_events(start: date, end: date) -> list[CalendarEvent]:
    """Every derivable structural event with `start <= on <= end`, date-ordered.

    An inverted or empty window yields `[]` — never an exception, because §2.2's
    calendar table asks for this range on every brief.
    """
    if start > end:
        return []

    events: list[CalendarEvent] = []
    for year, month in _months_between(start, end):
        expiry = _third_friday(year, month)
        if not (start <= expiry <= end):
            continue

        if month in _QUARTER_END_MONTHS:
            events.append(
                CalendarEvent(
                    on=expiry,
                    time_pt=_CLOSE_PT,
                    type=EventType.TRIPLE_WITCHING,
                    title="Triple witching",
                    description=(
                        "Stock index futures, stock index options and single-stock "
                        "options all expire together — volume and volatility spike "
                        "into the close."
                    ),
                )
            )
            events.append(
                CalendarEvent(
                    on=expiry,
                    time_pt=_CLOSE_PT,
                    type=EventType.INDEX_REBALANCE,
                    title="Quarterly index rebalance effective",
                    description=(
                        "S&P and other major index reconstitutions take effect at the "
                        "close, forcing index funds to trade the adds and drops."
                    ),
                )
            )
        else:
            events.append(
                CalendarEvent(
                    on=expiry,
                    time_pt=_CLOSE_PT,
                    type=EventType.OPEX,
                    title="Monthly options expiration",
                    description=(
                        "Monthly equity options expire at the close; pinning and "
                        "dealer hedging can pull prices toward large strikes."
                    ),
                )
            )

    return sorted(events, key=lambda e: (e.on, e.type.value))
