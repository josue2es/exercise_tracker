"""Tests for the stats service: last performance, history, training summary."""

from datetime import timedelta

import pytest

import services.sessions as sessions
import services.stats as stats
from sqlalchemy import select

from db.models import TrainingSession, utcnow
from db.session import get_session
from tests.conftest import make_exercise, make_user
from tests.test_sessions import ui_ctx


@pytest.fixture
def setup(engine):
    from services.workouts import create_workout, set_workout_exercises
    from services.schemas import WorkoutExerciseItem

    user = make_user(email="stat@example.com")
    bench = make_exercise(name="Bench Press", source_id="bench")
    curl = make_exercise(name="Curls", source_id="curls2")
    w1 = create_workout(ui_ctx(user), "Push A")
    w2 = create_workout(ui_ctx(user), "Push B")
    set_workout_exercises(ui_ctx(user), w1.id, [
        WorkoutExerciseItem(exercise_id=bench, exercise_name="Bench Press", position=0,
                            target_sets=3, target_reps_min=8, target_reps_max=12),
    ])
    set_workout_exercises(ui_ctx(user), w2.id, [
        WorkoutExerciseItem(exercise_id=bench, exercise_name="Bench Press", position=0,
                            target_sets=3, target_reps_min=8, target_reps_max=12),
    ])
    return user, w1.id, w2.id, bench, curl


def _backdate_sessions(hours: float) -> None:
    """Make existing sessions appear older so newer ones dominate ordering."""
    with get_session() as s:
        for row in s.scalars(select(TrainingSession)).unique():
            row.started_at = utcnow() - timedelta(hours=hours)


def _finish_open(user_id: int) -> None:
    from sqlalchemy import update

    with get_session() as s:
        s.execute(
            update(TrainingSession)
            .where(TrainingSession.user_id == user_id, TrainingSession.finished_at.is_(None))
            .values(finished_at=utcnow())
        )


def test_last_performance_most_recent_excluding_current(setup):
    user, w1, w2, bench, _ = setup
    # Older session.
    sessions.log_set(ui_ctx(user), w1, bench, reps=8, weight_value=70, weight_unit="kg")
    sessions.finish_session(ui_ctx(user))
    _backdate_sessions(48)

    # Recent session.
    sessions.log_set(ui_ctx(user), w2, bench, reps=8, weight_value=80, weight_unit="kg")
    sessions.log_set(ui_ctx(user), w2, bench, reps=7, weight_value=80, weight_unit="kg")
    sessions.finish_session(ui_ctx(user))

    # Current (open) session with a set.
    sessions.start_session(ui_ctx(user), w1)
    current = sessions.get_open_session(ui_ctx(user))
    sessions.log_set(ui_ctx(user), w1, bench, reps=6, weight_value=90, weight_unit="kg")

    perf = stats.get_last_performance(ui_ctx(user), bench, exclude_session_id=current.id)
    assert perf is not None
    assert [s.reps for s in perf.sets] == [8, 7]
    assert all(s.weight.value == 80 for s in perf.sets)


def test_last_performance_no_history(setup):
    user, _, _, bench, _ = setup
    assert stats.get_last_performance(ui_ctx(user), bench) is None


def test_last_performance_looks_across_workouts(setup):
    user, w1, w2, bench, _ = setup
    sessions.log_set(ui_ctx(user), w2, bench, reps=5, weight_value=60, weight_unit="lb")
    sessions.finish_session(ui_ctx(user))
    perf = stats.get_last_performance(ui_ctx(user), bench)
    assert perf.sets[0].weight.value == 60


def test_exercise_history(setup):
    user, w1, w2, bench, _ = setup
    sessions.log_set(ui_ctx(user), w1, bench, reps=8, weight_value=70, weight_unit="kg")
    sessions.finish_session(ui_ctx(user))
    _backdate_sessions(24)
    sessions.log_set(ui_ctx(user), w2, bench, reps=8, weight_value=80, weight_unit="kg")
    sessions.finish_session(ui_ctx(user))

    history = stats.get_exercise_history(ui_ctx(user), bench)
    assert [h.workout_name for h in history] == ["Push B", "Push A"]
    assert history[0].sets[0].weight.value == 80


def test_training_summary(setup):
    user, w1, _, bench, curl = setup
    # Session with kg bench sets + lb curls + bodyweight move.
    sessions.log_set(ui_ctx(user), w1, bench, reps=8, weight_value=100, weight_unit="kg")  # 800 kg
    sessions.log_set(ui_ctx(user), w1, bench, reps=6, weight_value=100, weight_unit="kg")  # 600 kg
    sessions.log_set(ui_ctx(user), w1, curl, reps=12, weight_value=30, weight_unit="lb")  # 13.6 kg x12
    sessions.log_set(ui_ctx(user), w1, curl, reps=10)  # bodyweight: sets but no volume
    sessions.finish_session(ui_ctx(user))

    summary = stats.get_training_summary(
        ui_ctx(user), utcnow() - timedelta(days=7), utcnow() + timedelta(days=1)
    )
    assert summary.session_count == 1
    assert summary.total_sets == 4
    expected_curl_kg = 30 * 0.45359237 * 12
    by_muscle = {v.muscle: v.volume_kg for v in summary.volume_per_muscle}
    assert by_muscle["pectorals"] == pytest.approx(1400 + expected_curl_kg, abs=0.5)

    # Best set: heaviest first (kg bench), then most reps.
    assert len(summary.best_sets) == 2
    best = {b.exercise_name: b for b in summary.best_sets}
    assert best["Bench Press"].reps == 8
    assert best["Bench Press"].weight.value == 100
    # Bodyweight sets never qualify as best sets (weight is None).
    assert best["Curls"].weight.value == 30
    assert best["Curls"].weight.unit == "lb"


def test_best_set_prefers_heavier_then_more_reps(setup):
    user, w1, _, bench, _ = setup
    sessions.log_set(ui_ctx(user), w1, bench, reps=12, weight_value=90, weight_unit="kg")
    sessions.log_set(ui_ctx(user), w1, bench, reps=5, weight_value=100, weight_unit="kg")  # heavier
    sessions.log_set(ui_ctx(user), w1, bench, reps=6, weight_value=100, weight_unit="kg")  # heavier + more
    sessions.finish_session(ui_ctx(user))

    summary = stats.get_training_summary(
        ui_ctx(user), utcnow() - timedelta(days=1), utcnow() + timedelta(days=1)
    )
    assert summary.best_sets[0].reps == 6


def test_summary_unit_conversion(setup):
    user, w1, _, bench, _ = setup
    sessions.log_set(ui_ctx(user), w1, bench, reps=10, weight_value=100, weight_unit="lb")
    sessions.finish_session(ui_ctx(user))
    summary = stats.get_training_summary(
        ui_ctx(user), utcnow() - timedelta(days=1), utcnow() + timedelta(days=1)
    )
    assert summary.volume_per_muscle[0].volume_kg == pytest.approx(100 * 0.45359237 * 10, abs=0.5)
