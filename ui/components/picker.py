"""Exercise picker dialog: debounced search, filters, paged lazy thumbnails.

Used from the workout editor; the caller passes an ``on_pick`` callback so
several exercises can be added before closing.
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from nicegui import run, ui

from services.context import UserContext
from services.errors import ServiceError
from services.schemas import ExerciseSummary
from ui.components.exercise_details import open_exercise_dialog
from ui.components.media import exercise_image, thumbnail_url

PAGE_SIZE = 30
SEARCH_DEBOUNCE_S = 0.3


@dataclass
class PickerState:
    query: str = ""
    muscle: str | None = None
    equipment: str | None = None
    source: str | None = None
    cursor: int | None = None
    results: list[ExerciseSummary] = field(default_factory=list)
    more_available: bool = False


def _search_exercises(ctx, **kwargs):
    from services.catalog import search_exercises

    return search_exercises(ctx, **kwargs)


def _filter_options(ctx):
    from services.catalog import filter_options

    return filter_options(ctx)


async def exercise_picker(
    ctx: UserContext,
    on_pick,
    *,
    selected_ids: Callable[[], Collection[int]] | None = None,
    on_remove=None,
    close_label: str = "Done",
) -> None:
    """Open the picker dialog. ``on_pick(exercise)`` is called per selection.

    If ``selected_ids`` is given, exercises already selected show as added;
    tapping an added exercise calls ``on_remove(exercise)`` when provided.
    """
    state = PickerState()
    options = await run.io_bound(_filter_options, ctx)

    with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full h-full no-shadow"):
        with ui.column().classes("w-full h-full p-4 gap-3"):
            title = ui.label().classes("text-xl font-bold")

            def _update_title():
                count = len(selected_ids()) if selected_ids else 0
                title.text = f"Add exercises ({count} added)" if count else "Add exercises"

            _update_title()

            results_container = ui.scroll_area().classes("w-full grow")

            async def run_search(reset: bool = True):
                if reset:
                    state.cursor = None
                    state.results = []
                try:
                    page = await run.io_bound(
                        _search_exercises,
                        ctx,
                        query=state.query,
                        muscle=state.muscle,
                        equipment=state.equipment,
                        source=state.source,
                        limit=PAGE_SIZE,
                        cursor=state.cursor,
                    )
                except ServiceError as exc:
                    ui.notify(str(exc), type="negative", position="top")
                    return
                if reset:
                    state.results = list(page.items)
                else:
                    state.results.extend(page.items)
                state.more_available = page.more_available
                state.cursor = int(page.next_cursor) if page.next_cursor else None
                _render_results()

            def _toggle_button(ex: ExerciseSummary):
                """'+' adds; once added it shows a check, and tapping it again removes."""
                button = ui.button().props("round dense unelevated").mark(f"pick-{ex.id}")
                with button:
                    tooltip = ui.tooltip()

                def refresh():
                    added = selected_ids is not None and ex.id in selected_ids()
                    button.props(f"icon={'check' if added else 'add'} color={'positive' if added else 'primary'}")
                    tooltip.text = ("Remove" if on_remove else "Added") if added else "Add"
                    # Without on_remove, an added exercise can't be toggled back.
                    button.set_enabled(not added or on_remove is not None)

                def toggle():
                    if selected_ids is not None and ex.id in selected_ids():
                        if on_remove is not None:
                            on_remove(ex)
                    else:
                        on_pick(ex)
                    refresh()
                    _update_title()

                button.on_click(toggle)
                refresh()

            def _render_results():
                with results_container:
                    results_container.clear()
                    with ui.column().classes("w-full gap-2 p-1"):
                        if not state.results:
                            ui.label("No matching exercises.").classes("text-gray-500 p-4")
                        for ex in state.results:
                            with ui.card().classes("w-full p-2"):
                                with ui.row().classes("items-center gap-3 w-full no-wrap"):
                                    # Thumbnail/name open details in a dialog over the picker.
                                    with (
                                        ui.row()
                                        .classes("items-center gap-3 grow no-wrap cursor-pointer")
                                        .on("click", lambda ex=ex: open_exercise_dialog(ctx, ex.id))
                                        .mark(f"details-{ex.id}")
                                    ):
                                        exercise_image(thumbnail_url(ex.media), 56)
                                        with ui.column().classes("grow gap-0"):
                                            ui.label(ex.name).classes("font-medium text-sm")
                                            detail = ", ".join(
                                                filter(
                                                    None,
                                                    [", ".join(ex.primary_muscles[:2]), ", ".join(ex.equipment[:2])],
                                                )
                                            )
                                            ui.label(detail).classes("text-xs text-gray-500")
                                    _toggle_button(ex)
                        if state.more_available:
                            ui.button("Load more", on_click=lambda: run_search(False)).props(
                                "outline"
                            ).classes("w-full")

            search = (
                ui.input(placeholder="Search exercises…").props("clearable outlined dense").classes("w-full")
            )
            # Keep state.query in sync on every keystroke, but only run the
            # search on a debounced (300 ms) trailing event.
            search.on("update:model-value", lambda e: setattr(state, "query", e.args or ""))
            search.on(
                "update:model-value",
                lambda: run_search(True),
                throttle=SEARCH_DEBOUNCE_S,
                leading_events=False,
                trailing_events=True,
            )

            async def _set_filter(name: str, value):
                setattr(state, name, value or None)
                await run_search(True)

            with ui.row().classes("w-full gap-2 wrap"):
                ui.select(
                    [""] + options["muscles"],
                    value="",
                    label="Muscle",
                    on_change=lambda e: _set_filter("muscle", e.value),
                ).props("dense outlined").classes("min-w-32 grow")
                ui.select(
                    [""] + options["equipment"],
                    value="",
                    label="Equipment",
                    on_change=lambda e: _set_filter("equipment", e.value),
                ).props("dense outlined").classes("min-w-32 grow")
                ui.select(
                    {**{s: s.replace("_", " ") for s in options["sources"]}, "": "All sources"},
                    value="",
                    label="Source",
                    on_change=lambda e: _set_filter("source", e.value),
                ).props("dense outlined").classes("min-w-32 grow")

            with ui.row().classes("w-full justify-end"):
                ui.button(close_label, on_click=dialog.close).props("unelevated")

        await run_search(True)
    # A fresh dialog is built per open; drop it once closed so stale ones don't pile up.
    dialog.on_value_change(lambda e: dialog.delete() if not e.value else None)
    dialog.open()

