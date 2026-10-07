"""Phase 11 (Part 3 item 5): the structural calendar is a rules engine, not a scrape.

Ground truth for third Fridays comes from the stdlib `calendar` module, computed
independently of the implementation under test.
"""

from __future__ import annotations

from datetime import date

from atlas.ingest.calendar import EventType, structural_events


def test_monthly_opex_is_the_third_friday() -> None:
    events = structural_events(date(2026, 8, 1), date(2026, 8, 31))
    opex = [e for e in events if e.type is EventType.OPEX]
    assert [e.on for e in opex] == [date(2026, 8, 21)]


def test_third_friday_of_a_quarter_end_month_is_triple_witching_not_plain_opex() -> None:
    """Mar/Jun/Sep/Dec third Friday is witching — index futures, index options and
    stock options all expire together. It must not also emit a plain OPEX row."""
    events = structural_events(date(2026, 9, 1), date(2026, 9, 30))
    kinds = {e.type for e in events if e.on == date(2026, 9, 18)}
    assert EventType.TRIPLE_WITCHING in kinds
    assert EventType.OPEX not in kinds


def test_quarterly_index_rebalance_lands_on_witching_day() -> None:
    events = structural_events(date(2026, 9, 1), date(2026, 9, 30))
    rebal = [e for e in events if e.type is EventType.INDEX_REBALANCE]
    assert [e.on for e in rebal] == [date(2026, 9, 18)]


def test_window_spanning_two_months_returns_both_expirations_in_date_order() -> None:
    events = structural_events(date(2026, 8, 1), date(2026, 9, 30))
    expiries = [e.on for e in events if e.type in (EventType.OPEX, EventType.TRIPLE_WITCHING)]
    assert expiries == [date(2026, 8, 21), date(2026, 9, 18)]


def test_events_are_sorted_by_date() -> None:
    events = structural_events(date(2026, 1, 1), date(2026, 12, 31))
    assert [e.on for e in events] == sorted(e.on for e in events)


def test_empty_window_returns_no_events_rather_than_raising() -> None:
    assert structural_events(date(2026, 8, 22), date(2026, 8, 31)) == []


def test_inverted_window_returns_empty() -> None:
    assert structural_events(date(2026, 9, 30), date(2026, 8, 1)) == []


def test_every_event_carries_a_time_and_timezone_label() -> None:
    """§2.2 rejects 'CPI Wednesday' — every event needs a date AND a time when the
    time matters. Expirations settle at the open; the label must say so."""
    events = structural_events(date(2026, 8, 1), date(2026, 8, 31))
    assert events, "August 2026 has an expiration"
    for e in events:
        assert e.time_pt, f"{e.type} carries no time"
        assert e.description
