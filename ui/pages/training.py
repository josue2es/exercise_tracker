"""Training screen: log weight and reps per set, seeing the last session's
numbers. Each set saves on its own tap so nothing is lost on bad signal."""

from dataclasses import dataclass, field

from nicegui import run, ui

import services.sessions as sessions
import services.stats as stats
import services.workouts as workouts
from services.errors import ServiceError
from services.schemas import WorkoutDetail, WorkoutExerciseItem
from ui.auth import current_context, current_user
from ui.layout import page_shell

WEIGHT_STEPS = {"kg": 2.5, "lb": 5.0}


def _fmt_set(weight_value, weight_unit, reps) -> str:
    if weight_value is None:
        return f"bw×{reps}"
    return f"{weight_value:g}×{reps} {weight_unit}"


def _fmt_last(perf) -> str:
    from zoneinfo import ZoneInfo

    user = current_user()
    local = perf.session_date.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(user.time_zone))
    parts = ", ".join(_fmt_set(s.weight and s.weight.value, s.weight and s.weight.unit, s.reps) for s in perf.sets)
    return f"Last · {local.strftime('%b %d')}: {parts}"


@ui.page("/workouts/{workout_id}", title="Training — Gym Tracker")
async def training_page(workout_id: int):
    ctx = current_context()
    user = current_user()

    try:
        workout = await run.io_bound(workouts.get_workout, ctx, workout_id)
    except ServiceError as exc:
        ui.notify(str(exc), type="negative", position="top")
        ui.navigate.to("/")
        return

    open_session = await run.io_bound(sessions.get_open_session, ctx)
    if open_session and open_session.workout_id != workout_id:
        with page_shell("Training"):
            ui.notify(
                f"You have an open session for “{open_session.workout_name}”. "
                "Saving a set here will finish it.",
                type="warning",
                position="top",
            )
    if open_session is None or open_session.workout_id != workout_id:
        open_session = None  # training screen shows a fresh start

    @dataclass
    class SetRow:
        set_number: int
        weight: float | None = None
        unit: str = "kg"
        reps: int | None = None
        saved: bool = False
        set_id: int | None = None

    @dataclass
    class ExerciseBlock:
        item: WorkoutExerciseItem
        rows: list[SetRow] = field(default_factory=list)
        last_line: str = "No history yet"

    async def build_blocks() -> list[ExerciseBlock]:
        blocks = []
        for item in workout.exercises:
            logged = [s for s in (open_session.sets if open_session else []) if s.exercise_id == item.exercise_id]
            logged.sort(key=lambda s: s.set_number)
            perf = await run.io_bound(
                stats.get_last_performance, ctx, item.exercise_id,
                open_session.id if open_session else None,
            )
            last_by_number = {s.set_number: s for s in perf.sets} if perf else {}
            rows = []
            count = max(item.target_sets, len(logged))
            for n in range(1, count + 1):
                existing = next((s for s in logged if s.set_number == n), None)
                if existing:
                    rows.append(
                        SetRow(
                            set_number=n,
                            weight=existing.weight.value if existing.weight else None,
                            unit=existing.weight.unit if existing.weight else user.unit_pref,
                            reps=existing.reps,
                            saved=True,
                            set_id=existing.id,
                        )
                    )
                else:
                    # Prefill: same set number last session, else previous set.
                    source = last_by_number.get(n)
                    if source is None and rows:
                        prev = rows[-1]
                        rows.append(
                            SetRow(
                                set_number=n,
                                weight=prev.weight,
                                unit=prev.unit,
                                reps=prev.reps,
                            )
                        )
                    elif source is not None:
                        rows.append(
                            SetRow(
                                set_number=n,
                                weight=source.weight.value if source.weight else None,
                                unit=source.weight.unit if source.weight else user.unit_pref,
                                reps=source.reps,
                            )
                        )
                    else:
                        rows.append(SetRow(set_number=n, unit=user.unit_pref))
            blocks.append(
                ExerciseBlock(
                    item=item,
                    rows=rows,
                    last_line=_fmt_last(perf) if perf else "No history yet",
                )
            )
        return blocks

    blocks = await build_blocks()

    with page_shell("Training"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(workout.name).classes("text-2xl font-bold")
            state_label = ui.label(
                "open session" if open_session else "not started"
            ).classes("text-xs text-gray-500 self-end")

        async def do_finish():
            try:
                if open_session is not None:
                    await run.io_bound(sessions.finish_session, ctx, open_session.id)
            except ServiceError as exc:
                ui.notify(str(exc), type="negative", position="top")
                return
            ui.notify("Workout finished. Great job!", type="positive", position="top")
            ui.navigate.to("/")

        ui.button("Finish workout", icon="flag", on_click=do_finish).props("unelevated color=positive").classes(
            "w-full"
        )

        container = ui.column().classes("w-full gap-4")

        def _render_blocks():
            container.clear()
            with container:
                for block in blocks:
                    _render_exercise(block)

        def _render_exercise(block: ExerciseBlock):
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(block.item.exercise_name).classes("font-semibold")
                    ui.button(
                        "How to",
                        icon="help_outline",
                        on_click=lambda exercise_id=block.item.exercise_id: ui.navigate.to(
                            f"/exercises/{exercise_id}"
                        ),
                    ).props("flat dense")

                target = (
                    f"{block.item.target_sets} × {block.item.target_reps_min}"
                    + (
                        f"–{block.item.target_reps_max}"
                        if block.item.target_reps_max != block.item.target_reps_min
                        else ""
                    )
                )
                ui.label(f"Target: {target}").classes("text-sm text-gray-500")
                if block.item.comment:
                    ui.label(block.item.comment).classes("text-sm text-gray-500 italic")
                ui.label(block.last_line).classes("text-sm text-blue-600")

                for row in block.rows:
                    _render_set_row(block, row)

                def add_row(block=block):
                    n = (block.rows[-1].set_number + 1) if block.rows else 1
                    prev = block.rows[-1] if block.rows else None
                    block.rows.append(
                        SetRow(
                            set_number=n,
                            weight=prev.weight if prev else None,
                            unit=prev.unit if prev else user.unit_pref,
                            reps=prev.reps if prev else None,
                        )
                    )
                    _render_blocks()

                ui.button("Add set", icon="add", on_click=add_row).props("outline dense").classes("w-full")

        def _render_set_row(block: ExerciseBlock, row: SetRow):
            with ui.row().classes("w-full items-center gap-1"):
                ui.badge(f"{row.set_number}").props("dense")
                weight_input = (
                    ui.number(
                        "Weight",
                        value=row.weight,
                        step=WEIGHT_STEPS.get(row.unit, 2.5),
                        min=0,
                    )
                    .props("outlined dense inputmode=decimal standout no-label")
                    .style("max-width: 6.5rem")
                    .tooltip("Weight (empty = bodyweight)")
                )
                with ui.column().classes("gap-0"):
                    step = WEIGHT_STEPS.get(row.unit, 2.5)
                    ui.button(icon="add", on_click=lambda: _bump(weight_input, step)).props(
                        "flat dense size=xs"
                    )
                    ui.button(
                        icon="remove", on_click=lambda: _bump(weight_input, -step)
                    ).props("flat dense size=xs")
                reps_input = (
                    ui.number("Reps", value=row.reps, step=1, min=0, max=100)
                    .props("outlined dense inputmode=numeric no-label")
                    .style("max-width: 4.5rem")
                    .tooltip("Reps")
                )
                with ui.column().classes("gap-0"):
                    ui.button(icon="add", on_click=lambda: _bump(reps_input, 1)).props("flat dense size=xs")
                    ui.button(icon="remove", on_click=lambda: _bump(reps_input, -1)).props("flat dense size=xs")

                if row.saved:

                    async def do_edit(row=row):
                        try:
                            await run.io_bound(
                                sessions.update_set,
                                ctx,
                                row.set_id,
                                int(reps_input.value or 0),
                                weight_input.value,
                                row.unit if weight_input.value is not None else None,
                                True,  # set_weight
                            )
                        except ServiceError as exc:
                            ui.notify(str(exc), type="negative", position="top")
                            return
                        ui.notify("Set updated", type="positive", position="top")

                    async def do_delete(row=row):
                        try:
                            await run.io_bound(sessions.delete_set, ctx, row.set_id)
                        except ServiceError as exc:
                            ui.notify(str(exc), type="negative", position="top")
                            return
                        for b in blocks:
                            b.rows = [r for r in b.rows if r.set_id != row.set_id]
                        _render_blocks()

                    ui.button(icon="check_circle", on_click=do_edit).props(
                        "flat round dense color=green"
                    ).tooltip("Save changes")
                    ui.button(icon="delete", on_click=do_delete).props(
                        "flat round dense color=red"
                    ).tooltip("Delete set")
                else:

                    async def do_save(block=block, row=row):
                        if reps_input.value is None:
                            ui.notify("Enter reps first", type="warning", position="top")
                            return
                        weight_value = weight_input.value
                        try:
                            item = await run.io_bound(
                                sessions.log_set,
                                ctx,
                                workout.id,
                                block.item.exercise_id,
                                int(reps_input.value),
                                weight_value,
                                row.unit if weight_value is not None else None,
                                row.set_number,
                            )
                        except ServiceError as exc:
                            ui.notify(str(exc), type="negative", position="top")
                            return
                        row.saved = True
                        row.set_id = item.id
                        row.weight = weight_value
                        row.reps = int(reps_input.value)
                        state_label.text = "open session"
                        ui.notify(f"Set {row.set_number} saved", type="positive", position="top")
                        _render_blocks()

                    ui.button("Save", icon="save", on_click=do_save).props(
                        "unelevated dense color=primary"
                    ).tooltip("Save this set")

        def _bump(number_input, delta: float):
            current = number_input.value or 0
            new_value = round(current + delta, 2)
            number_input.set_value(max(0.0, new_value))

        _render_blocks()
