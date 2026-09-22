"""Workouts home page: cards with name, exercise count, last performed date."""

from datetime import datetime
from zoneinfo import ZoneInfo

from nicegui import run, ui

import services.workouts as workouts
from services.errors import ServiceError
from ui.auth import current_context, current_user
from ui.layout import page_shell


def _fmt_date(value: datetime | None, tz_name: str) -> str:
    if value is None:
        return "never"
    local = value.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz_name))
    return local.strftime("%b %d, %Y")


@ui.page("/", title="Gym Tracker")
async def home_page():
    ctx = current_context()
    user = current_user()

    with page_shell("Workouts"):
        ui.label("Workouts").classes("text-2xl font-bold")
        ui.button(
            "New workout",
            icon="add",
            on_click=lambda: ui.navigate.to("/workouts/new"),
        ).props("unelevated").classes("w-full")

        def _confirm_delete(workout_id: int, name: str):
            with ui.dialog() as dialog, ui.card():
                ui.label(f"Delete “{name}”?").classes("text-lg font-semibold")
                ui.label("The workout plan is removed, but all logged sessions and sets are kept.").classes(
                    "text-sm text-gray-500"
                )

                async def do_delete():
                    try:
                        await run.io_bound(workouts.delete_workout, ctx, workout_id)
                    except ServiceError as exc:
                        ui.notify(str(exc), type="negative", position="top")
                        return
                    dialog.close()
                    ui.notify("Workout deleted", type="positive", position="top")
                    await render()

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")
                    ui.button("Delete", icon="delete", on_click=do_delete).props("unelevated color=red")
            dialog.open()

        async def render():
            container.clear()
            with container:
                items = await run.io_bound(workouts.list_workouts, ctx)
                if not items:
                    ui.label("No workouts yet. Create your first one!").classes("text-gray-500 mt-4")
                for w in items:
                    with ui.card().classes("w-full"):
                        ui.label(w.name).classes("text-lg font-semibold")
                        ui.label(
                            f"{w.exercise_count} exercises · last performed "
                            f"{_fmt_date(w.last_performed_at, user.time_zone)}"
                        ).classes("text-sm text-gray-500")
                        with ui.row().classes("w-full justify-end gap-2 mt-1"):
                            def train(workout_id=w.id):
                                ui.navigate.to(f"/workouts/{workout_id}")

                            def edit(workout_id=w.id):
                                ui.navigate.to(f"/workouts/{workout_id}/edit")

                            def delete(workout_id=w.id, name=w.name):
                                _confirm_delete(workout_id, name)

                            ui.button("Train", icon="play_arrow", on_click=train).props(
                                "unelevated dense color=primary"
                            ).tooltip("Start or continue this workout")
                            ui.button("Edit", icon="edit", on_click=edit).props("outline dense")
                            ui.button("Delete", icon="delete", on_click=delete).props(
                                "outline dense color=red"
                            )

        container = ui.column().classes("w-full gap-2")
        await render()
