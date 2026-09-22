"""Apply Alembic migrations to the configured database."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from db.session import get_engine

ROOT = Path(__file__).resolve().parents[1]


def ensure_schema(*, upgrade: bool = True) -> None:
    """Create the schema if the database has no tables yet.

    In production, deployments run ``alembic upgrade head`` explicitly; this
    makes fresh databases (first start, tests, CLI runs) work out of the box.
    """
    from sqlalchemy import inspect

    inspector = inspect(get_engine())
    if inspector.get_table_names():
        if not upgrade:
            return
    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    alembic_cfg.set_main_option(
        "sqlalchemy.url", get_engine().url.render_as_string(hide_password=False)
    )
    command.upgrade(alembic_cfg, "head")
