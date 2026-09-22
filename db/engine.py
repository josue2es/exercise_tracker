"""Engine factory with the SQLite pragmas required by the spec on every connection."""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

BUSY_TIMEOUT_MS = 5000


def make_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine for SQLite with pragmas applied per connection."""
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # pragma: no cover - driver callback
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()

    return engine
