"""Headless UI tests (NiceGUI User simulation): the training screen's compact
line per exercise, the "Detallar" per-set view, and the single save at the end
of the routine — logging new sets, updating/deleting edited ones, validation."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation

import ui.pages.training as training
from services.schemas import WorkoutDetail, WorkoutExerciseItem

WORKOUT = WorkoutDetail(
    id=1,
    name="Push Day",
    created_at=datetime.now(timezone.utc),
    updated_at=datetime.now(timezone.utc),
    exercises=[
        WorkoutExerciseItem(
            exercise_id=7,
            exercise_name="Bench Press",
            position=0,
            target_sets=2,
            target_reps_min=8,
            target_reps_max=12,
        )
    ],
)


@dataclass
class _SavedSet:
    id: int
    session_id: int


@contextmanager
def _shell(title):
    with ui.column():
        yield


class _FakeUser:
    unit_pref = "kg"
    time_zone = "UTC"


@pytest.fixture
def stub_services(monkeypatch):
    calls = {"log": [], "update": [], "delete": [], "finish": []}
    ids = iter(range(100, 200))

    def fake_log(ctx, workout_id, exercise_id, reps, weight, unit, set_number):
        calls["log"].append((exercise_id, reps, weight, unit, set_number))
        return _SavedSet(id=next(ids), session_id=55)

    def fake_update(ctx, set_id, reps, weight, unit, set_weight):
        calls["update"].append((set_id, reps, weight, unit, set_weight))

    monkeypatch.setattr(training.workouts, "get_workout", lambda ctx, wid: WORKOUT)
    monkeypatch.setattr(training.sessions, "get_open_session", lambda ctx: None)
    monkeypatch.setattr(training.stats, "get_last_performance", lambda ctx, eid, sid: None)
    monkeypatch.setattr(training.sessions, "log_set", fake_log)
    monkeypatch.setattr(training.sessions, "update_set", fake_update)
    monkeypatch.setattr(training.sessions, "delete_set", lambda ctx, set_id: calls["delete"].append(set_id))
    monkeypatch.setattr(
        training.sessions, "finish_session", lambda ctx, session_id: calls["finish"].append(session_id)
    )
    monkeypatch.setattr(training, "current_context", lambda: None)
    monkeypatch.setattr(training, "current_user", lambda: _FakeUser())
    monkeypatch.setattr(training, "page_shell", _shell)
    return calls


def _numbers(user):
    """The number inputs in creation order: (weight, reps, sets) on the compact
    line, (weight, reps) per set in the detailed view."""
    return sorted(user.find(ui.number).elements, key=lambda e: e.id)


def _type(user, element, text):
    from nicegui.testing.user_interaction import UserInteraction

    UserInteraction(user, {element}, None).clear()
    UserInteraction(user, {element}, None).type(text)


async def _root():
    await training.training_page(1)


def test_compact_line_saves_all_sets(stub_services):
    async def scenario():
        async with user_simulation(_root) as user:
            await user.open("/")
            await user.should_see("Bench Press")

            # Saving with empty reps warns and logs nothing.
            user.find(marker="save-all").click()
            await user.should_see("Completa al menos un ejercicio")
            assert stub_services["log"] == []

            # One line for all sets: 60 kg × 8, series prefilled with the target (2).
            weight, reps, num_sets = _numbers(user)
            assert num_sets.value == 2
            _type(user, weight, "60"), _type(user, reps, "8")
            user.find(marker="save-all").click()
            await user.should_see("2 series guardadas")
            assert stub_services["log"] == [(7, 8, 60.0, "kg", 1), (7, 8, 60.0, "kg", 2)]
            await user.should_see("sesión abierta")

            # Saving again without changes does nothing new.
            user.find(marker="save-all").click()
            await user.should_see("No hay nada que guardar")
            assert len(stub_services["log"]) == 2 and stub_services["update"] == []

            # Editing the line updates every set; fewer series deletes the extra one.
            weight, reps, num_sets = _numbers(user)
            _type(user, reps, "9"), _type(user, num_sets, "1")
            user.find(marker="save-all").click()
            await user.should_see("1 actualizada, 1 eliminada")
            assert stub_services["delete"] == [101]
            assert stub_services["update"] == [(100, 9, 60.0, "kg", True)]

    asyncio.run(scenario())


def test_detail_view_per_set(stub_services):
    async def scenario():
        async with user_simulation(_root) as user:
            await user.open("/")
            weight, reps, _ = _numbers(user)
            _type(user, weight, "50"), _type(user, reps, "10")

            # Detallar expands the line into one row per set, prefilled from it.
            user.find(marker="detail-7").click()
            numbers = _numbers(user)
            assert [n.value for n in numbers] == [50, 10, 50, 10]
            _type(user, numbers[2], "55"), _type(user, numbers[3], "8")

            # Terminar rutina saves pending sets, then closes the session.
            user.find(marker="finish").click()
            await user.should_see("Rutina terminada")
            assert stub_services["log"] == [(7, 10, 50.0, "kg", 1), (7, 8, 55.0, "kg", 2)]
            assert stub_services["finish"] == [55]

    asyncio.run(scenario())


def test_saves_complete_exercises_and_skips_the_rest(stub_services, monkeypatch):
    """Two exercises, only one filled in: Terminar saves that one, warns about
    the other, and closes the session. The day's notes show under its name."""
    two = WORKOUT.model_copy(
        update={
            "notes": "Descanso 90 s entre series",
            "exercises": WORKOUT.exercises
            + [
                WorkoutExerciseItem(
                    exercise_id=8,
                    exercise_name="Dips",
                    position=1,
                    target_sets=1,
                    target_reps_min=10,
                    target_reps_max=10,
                    rest_seconds=90,
                )
            ],
        }
    )
    monkeypatch.setattr(training.workouts, "get_workout", lambda ctx, wid: two)

    async def scenario():
        async with user_simulation(_root) as user:
            await user.open("/")
            await user.should_see("Descanso 90 s entre series")
            # Rest is shown, not editable: still 3 inputs (weight, reps, sets) per exercise.
            await user.should_see("Descanso: 90 s (1:30)")
            assert len(_numbers(user)) == 6

            weight, reps, _sets, *_dips = _numbers(user)
            _type(user, weight, "40"), _type(user, reps, "12")
            user.find(marker="finish").click()
            await user.should_see("Sin guardar (faltan reps): Dips")
            await user.should_see("Rutina terminada")
            assert stub_services["log"] == [(7, 12, 40.0, "kg", 1), (7, 12, 40.0, "kg", 2)]
            assert stub_services["finish"] == [55]

    asyncio.run(scenario())
