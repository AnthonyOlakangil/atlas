"""Database engine/session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from atlas.core.settings import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(db_url: str | None = None) -> Engine:
    global _engine, _session_factory
    if _engine is None or db_url is not None:
        url = db_url or get_settings().db_url
        _engine = create_engine(url, connect_args={"check_same_thread": False})
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope(db_url: str | None = None) -> Iterator[Session]:
    get_engine(db_url)
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Test helper: drop the cached engine so a new db_url takes effect."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
