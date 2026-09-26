"""Exercise detail page: media, instructions, muscles, attribution, history."""

from zoneinfo import ZoneInfo

from nicegui import run, ui

import services.catalog as catalog
import services.stats as stats
from services.errors import ServiceError
from ui.auth import current_context, current_user
from ui.components.exercise_details import exercise_details
from ui.layout import page_shell


def _local(date, tz_name: str):
    return date.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name))


@ui.page("/exercises/{exercise_id:int}", title="Exercise — Gym Tracker")
async def exercise_page(exercise_id: int):
    ctx = current_context()
    user = current_user()

    try:
        exercise = await run.io_bound(catalog.get_exercise, ctx, exercise_id)
    except ServiceError as exc:
        ui.notify(str(exc), type="negative", position="top")
        ui.navigate.to("/")
        return

    history = await run.io_bound(stats.get_exercise_history, ctx, exercise_id, limit=10)

    with page_shell(exercise.name):
        ui.label(exercise.name).classes("text-2xl font-bold")
        exercise_details(exercise)

        with ui.card().classes("w-full"):
            ui.label("Your last sessions").classes("font-semibold")
            if not history:
                ui.label("No history yet.").classes("text-sm text-gray-500")
            for entry in history:
                sets = ", ".join(
                    f"{s.weight.value:g}×{s.reps} {s.weight.unit}" if s.weight else f"bw×{s.reps}"
                    for s in entry.sets
                )
                date = _local(entry.date, user.time_zone).strftime("%b %d, %Y")
                ui.label(f"{date} · {entry.workout_name}: {sets}").classes("text-sm")
