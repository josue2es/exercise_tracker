"""Tests for the workouts service: CRUD, ordered exercises, isolation, soft delete."""

import pytest

import services.workouts as workouts
from services.context import UserContext
from services.errors import NotFoundError, ScopeError, ValidationError
from services.schemas import WorkoutExerciseItem
from tests.conftest import make_exercise, make_user


def ui_ctx(user_id=1, actor="ui"):
    return UserContext(user_id=user_id, role="member", actor=actor)


def agent_ctx(user_id=1, write=False):
    return UserContext(
        user_id=user_id,
        role="member",
        scopes={"read", "write"} if write else {"read"},
        actor="api",
    )


@pytest.fixture
def two_users(engine):
    a = make_user(email="a@example.com")
    b = make_user(email="b@example.com")
    return a, b


@pytest.fixture
def exercise_ids(engine):
    return [make_exercise(name=f"Exercise {i}", source_id=f"ex{i}") for i in range(3)]


def _items(exercise_ids, n=3, sets=3, reps=(8, 12), comment=None):
    return [
        WorkoutExerciseItem(
            exercise_id=eid,
            exercise_name=f"Exercise {i}",
            position=i,
            target_sets=sets,
            target_reps_min=reps[0],
            target_reps_max=reps[1],
            comment=comment,
        )
        for i, eid in enumerate(exercise_ids[:n])
    ]


def test_create_and_get_workout(engine, two_users, exercise_ids):
    a, _ = two_users
    detail = workouts.create_workout(ui_ctx(a), "Push Day", "heavy")
    assert detail.name == "Push Day"
    assert detail.exercise_count == 0
    assert detail.last_performed_at is None


def test_set_workout_exercises_order_and_ranges(engine, two_users, exercise_ids):
    a, _ = two_users
    w = workouts.create_workout(ui_ctx(a), "Legs")
    detail = workouts.set_workout_exercises(ui_ctx(a), w.id, _items(exercise_ids))
    assert [e.exercise_name for e in detail.exercises] == ["Exercise 0", "Exercise 1", "Exercise 2"]
    assert detail.exercises[0].target_sets == 3
    assert (detail.exercises[0].target_reps_min, detail.exercises[0].target_reps_max) == (8, 12)

    # Replace with a shorter, reordered list.
    reordered = _items(list(reversed(exercise_ids))[:2], sets=5, reps=(12, 12))
    detail = workouts.set_workout_exercises(ui_ctx(a), w.id, reordered)
    assert [e.exercise_id for e in detail.exercises] == [exercise_ids[2], exercise_ids[1]]
    assert detail.exercises[0].target_reps_min == detail.exercises[0].target_reps_max == 12


def test_set_workout_exercises_validation(engine, two_users, exercise_ids):
    a, _ = two_users
    w = workouts.create_workout(ui_ctx(a), "X")
    with pytest.raises(ValidationError):
        workouts.set_workout_exercises(ui_ctx(a), w.id, _items(exercise_ids, sets=0))
    with pytest.raises(ValidationError):
        workouts.set_workout_exercises(ui_ctx(a), w.id, _items(exercise_ids, reps=(12, 8)))
    with pytest.raises(ValidationError):
        workouts.set_workout_exercises(ui_ctx(a), w.id, _items(exercise_ids, reps=(0, 8)))
    dupes = _items(exercise_ids[:1]) + _items(exercise_ids[:1])
    with pytest.raises(ValidationError):
        workouts.set_workout_exercises(ui_ctx(a), w.id, dupes)
    with pytest.raises(ValidationError):
        workouts.set_workout_exercises(ui_ctx(a), w.id, [WorkoutExerciseItem(
            exercise_id=999999, exercise_name="?", position=0, target_sets=3,
            target_reps_min=8, target_reps_max=12,
        )])


def test_update_workout(engine, two_users):
    a, _ = two_users
    w = workouts.create_workout(ui_ctx(a), "Old")
    detail = workouts.update_workout(ui_ctx(a), w.id, name="New", notes="note")
    assert detail.name == "New"
    assert detail.notes == "note"


# --- isolation: user B and admins get 404 for user A's data -----------------------


