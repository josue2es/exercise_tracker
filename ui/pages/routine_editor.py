"""Routine pages: create a routine ("Volumen") and manage its days — rename,
reorder, delete, and open each day in its own editor (workout_editor.py)."""

from nicegui import run, ui

import services.routines as routines
import services.workouts as workouts
from services.errors import ServiceError
from ui.auth import current_context
from ui.i18n import error_message
from ui.layout import page_shell


@ui.page("/routines/new", title="Nueva rutina — Gym Tracker")
async def new_routine_page():
    ctx = current_context()

    with page_shell("Nueva rutina"):
        ui.label("Nueva rutina").classes("text-2xl font-bold")
        ui.label("Después agregas sus días (Día 1: Pecho, Día 2: Espalda…).").classes("text-sm text-gray-500")
        name_input = (
            ui.input("Nombre de la rutina", placeholder="p. ej. Volumen").props("outlined dense").classes("w-full")
        )

        async def create():
            try:
                routine = await run.io_bound(routines.create_routine, ctx, name_input.value or "")
            except ServiceError as exc:
                ui.notify(error_message(exc), type="negative", position="top")
                return
            # Straight to the first day: a routine without days has nothing to train.
            ui.navigate.to(f"/routines/{routine.id}/days/new")

        with ui.row().classes("w-full gap-2"):
            ui.button("Crear y agregar día 1", icon="arrow_forward", on_click=create).props("unelevated").classes(
                "grow"
            )
            ui.button("Cancelar", on_click=lambda: ui.navigate.to("/")).props("flat")


@ui.page("/routines/{routine_id:int}/edit", title="Editar rutina — Gym Tracker")
async def edit_routine_page(routine_id: int):
    ctx = current_context()
    try:
        routine = await run.io_bound(routines.get_routine, ctx, routine_id)
    except ServiceError as exc:
        ui.notify(error_message(exc), type="negative", position="top")
        ui.navigate.to("/")
        return

    with page_shell("Editar rutina"):
        with ui.row().classes("w-full items-center gap-2 no-wrap"):
            name_input = ui.input("Nombre de la rutina", value=routine.name).props("outlined dense").classes("grow")

            async def rename() -> bool:
                nonlocal routine
                try:
                    routine = await run.io_bound(routines.rename_routine, ctx, routine_id, name_input.value or "")
                except ServiceError as exc:
                    ui.notify(error_message(exc), type="negative", position="top")
                    return False
                ui.notify("Nombre guardado", type="positive", position="top")
                return True

            ui.button(icon="save", on_click=rename).props("flat round").tooltip("Guardar nombre")

        ui.label("Días").classes("text-lg font-semibold mt-2")
        days_container = ui.column().classes("w-full gap-2")

        async def move(workout_id: int, delta: int):
            await run.io_bound(routines.move_day, ctx, workout_id, delta)
            await render()

        async def render():
            current = await run.io_bound(routines.get_routine, ctx, routine_id)
            days_container.clear()
            with days_container:
                if not current.days:
                    ui.label("Aún no tiene días. Agrega el primero.").classes("text-gray-500")
                for index, day in enumerate(current.days):
                    with ui.card().classes("w-full p-3"):
                        with ui.row().classes("w-full items-center justify-between no-wrap"):
                            with ui.column().classes("gap-0"):
                                ui.label(f"Día {index + 1} · {day.name}").classes("font-semibold")
                                ui.label(f"{day.exercise_count} ejercicio{'' if day.exercise_count == 1 else 's'}").classes("text-sm text-gray-500")
                            with ui.row().classes("gap-0 no-wrap"):
                                ui.button(
                                    icon="arrow_upward", on_click=lambda d=day.id: move(d, -1)
                                ).props("flat round dense").set_enabled(index > 0)
                                ui.button(
                                    icon="arrow_downward", on_click=lambda d=day.id: move(d, 1)
                                ).props("flat round dense").set_enabled(index < len(current.days) - 1)
                                ui.button(
                                    icon="edit", on_click=lambda d=day.id: ui.navigate.to(f"/workouts/{d}/edit")
                                ).props("flat round dense").tooltip("Editar ejercicios").mark(f"edit-day-{day.id}")
                                ui.button(
                                    icon="delete", on_click=lambda d=day: confirm_delete(d.id, d.name)
                                ).props("flat round dense color=red").tooltip("Eliminar día")

        def confirm_delete(workout_id: int, name: str):
            with ui.dialog() as dialog, ui.card():
                ui.label(f"¿Eliminar el día «{name}»?").classes("text-lg font-semibold")
                ui.label("Se conservan todas las sesiones y series registradas.").classes("text-sm text-gray-500")

                async def do_delete():
                    try:
                        await run.io_bound(workouts.delete_workout, ctx, workout_id)
                    except ServiceError as exc:
                        ui.notify(error_message(exc), type="negative", position="top")
                        return
                    dialog.close()
                    ui.notify("Día eliminado", type="positive", position="top")
                    await render()

                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button("Cancelar", on_click=dialog.close).props("flat")
                    ui.button("Eliminar", icon="delete", on_click=do_delete).props("unelevated color=red")
            dialog.open()

        await render()

        ui.button(
            "Agregar día", icon="add", on_click=lambda: ui.navigate.to(f"/routines/{routine_id}/days/new")
        ).props("outline").classes("w-full")
        async def done():
            # Keep a typed-but-unsaved name instead of silently dropping it.
            if (name_input.value or "").strip() != routine.name and not await rename():
                return
            ui.navigate.to("/")

        ui.button("Listo", icon="check", on_click=done).props("unelevated").classes("w-full")
