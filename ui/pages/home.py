"""Home page: one card per routine; tapping its name unfolds its days
("Día 1 · Pecho"), each with its own Entrenar button."""

from datetime import datetime

from nicegui import run, ui

import services.routines as routines
from services.errors import ServiceError
from ui.auth import current_context, current_user
from ui.i18n import error_message, fmt_date
from ui.layout import page_shell


def _fmt_date(value: datetime | None, tz_name: str) -> str:
    if value is None:
        return "nunca"
    return fmt_date(value, tz_name)


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


@ui.page("/", title="Gym Tracker")
async def home_page():
    ctx = current_context()
    user = current_user()

    with page_shell("Rutinas"):
        ui.label("Rutinas").classes("text-2xl font-bold")
        ui.button(
            "Nueva rutina",
            icon="add",
            on_click=lambda: ui.navigate.to("/routines/new"),
        ).props("unelevated").classes("w-full")

        def _confirm_delete(routine_id: int, name: str):
            with ui.dialog() as dialog, ui.card():
                ui.label(f"¿Eliminar «{name}»?").classes("text-lg font-semibold")
                ui.label(
                    "Se eliminan la rutina y todos sus días, pero se conservan las sesiones y series registradas."
                ).classes("text-sm text-gray-500")

                async def do_delete():
                    try:
                        await run.io_bound(routines.delete_routine, ctx, routine_id)
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
                items = await run.io_bound(routines.list_routines, ctx)
                if not items:
                    ui.label("Aún no tienes rutinas. ¡Crea la primera!").classes("text-gray-500 mt-4")
                for r in items:
                    _render_routine(r, opened=len(items) == 1)

        def _render_routine(r, opened: bool):
            next_id = routines.next_day_id(r)
            with ui.card().classes("w-full p-0"):
                with ui.expansion(value=opened).classes("w-full").mark(f"routine-{r.id}") as expansion:
                    with expansion.add_slot("header"):
                        with ui.column().classes("gap-0 grow"):
                            ui.label(r.name).classes("text-lg font-semibold")
                            ui.label(
                                f"{_count(len(r.days), 'día', 'días')} · última vez: "
                                f"{_fmt_date(r.last_performed_at, user.time_zone)}"
                            ).classes("text-sm text-gray-500")

                    if not r.days:
                        ui.label("Aún no tiene días.").classes("text-gray-500")
                    for index, day in enumerate(r.days):
                        with ui.row().classes("w-full items-center justify-between no-wrap py-1"):
                            with ui.column().classes("gap-0"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(f"Día {index + 1} · {day.name}").classes("font-semibold")
                                    if day.id == next_id and len(r.days) > 1:
                                        ui.badge("Siguiente", color="positive").props("rounded")
                                ui.label(
                                    f"{_count(day.exercise_count, 'ejercicio', 'ejercicios')} · "
                                    f"{_fmt_date(day.last_performed_at, user.time_zone)}"
                                ).classes("text-xs text-gray-500")
                            ui.button(
                                "Entrenar",
                                icon="play_arrow",
                                on_click=lambda d=day.id: ui.navigate.to(f"/workouts/{d}"),
                            ).props("unelevated dense color=primary").mark(f"train-{day.id}")
                        ui.separator()

                    with ui.row().classes("w-full justify-center gap-2 mt-2"):
                        ui.button(
                            "Editar",
                            icon="edit",
                            on_click=lambda rid=r.id: ui.navigate.to(f"/routines/{rid}/edit"),
                        ).props("outline dense")
                        ui.button(
                            "Eliminar",
                            icon="delete",
                            on_click=lambda rid=r.id, name=r.name: _confirm_delete(rid, name),
                        ).props("outline dense color=red")

        container = ui.column().classes("w-full gap-2")
        await render()
