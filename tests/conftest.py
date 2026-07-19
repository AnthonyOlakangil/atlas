from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from atlas.core.models import Base
from atlas.core.settings import get_settings


@pytest.fixture(autouse=True)
def connected_broker(monkeypatch: pytest.MonkeyPatch):
    """The pre-flag suite exercises the connected path; it must stay fully intact.

    Manual-mode tests (test_broker_mode.py) override BROKER_MODE per test.
    """
    monkeypatch.setenv("BROKER_MODE", "connected")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},  # FastAPI TestClient uses worker threads
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
