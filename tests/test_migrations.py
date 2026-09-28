"""Migrations run against databases that already hold data (an empty table
hides bugs such as a NOT NULL column without a default for existing rows)."""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from db.upgrade import ROOT


def _config(db_path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _seed_exercise_row(engine):
    with engine.begin() as conn:
        for sql in (
            "INSERT INTO users (id, email, display_name, password_hash, role, unit_pref, time_zone, "
            "is_active, created_at) VALUES (1, 'a@x.com', 'A', 'x', 'member', 'kg', 'UTC', 1, '2026-01-01')",
            "INSERT INTO exercises (id, source, source_id, name, body_parts, primary_muscles, secondary_muscles, "
            "equipment, instructions, media, search_text, updated_at) "
            "VALUES (1, 's', '1', 'Bench', '[]', '[]', '[]', '[]', '[]', '[]', '', '2026-01-01')",
            "INSERT INTO routines (id, user_id, name, created_at, updated_at) VALUES (1, 1, 'R', '2026-01-01', '2026-01-01')",
            "INSERT INTO workouts (id, user_id, routine_id, position, name, created_at, updated_at) "
            "VALUES (1, 1, 1, 0, 'D', '2026-01-01', '2026-01-01')",
            "INSERT INTO workout_exercises (id, workout_id, exercise_id, position, target_sets, "
            "target_reps_min, target_reps_max) VALUES (1, 1, 1, 0, 3, 8, 12)",
        ):
            conn.execute(text(sql))


def _state(engine):
    with engine.connect() as conn:
        row = conn.execute(text("SELECT rest_seconds, rir, amrap FROM workout_exercises")).one()
        tmp = conn.execute(
            text("SELECT count(*) FROM sqlite_master WHERE name = '_alembic_tmp_workout_exercises'")
        ).scalar()
    return tuple(row), tmp


def test_exercise_fields_migrate_existing_rows(tmp_path):
    cfg = _config(tmp_path / "old.db")
    command.upgrade(cfg, "8f3a2c61d7e5")  # routines, before rest/RIR/AMRAP
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _seed_exercise_row(engine)

    command.upgrade(cfg, "head")
    assert _state(engine) == ((None, None, 0), 0)
    engine.dispose()


def test_amrap_recovers_from_the_first_failed_attempt(tmp_path):
    """The first AMRAP migration failed on existing rows and left SQLite's batch
    temp table behind at revision 6c2d8a4e9f13; upgrading again must work."""
    cfg = _config(tmp_path / "old.db")
    command.upgrade(cfg, "6c2d8a4e9f13")
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _seed_exercise_row(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE _alembic_tmp_workout_exercises (id INTEGER)"))

    command.upgrade(cfg, "head")
    assert _state(engine) == ((None, None, 0), 0)
    engine.dispose()
