"""Workout editor: name, notes, ordered exercise rows (sets, rep range, comment)."""

from dataclasses import dataclass, field

from nicegui import run, ui

import services.workouts as workouts
from services.errors import ServiceError, ValidationError
from services.schemas import ExerciseSummary, WorkoutExerciseItem
from ui.auth import current_context
from ui.components.exercise_details import open_exercise_dialog
from ui.components.picker import exercise_picker
from ui.i18n import error_message
from ui.layout import page_shell

DEFAULT_SETS = 3
DEFAULT_REPS = (8, 12)


@dataclass
class EditorRow:
    exercise_id: int
    exercise_name: str
    target_sets: int = DEFAULT_SETS
    target_reps_min: int = DEFAULT_REPS[0]
    target_reps_max: int = DEFAULT_REPS[1]
    comment: str = ""


@dataclass
class EditorState:
    workout_id: int | None = None
    name: str = ""
    notes: str = ""
    rows: list[EditorRow] = field(default_factory=list)


def _load_state(ctx, workout_id: int | None) -> EditorState:
    if workout_id is None:
        return EditorState()
    detail = workouts.get_workout(ctx, workout_id)
    return EditorState(
        workout_id=detail.id,
        name=detail.name,
        notes=detail.notes or "",
        rows=[
            EditorRow(
                exercise_id=e.exercise_id,
                exercise_name=e.exercise_name,
                target_sets=e.target_sets,
                target_reps_min=e.target_reps_min,
                target_reps_max=e.target_reps_max,
                comment=e.comment or "",
            )
            for e in detail.exercises
        ],
    )


@ui.page("/workouts/new", title="Nueva rutina — Gym Tracker")
async def new_workout_page():
    await _editor_page(workout_id=None)


@ui.page("/workouts/{workout_id:int}/edit", title="Editar rutina — Gym Tracker")
async def edit_workout_page(workout_id: int):
    await _editor_page(workout_id=workout_id)


async def _editor_page(workout_id: int | None):
    ctx = current_context()
    try:
        state = await run.io_bound(_load_state, ctx, workout_id)
    except ServiceError as exc:
        ui.notify(error_message(exc), type="negative", position="top")
        ui.navigate.to("/")
        return

    with page_shell("Editor de rutina"):
        name_input = ui.input("Nombre de la rutina", value=state.name).props("outlined dense").classes("w-full")
        notes_input = (
            ui.textarea("Notas", value=state.notes).props("outlined dense autogrow").classes("w-full")
        )

        rows_container = ui.column().classes("w-full gap-2")

        def render_rows():
            rows_container.clear()
            with rows_container:
                for index, row in enumerate(state.rows):
                    with ui.card().classes("w-full p-3"):
                        with ui.row().classes("items-center justify-between w-full"):
                            with ui.row().classes("items-center gap-1 no-wrap"):
                                ui.label(f"{index + 1}.").classes("font-semibold text-sm")
                                # A dialog, not navigation: leaving the page would lose unsaved edits.
                                ui.label(row.exercise_name).classes(
                                    "font-semibold text-sm text-primary cursor-pointer"
                                ).on(
                                    "click", lambda eid=row.exercise_id: open_exercise_dialog(ctx, eid)
                                ).mark(f"row-name-{row.exercise_id}")
                            with ui.row().classes("gap-0"):
                                def move_up(i=index):
                                    if i > 0:
                                        state.rows[i - 1], state.rows[i] = state.rows[i], state.rows[i - 1]
                                        render_rows()

                                def move_down(i=index):
                                    if i < len(state.rows) - 1:
                                        state.rows[i + 1], state.rows[i] = state.rows[i], state.rows[i + 1]
                                        render_rows()

                                def remove(i=index):
                                    state.rows.pop(i)
                                    render_rows()

                                ui.button(icon="arrow_upward", on_click=move_up).props("flat round dense")
                                ui.button(icon="arrow_downward", on_click=move_down).props("flat round dense")
                                ui.button(icon="close", on_click=remove).props("flat round dense color=red")

                        with ui.row().classes("items-end gap-2 w-full wrap"):
                            ui.number("Series", value=row.target_sets, min=1, max=20, step=1).bind_value(
                                row, "target_sets"
                            ).props("outlined dense inputmode=numeric").style("max-width: 5rem")
                            ui.number("Reps mín.", value=row.target_reps_min, min=1, max=100, step=1).bind_value(
                                row, "target_reps_min"
                            ).props("outlined dense inputmode=numeric").style("max-width: 5.5rem")
                            ui.number("Reps máx.", value=row.target_reps_max, min=1, max=100, step=1).bind_value(
                                row, "target_reps_max"
                            ).props("outlined dense inputmode=numeric").style("max-width: 5.5rem")
                        ui.textarea("Comentario", value=row.comment).bind_value(row, "comment").props(
                            "outlined dense autogrow"
                        ).classes("w-full")

        async def open_picker():
            def on_pick(exercise: ExerciseSummary):
                if any(r.exercise_id == exercise.id for r in state.rows):
                    ui.notify("Ya está en esta rutina", type="warning", position="top")
                    return
                state.rows.append(EditorRow(exercise_id=exercise.id, exercise_name=exercise.name))
                render_rows()

            def on_remove(exercise: ExerciseSummary):
                state.rows[:] = [r for r in state.rows if r.exercise_id != exercise.id]
                render_rows()

            await exercise_picker(
                ctx,
                on_pick,
                selected_ids=lambda: {r.exercise_id for r in state.rows},
                on_remove=on_remove,
                close_label="Listo",
            )

        async def save():
            name = name_input.value.strip()
            notes = notes_input.value.strip()
            if not name:
                ui.notify("Escribe un nombre para la rutina", type="negative", position="top")
                return
            items = [
                WorkoutExerciseItem(
                    exercise_id=r.exercise_id,
                    exercise_name=r.exercise_name,
                    position=i,
                    target_sets=int(r.target_sets or DEFAULT_SETS),
                    target_reps_min=int(r.target_reps_min or DEFAULT_REPS[0]),
                    target_reps_max=int(r.target_reps_max or DEFAULT_REPS[1]),
                    comment=r.comment or None,
                )
                for i, r in enumerate(state.rows)
            ]
            try:
                if state.workout_id is None:
                    detail = await run.io_bound(workouts.create_workout, ctx, name, notes or None)
                    state.workout_id = detail.id
                else:
                    # Pass "" (not None) so clearing the notes field clears them.
                    await run.io_bound(workouts.update_workout, ctx, state.workout_id, name, notes)
                await run.io_bound(workouts.set_workout_exercises, ctx, state.workout_id, items)
            except (ServiceError, ValidationError) as exc:
                ui.notify(error_message(exc), type="negative", position="top")
                return
            ui.notify("Rutina guardada", type="positive", position="top")
            ui.navigate.to("/")

        render_rows()

        ui.button("Agregar ejercicio", icon="add", on_click=open_picker).props("outline").classes("w-full")
        with ui.row().classes("w-full gap-2"):
            ui.button("Guardar", icon="save", on_click=save).props("unelevated").classes("grow")
            ui.button("Cancelar", on_click=lambda: ui.navigate.to("/")).props("flat")
