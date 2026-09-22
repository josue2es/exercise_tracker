"""Exercise detail page: media, instructions, muscles, attribution, history."""

from zoneinfo import ZoneInfo

from nicegui import run, ui

import services.catalog as catalog
import services.stats as stats
from services.errors import ServiceError
from ui.auth import current_context, current_user
from ui.layout import page_shell


def _local(date, tz_name: str):
    return date.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name))


@ui.page("/exercises/{exercise_id}", title="Exercise — Gym Tracker")
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
        if exercise.retired_at:
            ui.badge("retired from catalog", color="grey").classes("mb-1")

        # Media: local images for free-exercise-db, GIF for ExerciseDB.
        images = [m.url for m in exercise.media]
        if images:
            with ui.row().classes("w-full justify-center gap-2 wrap"):
                for url in images:
                    if url.endswith(".gif"):
                        ui.html(
                            f'<img src="{url}" style="max-width:100%;max-height:280px;'
                            f'border-radius:12px" alt="{exercise.name}">'
                        )
                    else:
                        ui.html(
                            f'<img src="{url}" loading="lazy" style="width:150px;height:150px;'
                            f'object-fit:cover;border-radius:12px;background:#e2e8f0" alt="">'
                        )
        else:
            ui.icon("fitness_center").classes("text-6xl text-gray-400")

        with ui.card().classes("w-full"):
            ui.label("Muscles").classes("font-semibold")
            ui.label(", ".join(exercise.primary_muscles) or "—").classes("text-sm")
            if exercise.secondary_muscles:
                ui.label(f"Secondary: {', '.join(exercise.secondary_muscles)}").classes("text-xs text-gray-500")
            if exercise.body_parts:
                ui.label(f"Body parts: {', '.join(exercise.body_parts)}").classes("text-xs text-gray-500")
            if exercise.equipment:
                ui.label(f"Equipment: {', '.join(exercise.equipment)}").classes("text-xs text-gray-500")
            if exercise.level:
                ui.label(f"Level: {exercise.level}").classes("text-xs text-gray-500")

        if exercise.instructions:
            with ui.card().classes("w-full"):
                ui.label("How to").classes("font-semibold")
                for i, step in enumerate(exercise.instructions, start=1):
                    ui.label(f"{i}. {step}").classes("text-sm")

        if exercise.attribution:
            ui.label(exercise.attribution).classes("text-xs text-gray-400")

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
