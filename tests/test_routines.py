"""Tests for routines: a routine groups ordered days (workouts), sessions are
named after routine and day, and the migration turns old workouts into
single-day routines."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text

import services.routines as routines
import services.sessions as sessions
import services.workouts as workouts
from db.models import TrainingSession, utcnow
from db.session import get_session
from services.errors import NotFoundError, ScopeError
from tests.conftest import make_exercise, make_user
from tests.test_workouts import agent_ctx, ui_ctx


@pytest.fixture
def two_users(engine):
    return make_user(email="a@example.com"), make_user(email="b@example.com")


def test_routine_with_ordered_days(two_users):
    a, _ = two_users
    routine = routines.create_routine(ui_ctx(a), "Volumen")
    chest = workouts.create_workout(ui_ctx(a), "Pecho", routine_id=routine.id)
    back = workouts.create_workout(ui_ctx(a), "Espalda", routine_id=routine.id)
    legs = workouts.create_workout(ui_ctx(a), "Pierna", routine_id=routine.id)

    assert (chest.routine_name, chest.position, legs.position) == ("Volumen", 0, 2)
    detail = routines.get_routine(ui_ctx(a), routine.id)
    assert [d.name for d in detail.days] == ["Pecho", "Espalda", "Pierna"]

    routines.move_day(ui_ctx(a), legs.id, -1)
    detail = routines.move_day(ui_ctx(a), legs.id, -1)
    assert [d.name for d in detail.days] == ["Pierna", "Pecho", "Espalda"]
    routines.move_day(ui_ctx(a), legs.id, -1)  # already first: no-op
    assert [d.name for d in routines.get_routine(ui_ctx(a), routine.id).days][0] == "Pierna"

    workouts.delete_workout(ui_ctx(a), back.id)
    assert [d.name for d in routines.get_routine(ui_ctx(a), routine.id).days] == ["Pierna", "Pecho"]
    # A new day goes last.
    arms = workouts.create_workout(ui_ctx(a), "Brazo", routine_id=routine.id)
    assert routines.get_routine(ui_ctx(a), routine.id).days[-1].id == arms.id


def test_workout_without_routine_gets_its_own(two_users):
    """REST/MCP create_workout keeps working: a single-day routine is made."""
    a, _ = two_users
    w = workouts.create_workout(agent_ctx(a, write=True), "Push Day")
    assert w.routine_name == "Push Day"
    (routine,) = routines.list_routines(ui_ctx(a))
    assert [d.id for d in routine.days] == [w.id]


def test_routines_are_private(two_users):
    a, b = two_users
    routine = routines.create_routine(ui_ctx(a), "Volumen")
    assert routines.list_routines(ui_ctx(b)) == []
    with pytest.raises(NotFoundError):
        routines.get_routine(ui_ctx(b), routine.id)
    with pytest.raises(NotFoundError):
        workouts.create_workout(ui_ctx(b), "Pecho", routine_id=routine.id)
    with pytest.raises(NotFoundError):
        routines.rename_routine(ui_ctx(b), routine.id, "Mía")


def test_delete_routine_removes_days_but_keeps_history(two_users):
    a, _ = two_users
    exercise_id = make_exercise()
    routine = routines.create_routine(ui_ctx(a), "Volumen")
    chest = workouts.create_workout(ui_ctx(a), "Pecho", routine_id=routine.id)
    item = sessions.log_set(ui_ctx(a), chest.id, exercise_id, 8, 60.0, "kg", 1)

    with pytest.raises(ScopeError):
        routines.delete_routine(agent_ctx(a, write=True), routine.id)
    routines.delete_routine(ui_ctx(a), routine.id)

    assert routines.list_routines(ui_ctx(a)) == []
    with pytest.raises(NotFoundError):
        workouts.get_workout(ui_ctx(a), chest.id)
    assert sessions.get_session(ui_ctx(a), item.session_id).sets[0].reps == 8


def test_session_named_after_routine_and_day(two_users):
    a, _ = two_users
    exercise_id = make_exercise()
    routine = routines.create_routine(ui_ctx(a), "Volumen")
    chest = workouts.create_workout(ui_ctx(a), "Pecho", routine_id=routine.id)
    item = sessions.log_set(ui_ctx(a), chest.id, exercise_id, 8, None, None, 1)
    assert sessions.get_session(ui_ctx(a), item.session_id).workout_name == "Volumen · Pecho"


def test_next_day_follows_the_last_trained(two_users):
    a, _ = two_users
    routine = routines.create_routine(ui_ctx(a), "Volumen")
    days = [workouts.create_workout(ui_ctx(a), n, routine_id=routine.id) for n in ("Pecho", "Espalda")]
    assert routines.next_day_id(routines.get_routine(ui_ctx(a), routine.id)) == days[0].id

    def trained(workout_id, days_ago):
        with get_session() as s:
            at = utcnow() - timedelta(days=days_ago)
            s.add(TrainingSession(user_id=a, workout_id=workout_id, workout_name="x", started_at=at,
                                  last_activity_at=at, finished_at=at))

    trained(days[0].id, 2)
    assert routines.next_day_id(routines.get_routine(ui_ctx(a), routine.id)) == days[1].id
    trained(days[1].id, 1)  # last day trained: wraps to the first
    assert routines.next_day_id(routines.get_routine(ui_ctx(a), routine.id)) == days[0].id


def test_migration_wraps_existing_workouts_in_routines(tmp_path):
    from alembic import command
    from alembic.config import Config

    from db.upgrade import ROOT

    db_path = tmp_path / "old.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "5d1c7e2a9b40")  # the revision before routines

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, email, display_name, password_hash, role, unit_pref, time_zone, "
            "is_active, created_at) VALUES (1, 'a@x.com', 'A', 'x', 'member', 'kg', 'UTC', 1, '2026-01-01')"
        ))
        for wid, name, deleted in ((1, "Push Day", None), (2, "Legs", "2026-02-01")):
            conn.execute(
                text("INSERT INTO workouts (id, user_id, name, created_at, updated_at, deleted_at) "
                     "VALUES (:id, 1, :name, '2026-01-01', '2026-01-02', :deleted)"),
                dict(id=wid, name=name, deleted=deleted),
            )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT w.name, w.position, r.name, r.user_id, r.deleted_at FROM workouts w "
            "JOIN routines r ON r.id = w.routine_id ORDER BY w.id"
        )).all()
    assert [tuple(r) for r in rows] == [
        ("Push Day", 0, "Push Day", 1, None),
        ("Legs", 0, "Legs", 1, "2026-02-01"),
    ]
    engine.dispose()
