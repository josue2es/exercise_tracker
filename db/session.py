"""Database session management.

The session factory is configured once per process (app startup, CLI scripts,
tests) via :func:`configure`. Services open one session per call through
:func:`get_session`.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.engine import make_engine

_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None


def configure(database_url: str) -> Engine:
    """(Re)initialize the global engine and session factory. Returns the engine."""
    global _engine, _SessionFactory
    _engine = make_engine(database_url)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database not configured; call db.session.configure() first")
    return _engine


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a database session, committing on success and rolling back on error."""
    if _SessionFactory is None:
        raise RuntimeError("Database not configured; call db.session.configure() first")
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
