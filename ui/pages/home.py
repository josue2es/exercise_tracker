"""Workouts home page: cards with name, exercise count, last performed date."""

from datetime import datetime

from nicegui import run, ui

import services.workouts as workouts
from services.errors import ServiceError
from ui.auth import current_context, current_user
from ui.i18n import error_message, fmt_date
from ui.layout import page_shell


def _fmt_date(value: datetime | None, tz_name: str) -> str:
    if value is None:
        return "nunca"
    return fmt_date(value, tz_name)


@ui.page("/", title="Gym Tracker")
async def home_page():
    ctx = current_context()
    user = current_user()

    with page_shell("Rutinas"):
        ui.label("Rutinas").classes("text-2xl font-bold")
        ui.button(
            "Nueva rutina",
            icon="add",
            on_click=lambda: ui.navigate.to("/workouts/new"),
        ).props("unelevated").classes("w-full")

        def _confirm_delete(workout_id: int, name: str):
            with ui.dialog() as dialog, ui.card():
                ui.label(f"¿Eliminar «{name}»?").classes("text-lg font-semibold")
                ui.label("Se elimina la rutina, pero se conservan todas las sesiones y series registradas.").classes(
                    "text-sm text-gray-500"
                )

                async def do_delete():
                    try:
                        await run.io_bound(workouts.delete_workout, ctx, workout_id)
                    except ServiceError as exc:
                        ui.notify(error_message(exc), type="negative", position="top")
                        return
                    dialog.close()
                    ui.notify("Rutina eliminada", type="positive", position="top")
                    await render()

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancelar", on_click=dialog.close).props("flat")
                    ui.button("Eliminar", icon="delete", on_click=do_delete).props("unelevated color=red")
            dialog.open()

        async def render():
            container.clear()
            with container:
                items = await run.io_bound(workouts.list_workouts, ctx)
                if not items:
                    ui.label("Aún no tienes rutinas. ¡Crea la primera!").classes("text-gray-500 mt-4")
                for w in items:
                    with ui.card().classes("w-full"):
                        ui.label(w.name).classes("text-lg font-semibold")
                        ui.label(
                            f"{w.exercise_count} ejercicios · última vez: "
                            f"{_fmt_date(w.last_performed_at, user.time_zone)}"
                        ).classes("text-sm text-gray-500")
                        with ui.row().classes("w-full justify-center gap-2 mt-1"):
                            def train(workout_id=w.id):
                                ui.navigate.to(f"/workouts/{workout_id}")

                            def edit(workout_id=w.id):
                                ui.navigate.to(f"/workouts/{workout_id}/edit")

                            def delete(workout_id=w.id, name=w.name):
                                _confirm_delete(workout_id, name)

                            ui.button("Entrenar", icon="play_arrow", on_click=train).props(
                                "unelevated dense color=primary"
                            ).tooltip("Empezar o continuar esta rutina")
                            ui.button("Editar", icon="edit", on_click=edit).props("outline dense")
                            ui.button("Eliminar", icon="delete", on_click=delete).props(
                                "outline dense color=red"
                            )

        container = ui.column().classes("w-full gap-2")
        await render()
