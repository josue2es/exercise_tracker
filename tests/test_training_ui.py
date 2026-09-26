"""Headless UI tests (NiceGUI User simulation): the training screen's single
save-per-exercise flow — logging new sets, updating edited ones, validation."""

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
    calls = {"log": [], "update": []}
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
    monkeypatch.setattr(training, "current_context", lambda: None)
    monkeypatch.setattr(training, "current_user", lambda: _FakeUser())
    monkeypatch.setattr(training, "page_shell", _shell)
    return calls


def _set_numbers(user):
    """The set-row number inputs in creation order (weight, reps per row)."""
    return sorted(user.find(ui.number).elements, key=lambda e: e.id)


def _type(user, element, text):
    from nicegui.testing.user_interaction import UserInteraction

    UserInteraction(user, {element}, None).clear()
    UserInteraction(user, {element}, None).type(text)


def test_single_save_per_exercise(stub_services):
    async def root():
        await training.training_page(1)

    async def scenario():
        async with user_simulation(root) as user:
            await user.open("/")
            await user.should_see("Bench Press")

            # Saving with an empty set warns and logs nothing.
            user.find(marker="save-block-7").click()
            await user.should_see("primero indica las reps")
            assert stub_services["log"] == []

            # Fill both rows: weight 60, reps 8 and 10.
            numbers = _set_numbers(user)
            _type(user, numbers[0], "60"), _type(user, numbers[1], "8")
            _type(user, numbers[2], "60"), _type(user, numbers[3], "10")
            user.find(marker="save-block-7").click()
            await user.should_see("2 series guardadas")
            assert stub_services["log"] == [
                (7, 8, 60.0, "kg", 1),
                (7, 10, 60.0, "kg", 2),
            ]
            await user.should_see("sesión abierta")

            # Saving again without changes does nothing new.
            user.find(marker="save-block-7").click()
            await user.should_see("No hay nada que guardar")
            assert len(stub_services["log"]) == 2
            assert stub_services["update"] == []

            # Editing a saved set and saving updates just that set.
            numbers = _set_numbers(user)
            _type(user, numbers[1], "9")  # row 1 reps: 8 -> 9
            user.find(marker="save-block-7").click()
            await user.should_see("1 actualizada")
            assert len(stub_services["log"]) == 2
            assert stub_services["update"] == [(100, 9, 60.0, "kg", True)]

    asyncio.run(scenario())
