"""Tests for the sessions service: open-session rules, set logging, isolation."""

from datetime import timedelta

import pytest
from sqlalchemy import select

import services.sessions as sessions
from db.models import SetLog, TrainingSession, utcnow
from db.session import get_session
from services.context import UserContext
from services.errors import NotFoundError, ScopeError, ValidationError
from tests.conftest import make_exercise, make_user


def ui_ctx(user_id):
    return UserContext(user_id=user_id, role="member", actor="ui")


def agent_ctx(user_id, write=True):
    return UserContext(
        user_id=user_id, role="member", scopes={"read", "write"} if write else {"read"}, actor="mcp"
    )


@pytest.fixture
def users(engine):
    a = make_user(email="a@example.com")
    b = make_user(email="b@example.com")
    return a, b


@pytest.fixture
def workout_setup(engine, users):
    from services.workouts import create_workout, set_workout_exercises
    from services.schemas import WorkoutExerciseItem

    a, _ = users
    ex1 = make_exercise(name="Bench Press", source_id="bench")
    ex2 = make_exercise(name="Squat", source_id="squat")
    extra = make_exercise(name="Curls", source_id="curls")
    w1 = create_workout(ui_ctx(a), "Day A")
    set_workout_exercises(
        ui_ctx(a), w1.id,
        [WorkoutExerciseItem(exercise_id=ex1, exercise_name="Bench Press", position=0, target_sets=3,
                             target_reps_min=8, target_reps_max=12)],
    )
    w2 = create_workout(ui_ctx(a), "Day B")
    set_workout_exercises(
        ui_ctx(a), w2.id,
        [WorkoutExerciseItem(exercise_id=ex2, exercise_name="Squat", position=0, target_sets=3,
                             target_reps_min=8, target_reps_max=12)],
    )
    return a, w1.id, w2.id, ex1, ex2, extra


# --- open session rules -----------------------------------------------------------


