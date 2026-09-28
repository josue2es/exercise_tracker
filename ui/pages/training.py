"""Training screen: log weight and reps for each exercise, seeing the last
session's numbers.

Each exercise shows one compact line (weight, reps, number of sets) that
applies to all its sets; "Detallar" expands it to one line per set. Nothing is
persisted until the single Guardar / Terminar rutina at the end of the page."""

from dataclasses import dataclass, field

from nicegui import run, ui

import services.sessions as sessions
import services.stats as stats
import services.workouts as workouts
from services.errors import ServiceError
from services.schemas import WorkoutExerciseItem
from ui.auth import current_context, current_user
from ui.i18n import error_message, fmt_date
from ui.layout import page_shell

WEIGHT_STEPS = {"kg": 2.5, "lb": 5.0}


def _fmt_set(weight_value, weight_unit, reps) -> str:
    if weight_value is None:
        return f"PC×{reps}"
    return f"{weight_value:g}×{reps} {weight_unit}"


def _fmt_last(perf) -> str:
    user = current_user()
    parts = ", ".join(_fmt_set(s.weight and s.weight.value, s.weight and s.weight.unit, s.reps) for s in perf.sets)
    return f"Última · {fmt_date(perf.session_date, user.time_zone, year=False)}: {parts}"


@ui.page("/workouts/{workout_id:int}", title="Entrenamiento — Gym Tracker")
async def training_page(workout_id: int):
    ctx = current_context()
    user = current_user()

    try:
        workout = await run.io_bound(workouts.get_workout, ctx, workout_id)
    except ServiceError as exc:
        ui.notify(error_message(exc), type="negative", position="top")
        ui.navigate.to("/")
        return

    open_session = await run.io_bound(sessions.get_open_session, ctx)
    if open_session and open_session.workout_id != workout_id:
        ui.notify(
            f"Tienes una sesión abierta de «{open_session.workout_name}». "
            "Si guardas aquí, se terminará.",
            type="warning",
            position="top",
        )
    if open_session is None or open_session.workout_id != workout_id:
        open_session = None  # training screen shows a fresh start
    # The session this screen logs into; set by the first saved set if none is open.
    session_id = open_session.id if open_session else None

    @dataclass
    class SetRow:
        set_number: int
        weight: float | None = None
        unit: str = "kg"
        reps: int | None = None
        saved: bool = False
        set_id: int | None = None
        # Values as last persisted; a saved row is "dirty" (needs update_set)
        # when weight/reps drift from these.
        orig_weight: float | None = None
        orig_reps: int | None = None

    @dataclass
    class ExerciseBlock:
        item: WorkoutExerciseItem
        rows: list[SetRow] = field(default_factory=list)
        last_line: str = "Aún sin historial"
        # Compact view: one weight/reps pair applied to `num_sets` sets.
        detailed: bool = False
        weight: float | None = None
        unit: str = "kg"
        reps: int | None = None
        num_sets: int = 0
        # Saved sets removed on screen; deleted from the DB on the next save.
        deleted_ids: list[int] = field(default_factory=list)

        @property
        def name(self) -> str:
            return self.item.exercise_name_es or self.item.exercise_name

        def uniform(self) -> bool:
            return len({(r.weight, r.unit, r.reps) for r in self.rows}) <= 1

        def rows_to_summary(self):
            first = self.rows[0] if self.rows else None
            if first is not None:
                self.weight, self.unit, self.reps = first.weight, first.unit, first.reps
            self.num_sets = len(self.rows)

        def summary_to_rows(self):
            """Make the per-set rows match the compact line: same weight/reps
            everywhere, `num_sets` rows (extra saved ones queued for delete)."""
            count = max(0, int(self.num_sets or 0))
            for row in self.rows[count:]:
                if row.saved:
                    self.deleted_ids.append(row.set_id)
            del self.rows[count:]
            while len(self.rows) < count:
                n = (self.rows[-1].set_number + 1) if self.rows else 1
                self.rows.append(SetRow(set_number=n))
            for row in self.rows:
                row.weight, row.unit, row.reps = self.weight, self.unit, self.reps

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
                            orig_weight=existing.weight.value if existing.weight else None,
                            orig_reps=existing.reps,
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
            block = ExerciseBlock(
                item=item,
                rows=rows,
                last_line=_fmt_last(perf) if perf else "Aún sin historial",
                unit=user.unit_pref,
            )
            block.rows_to_summary()
            # Sets already logged with different values only fit the detailed view.
            block.detailed = any(r.saved for r in rows) and not block.uniform()
            blocks.append(block)
        return blocks

    blocks = await build_blocks()

    with page_shell("Entrenamiento"):
        if workout.routine_name and workout.routine_name != workout.name:
            ui.label(workout.routine_name).classes("text-sm text-gray-500")
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(workout.name).classes("text-2xl font-bold")
            state_label = ui.label(
                "sesión abierta" if open_session else "sin empezar"
            ).classes("text-xs text-gray-500 self-end")
        if workout.notes:
            ui.label(workout.notes).classes("text-sm text-gray-600 whitespace-pre-line").mark("day-notes")

        container = ui.column().classes("w-full gap-4")

        def _render_blocks():
            container.clear()
            with container:
                for block in blocks:
                    _render_exercise(block)

        def _render_exercise(block: ExerciseBlock):
            with ui.card().classes("w-full"):
                with ui.row().classes("w-full items-center justify-between no-wrap"):
                    ui.label(block.name).classes("text-xl font-bold leading-tight")
                    ui.button(
                        icon="help_outline",
                        on_click=lambda exercise_id=block.item.exercise_id: ui.navigate.to(
                            f"/exercises/{exercise_id}"
                        ),
                    ).props("flat round dense").tooltip("Cómo se hace")

                target = (
                    f"{block.item.target_sets} × {block.item.target_reps_min}"
                    + (
                        f"–{block.item.target_reps_max}"
                        if block.item.target_reps_max != block.item.target_reps_min
                        else ""
                    )
                )
                ui.label(f"Objetivo: {target}").classes("text-sm text-gray-500")
                if block.item.comment:
                    ui.label(block.item.comment).classes("text-sm text-gray-500 italic")
                ui.label(block.last_line).classes("text-sm text-blue-600")

                if block.detailed:
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

                    with ui.row().classes("w-full gap-2"):
                        ui.button("Agregar serie", icon="add", on_click=add_row).props("outline dense").classes(
                            "grow"
                        )
                        ui.button(
                            "Resumir", icon="unfold_less", on_click=lambda block=block: toggle_detail(block)
                        ).props("flat dense").mark(f"detail-{block.item.exercise_id}")
                else:
                    _render_summary_row(block)
                    ui.button(
                        "Detallar", icon="unfold_more", on_click=lambda block=block: toggle_detail(block)
                    ).props("flat dense").mark(f"detail-{block.item.exercise_id}")

        def toggle_detail(block: ExerciseBlock):
            if block.detailed:
                if not block.uniform():
                    ui.notify(
                        f"{block.name}: las series tienen valores distintos; "
                        "al guardar se usarán los de la serie 1 para todas",
                        type="warning",
                        position="top",
                    )
                block.rows_to_summary()
                block.detailed = False
            else:
                block.summary_to_rows()
                block.detailed = True
            _render_blocks()

        def _weight_input(target, step: float):
            """Weight number field bound to `target.weight`, with ± buttons."""
            weight_input = (
                ui.number("Peso", value=target.weight, step=step, min=0)
                .props("outlined dense inputmode=decimal standout stack-label")
                .style("width: 4.75rem; min-width: 4.75rem")
                .tooltip("Peso (vacío = peso corporal)")
                .bind_value_to(target, "weight")  # typed values survive re-renders
            )
            _bump_buttons(weight_input, step)

        def _int_input(target, attr: str, label: str, max_value: int):
            number_input = (
                ui.number(label, value=getattr(target, attr), step=1, min=0, max=max_value)
                .props("outlined dense inputmode=numeric stack-label")
                .style("width: 3.5rem; min-width: 3.5rem")
                .tooltip(label)
                .bind_value_to(target, attr)
            )
            _bump_buttons(number_input, 1)

        def _bump_buttons(number_input, step: float):
            with ui.column().classes("gap-0 shrink-0"):
                ui.button(icon="add", on_click=lambda: _bump(number_input, step)).props("flat dense size=xs")
                ui.button(icon="remove", on_click=lambda: _bump(number_input, -step)).props("flat dense size=xs")

        def _render_summary_row(block: ExerciseBlock):
            with ui.row().classes("w-full items-center gap-1 no-wrap"):
                _weight_input(block, WEIGHT_STEPS.get(block.unit, 2.5))
                _int_input(block, "reps", "Reps", 100)
                _int_input(block, "num_sets", "Series", 20)

                def skip(block=block):
                    block.num_sets = 0
                    _render_blocks()

                ui.button(icon="delete", on_click=skip).props("flat round dense color=red").tooltip(
                    "Quitar todas las series"
                )

        def _render_set_row(block: ExerciseBlock, row: SetRow):
            with ui.row().classes("w-full items-center gap-1 no-wrap"):
                ui.badge(f"{row.set_number}").props("dense")
                _weight_input(row, WEIGHT_STEPS.get(row.unit, 2.5))
                _int_input(row, "reps", "Reps", 100)

                def remove(row=row, block=block):
                    if row.saved:
                        block.deleted_ids.append(row.set_id)
                    block.rows.remove(row)
                    _render_blocks()

                ui.button(icon="delete", on_click=remove).props("flat round dense color=red").tooltip(
                    "Eliminar serie"
                )
                if not row.saved:
                    ui.icon("radio_button_unchecked").classes("text-gray-400").tooltip("Aún no guardada")

        def _bump(number_input, delta: float):
            current = number_input.value or 0
            new_value = round(current + delta, 2)
            number_input.set_value(max(0.0, new_value))

        async def save_all() -> bool:
            """The single save for the whole routine: delete removed sets, log
            new ones and persist edits.

            An exercise is complete when it has sets and every set has reps.
            Incomplete exercises are skipped (kept on screen, unsaved); at least
            one complete exercise is needed. Returns False if nothing was saved."""
            nonlocal session_id
            for block in blocks:
                if not block.detailed:
                    block.summary_to_rows()
            complete = [b for b in blocks if b.rows and all(r.reps is not None for r in b.rows)]
            removed = [b for b in blocks if not b.rows]  # 🗑: only pending deletes to apply
            skipped = [b for b in blocks if b.rows and b not in complete]
            if not complete:
                ui.notify(
                    "Completa al menos un ejercicio: indica las reps de todas sus series",
                    type="warning",
                    position="top",
                )
                return False
            saved_new = updated = deleted = 0
            try:
                for block in removed + complete:
                    while block.deleted_ids:
                        await run.io_bound(sessions.delete_set, ctx, block.deleted_ids[0])
                        block.deleted_ids.pop(0)
                        deleted += 1
                    for row in block.rows:
                        if not row.saved:
                            item = await run.io_bound(
                                sessions.log_set,
                                ctx,
                                workout.id,
                                block.item.exercise_id,
                                int(row.reps),
                                row.weight,
                                row.unit if row.weight is not None else None,
                                row.set_number,
                            )
                            row.saved = True
                            row.set_id = item.id
                            row.orig_weight, row.orig_reps = row.weight, row.reps
                            session_id = item.session_id
                            saved_new += 1
                        elif (row.weight, row.reps) != (row.orig_weight, row.orig_reps):
                            await run.io_bound(
                                sessions.update_set,
                                ctx,
                                row.set_id,
                                int(row.reps),
                                row.weight,
                                row.unit if row.weight is not None else None,
                                True,  # set_weight
                            )
                            row.orig_weight, row.orig_reps = row.weight, row.reps
                            updated += 1
            except ServiceError as exc:
                ui.notify(error_message(exc), type="negative", position="top")
                _render_blocks()
                return False
            if session_id is not None:
                state_label.text = "sesión abierta"
            if saved_new or updated or deleted:
                parts = []
                if saved_new:
                    plural = "s" if saved_new != 1 else ""
                    parts.append(f"{saved_new} serie{plural} guardada{plural}")
                if updated:
                    parts.append(f"{updated} actualizada{'s' if updated != 1 else ''}")
                if deleted:
                    parts.append(f"{deleted} eliminada{'s' if deleted != 1 else ''}")
                ui.notify(", ".join(parts), type="positive", position="top")
            else:
                ui.notify("No hay nada que guardar", type="info", position="top")
            if skipped:
                ui.notify(
                    "Sin guardar (faltan reps): " + ", ".join(b.name for b in skipped),
                    type="warning",
                    position="top",
                )
            _render_blocks()
            return True

        async def do_finish():
            if not await save_all():
                return
            try:
                if session_id is not None:
                    await run.io_bound(sessions.finish_session, ctx, session_id)
            except ServiceError as exc:
                ui.notify(error_message(exc), type="negative", position="top")
                return
            ui.notify("Rutina terminada. ¡Buen trabajo!", type="positive", position="top")
            ui.navigate.to("/")

        _render_blocks()

        with ui.column().classes("w-full gap-2"):
            ui.button("Guardar", icon="save", on_click=save_all).props("unelevated color=primary").classes(
                "w-full"
            ).mark("save-all")
            ui.button("Terminar rutina", icon="flag", on_click=do_finish).props(
                "unelevated color=positive"
            ).classes("w-full").mark("finish")
