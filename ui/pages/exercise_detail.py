"""Exercise detail page: media, instructions, muscles, attribution, history."""

from nicegui import run, ui

import services.catalog as catalog
import services.stats as stats
from services.errors import ServiceError
from ui.auth import current_context, current_user
from ui.components.exercise_details import english_subtitle, exercise_details
from ui.i18n import error_message, exercise_name, fmt_date
from ui.layout import page_shell


@ui.page("/exercises/{exercise_id:int}", title="Ejercicio — Gym Tracker")
async def exercise_page(exercise_id: int):
    ctx = current_context()
    user = current_user()

    try:
        exercise = await run.io_bound(catalog.get_exercise, ctx, exercise_id)
    except ServiceError as exc:
        ui.notify(error_message(exc), type="negative", position="top")
        ui.navigate.to("/")
        return

    history = await run.io_bound(stats.get_exercise_history, ctx, exercise_id, limit=10)

    with page_shell(exercise_name(exercise)):
        with ui.column().classes("gap-0"):
            ui.label(exercise_name(exercise)).classes("text-2xl font-bold")
            english_subtitle(exercise)
        exercise_details(exercise)

        with ui.card().classes("w-full"):
            ui.label("Tus últimas sesiones").classes("font-semibold")
            if not history:
                ui.label("Aún no hay historial.").classes("text-sm text-gray-500")
            for entry in history:
                sets = ", ".join(
                    f"{s.weight.value:g}×{s.reps} {s.weight.unit}" if s.weight else f"PC×{s.reps}"
                    for s in entry.sets
                )
                date = fmt_date(entry.date, user.time_zone)
                ui.label(f"{date} · {entry.workout_name}: {sets}").classes("text-sm")