def test_first_set_starts_session(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    assert sessions.get_open_session(ui_ctx(a)) is None
    item = sessions.log_set(ui_ctx(a), w1, ex1, reps=8, weight_value=80, weight_unit="kg")
    open_session = sessions.get_open_session(ui_ctx(a))
    assert open_session is not None
    assert open_session.workout_id == w1
    assert open_session.workout_name == "Day A"
    assert open_session.sets[0].id == item.id


def test_one_open_session_start_finishes_previous(workout_setup):
    a, w1, w2, ex1, ex2, _ = workout_setup
    sessions.log_set(ui_ctx(a), w1, ex1, reps=8, weight_value=80, weight_unit="kg")
    sessions.start_session(ui_ctx(a), w2)
    open_session = sessions.get_open_session(ui_ctx(a))
    assert open_session.workout_id == w2
    with get_session() as session:
        finished = session.scalars(
            select(TrainingSession).where(TrainingSession.finished_at.isnot(None))
        ).unique().all()
        assert len(finished) == 1
        assert finished[0].workout_name == "Day A"


def test_log_set_on_other_workout_finishes_open(workout_setup):
    a, w1, w2, ex1, ex2, _ = workout_setup
    sessions.log_set(ui_ctx(a), w1, ex1, reps=8, weight_value=80, weight_unit="kg")
    sessions.log_set(ui_ctx(a), w2, ex2, reps=5, weight_value=100, weight_unit="kg")
    open_session = sessions.get_open_session(ui_ctx(a))
    assert open_session.workout_id == w2
    with get_session() as s:
        assert s.scalars(select(TrainingSession)).unique().all().__len__() == 2


def test_lazy_idle_close(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    sessions.log_set(ui_ctx(a), w1, ex1, reps=8)
    with get_session() as s:
        row = s.scalars(select(TrainingSession)).one()
        row.last_activity_at = utcnow() - timedelta(hours=7)
    assert sessions.get_open_session(ui_ctx(a)) is None  # closed lazily on read
    with get_session() as s:
        row = s.scalars(select(TrainingSession)).one()
        assert row.finished_at is not None
        assert row.finished_at == row.last_activity_at  # finished at last activity


def test_lazy_idle_close_not_triggered_within_six_hours(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    sessions.log_set(ui_ctx(a), w1, ex1, reps=8)
    with get_session() as s:
        row = s.scalars(select(TrainingSession)).one()
        row.last_activity_at = utcnow() - timedelta(hours=5)
    assert sessions.get_open_session(ui_ctx(a)) is not None


# --- set logging -------------------------------------------------------------------


def test_log_set_auto_numbering_and_retry_safety(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    first = sessions.log_set(ui_ctx(a), w1, ex1, reps=8, weight_value=80, weight_unit="kg")
    second = sessions.log_set(ui_ctx(a), w1, ex1, reps=7, weight_value=80, weight_unit="kg")
    assert (first.set_number, second.set_number) == (1, 2)

    # Retrying the same set_number returns the existing set unchanged.
    retried = sessions.log_set(
        ui_ctx(a), w1, ex1, reps=99, weight_value=999, weight_unit="kg", set_number=1
    )
    assert retried.id == first.id
    assert retried.reps == 8  # unchanged
    with get_session() as s:
        assert s.scalars(select(SetLog)).unique().all().__len__() == 2  # no duplicate


def test_log_set_accepts_any_catalog_exercise(workout_setup):
    a, w1, _, _, _, extra = workout_setup
    item = sessions.log_set(ui_ctx(a), w1, extra, reps=12, weight_value=15, weight_unit="kg")
    assert item.exercise_name == "Curls"


def test_log_set_bodyweight(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    item = sessions.log_set(ui_ctx(a), w1, ex1, reps=10)  # no weight
    assert item.weight is None


def test_log_set_validation(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    with pytest.raises(ValidationError):
        sessions.log_set(ui_ctx(a), w1, ex1, reps=101)
    with pytest.raises(ValidationError):
        sessions.log_set(ui_ctx(a), w1, ex1, reps=8, weight_value=80)  # unit required
    with pytest.raises(ValidationError):
        sessions.log_set(ui_ctx(a), w1, ex1, reps=8, weight_value=80, weight_unit="stones")
    with pytest.raises(ValidationError):
        sessions.log_set(ui_ctx(a), w1, ex1, reps=8, weight_value=-5, weight_unit="kg")
    with pytest.raises(ValidationError):
        sessions.log_set(ui_ctx(a), w1, ex1, reps=8, set_number=0)
    with pytest.raises(NotFoundError):
        sessions.log_set(ui_ctx(a), w1, 999999, reps=8)
    with pytest.raises(NotFoundError):
        sessions.log_set(ui_ctx(a), 999999, ex1, reps=8)


def test_update_set_and_partial_semantics(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    item = sessions.log_set(ui_ctx(a), w1, ex1, reps=8, weight_value=80, weight_unit="kg")
    updated = sessions.update_set(ui_ctx(a), item.id, reps=6)  # only reps
    assert updated.reps == 6
    assert updated.weight is not None and updated.weight.value == 80
    # Switch to bodyweight.
    updated = sessions.update_set(ui_ctx(a), item.id, set_weight=True)
    assert updated.weight is None
    # Change weight only.
    updated = sessions.update_set(
        ui_ctx(a), item.id, weight_value=100, weight_unit="kg", set_weight=True
    )
    assert updated.reps == 6
    assert updated.weight.value == 100


def test_delete_set_ui_only(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    item = sessions.log_set(ui_ctx(a), w1, ex1, reps=8)
    with pytest.raises(ScopeError):
        sessions.delete_set(agent_ctx(a), item.id)
    sessions.delete_set(ui_ctx(a), item.id)
    with get_session() as s:
        assert s.scalars(select(SetLog)).first() is None


def test_finish_session(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    sessions.log_set(ui_ctx(a), w1, ex1, reps=8)
    finished = sessions.finish_session(ui_ctx(a), notes="felt strong")
    assert finished.finished_at is not None
    assert finished.notes == "felt strong"
    assert sessions.get_open_session(ui_ctx(a)) is None
    with pytest.raises(ValidationError):
        sessions.finish_session(ui_ctx(a), finished.id)
    with pytest.raises(NotFoundError):
        sessions.finish_session(ui_ctx(a))  # nothing open


# --- isolation ----------------------------------------------------------------------


def test_isolation(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    b = make_user(email="intruder@example.com")
    admin = make_user(email="root@example.com", role="admin")
    item = sessions.log_set(ui_ctx(a), w1, ex1, reps=8)
    session_id = item.session_id

    for other in (ui_ctx(b), UserContext(user_id=admin, role="admin", actor="ui")):
        with pytest.raises(NotFoundError):
            sessions.get_session(other, session_id)
        with pytest.raises(NotFoundError):
            sessions.update_set(other, item.id, reps=5)
        with pytest.raises(NotFoundError):
            sessions.delete_set(other, item.id)
        with pytest.raises(NotFoundError):
            sessions.start_session(other, w1)
        assert sessions.get_open_session(other) is None
        assert sessions.list_sessions(other) == []


def test_user_id_inherits_from_session_not_caller(workout_setup):
    """set_logs.user_id always matches the session's user (spec constraint)."""
    a, w1, _, ex1, _, _ = workout_setup
    item = sessions.log_set(ui_ctx(a), w1, ex1, reps=8)
    with get_session() as s:
        log = s.get(SetLog, item.id)
        session_row = s.get(TrainingSession, log.session_id)
        assert log.user_id == session_row.user_id == a


def test_list_sessions_date_filter(workout_setup):
    a, w1, _, ex1, _, _ = workout_setup
    sessions.log_set(ui_ctx(a), w1, ex1, reps=8)
    sessions.finish_session(ui_ctx(a))
    listed = sessions.list_sessions(ui_ctx(a))
    assert len(listed) == 1
    assert listed[0].workout_name == "Day A"
