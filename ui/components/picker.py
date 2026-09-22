"""Exercise picker dialog: debounced search, filters, paged lazy thumbnails.

Used from the workout editor; the caller passes an ``on_pick`` callback so
several exercises can be added before closing.
"""

from dataclasses import dataclass, field

from nicegui import run, ui

from services.context import UserContext
from services.errors import ServiceError
from services.schemas import ExerciseSummary

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


async def exercise_picker(ctx: UserContext, on_pick, *, close_label: str = "Done") -> None:
    """Open the picker dialog. ``on_pick(exercise)`` is called per selection."""
    state = PickerState()
    options = await run.io_bound(_filter_options, ctx)

    with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full h-full no-shadow"):
        with ui.column().classes("w-full h-full p-4 gap-3"):
            ui.label("Add exercises").classes("text-xl font-bold")

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

            def _render_results():
                with results_container:
                    results_container.clear()
                    with ui.column().classes("w-full gap-2 p-1"):
                        if not state.results:
                            ui.label("No matching exercises.").classes("text-gray-500 p-4")
                        for ex in state.results:
                            with ui.card().classes("w-full p-2"):
                                with ui.row().classes("items-center gap-3"):
                                    _thumbnail(ex)
                                    with ui.column().classes("grow gap-0"):
                                        ui.label(ex.name).classes("font-medium text-sm")
                                        detail = ", ".join(
                                            filter(
                                                None,
                                                [", ".join(ex.primary_muscles[:2]), ", ".join(ex.equipment[:2])],
                                            )
                                        )
                                        ui.label(detail).classes("text-xs text-gray-500")
                                    ui.button(icon="add", on_click=lambda ex=ex: on_pick(ex)).props(
                                        "round dense unelevated color=primary"
                                    ).tooltip("Add")
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

            def _set_filter(name: str, value):
                setattr(state, name, value or None)
                run_search(True)

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
    dialog.open()


def _thumbnail(ex: ExerciseSummary) -> None:
    url = next((m.url for m in ex.media if m.type == "image"), None)
    if url:
        ui.html(
            f'<img src="{url}" loading="lazy" style="width:56px;height:56px;object-fit:cover;'
            f'border-radius:8px;background:#e2e8f0" alt="">'
        )
    else:
        ui.icon("fitness_center").classes("text-3xl text-gray-400")