def test_isolation_other_user_and_admin(engine, two_users, exercise_ids):
    from db.session import get_session
    from db.models import User
    from sqlalchemy import select

    a, b = two_users
    w = workouts.create_workout(ui_ctx(a), "A's workout")
    workouts.set_workout_exercises(ui_ctx(a), w.id, _items(exercise_ids))

    with get_session() as session:
        admin_id = make_user(email="admin2@example.com", role="admin")

    for other in (ui_ctx(b), UserContext(user_id=admin_id, role="admin", actor="ui")):
        with pytest.raises(NotFoundError):
            workouts.get_workout(other, w.id)
        with pytest.raises(NotFoundError):
            workouts.update_workout(other, w.id, name="hijacked")
        with pytest.raises(NotFoundError):
            workouts.set_workout_exercises(other, w.id, [])
        with pytest.raises(NotFoundError):
            workouts.delete_workout(other, w.id)
    assert workouts.list_workouts(ui_ctx(b)) == []


def test_soft_delete_keeps_history_and_hides_from_list(engine, two_users, exercise_ids):
    from db.models import TrainingSession, Workout
    from db.session import get_session
    from sqlalchemy import select

    a, _ = two_users
    w = workouts.create_workout(ui_ctx(a), "Archive me")
    workouts.set_workout_exercises(ui_ctx(a), w.id, _items(exercise_ids))

    # Simulate a logged session against this workout.
    with get_session() as session:
        session.add(
            TrainingSession(user_id=a, workout_id=w.id, workout_name="Archive me")
        )

    workouts.delete_workout(ui_ctx(a), w.id)

    assert workouts.list_workouts(ui_ctx(a)) == []
    with pytest.raises(NotFoundError):
        workouts.get_workout(ui_ctx(a), w.id)

    # The row still exists (soft delete), the session keeps its name.
    with get_session() as session:
        row = session.scalars(select(Workout).where(Workout.id == w.id)).one()
        assert row.deleted_at is not None
        session_row = session.scalars(select(TrainingSession)).one()
        assert session_row.workout_name == "Archive me"


def test_removing_exercise_keeps_logged_sets(engine, two_users, exercise_ids):
    from db.models import SetLog
    from db.session import get_session
    from sqlalchemy import select

    a, _ = two_users
    w = workouts.create_workout(ui_ctx(a), "Mixed")
    workouts.set_workout_exercises(ui_ctx(a), w.id, _items(exercise_ids))

    with get_session() as session:
        session.add(
            SetLog(
                session_id=_make_session(a, w.id, "Mixed"),
                user_id=a,
                exercise_id=exercise_ids[0],
                set_number=1,
                reps=8,
                weight_value=80,
                weight_unit="kg",
            )
        )

    # Remove the exercise that was logged.
    workouts.set_workout_exercises(ui_ctx(a), w.id, _items(exercise_ids, n=1)[1:] or _items(exercise_ids[1:2]))
    with get_session() as session:
        assert session.scalars(select(SetLog)).first() is not None


def _make_session(user_id, workout_id, workout_name) -> int:
    from db.models import TrainingSession
    from db.session import get_session

    with get_session() as session:
        s = TrainingSession(user_id=user_id, workout_id=workout_id, workout_name=workout_name)
        session.add(s)
        session.flush()
        return s.id


def test_agents_cannot_delete_workouts(engine, two_users):
    a, _ = two_users
    w = workouts.create_workout(ui_ctx(a), "Agent target")
    with pytest.raises(ScopeError):
        workouts.delete_workout(agent_ctx(a, write=True), w.id)


def test_agent_without_write_scope_cannot_edit(engine, two_users, exercise_ids):
    a, _ = two_users
    w = workouts.create_workout(ui_ctx(a), "ReadOnly")
    with pytest.raises(ScopeError):
        workouts.update_workout(agent_ctx(a, write=False), w.id, name="Nope")
    with pytest.raises(ScopeError):
        workouts.set_workout_exercises(agent_ctx(a, write=False), w.id, _items(exercise_ids))
    with pytest.raises(ScopeError):
        workouts.create_workout(agent_ctx(a, write=False), "Nope")
    # With write scope it works.
    workouts.update_workout(agent_ctx(a, write=True), w.id, name="Yes")
    assert workouts.get_workout(ui_ctx(a), w.id).name == "Yes"
