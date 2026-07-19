"""API dependencies: DB session + quote lookup (patchable in tests)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal

from sqlalchemy.orm import Session

from atlas.core.db import session_scope

QuoteFn = Callable[[list[str]], dict[str, Decimal]]


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def _default_quotes(symbols: list[str]) -> dict[str, Decimal]:
    from atlas.ingest.market import _yf_price

    out: dict[str, Decimal] = {}
    for s in symbols:
        try:
            out[s] = Decimal(str(_yf_price(s)))
        except Exception:  # noqa: BLE001 — stale is flagged client-side, never fatal
            continue
    return out


def get_quotes() -> QuoteFn:
    return _default_quotes
