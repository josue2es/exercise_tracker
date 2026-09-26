"""Exercise detail content shared by the /exercises/{id} page and the in-app
detail dialog (opened from the picker and workout editor rows)."""

from nicegui import run, ui

import services.catalog as catalog
from services.context import UserContext
from services.errors import ServiceError
from services.schemas import ExerciseDetail
from ui.components.media import exercise_image
from ui.i18n import error_message, term, terms


def lightbox(urls: list[str], name: str):
    """Full-screen image viewer; returns ``open(index)``. Arrows page through
    several images; tapping the image or the close button dismisses it."""
    current = {"index": 0}

    with ui.dialog().props("maximized") as dialog, ui.card().classes(
        "w-full h-full items-center justify-center gap-2 bg-black no-shadow"
    ):
        ui.button(icon="close", on_click=dialog.close).props("flat round color=white").classes(
            "absolute top-2 right-2 z-10"
        )
        img = ui.element("img").style("max-width:100%;max-height:85vh;object-fit:contain")
        img.props["alt"] = name
        img.on("click", dialog.close)
        with ui.row().classes("items-center gap-4") as nav:
            prev_button = ui.button(icon="chevron_left", on_click=lambda: show(current["index"] - 1)).mark(
                "lightbox-prev"
            )
            counter = ui.label().classes("text-white text-sm")
            next_button = ui.button(icon="chevron_right", on_click=lambda: show(current["index"] + 1)).mark(
                "lightbox-next"
            )
        for button in (prev_button, next_button):
            button.props("flat round color=white")
        nav.set_visibility(len(urls) > 1)

    def show(index: int):
        current["index"] = index % len(urls)
        img.props["src"] = urls[current["index"]]
        img.update()
        counter.text = f"{current['index'] + 1} / {len(urls)}"

    def open_at(index: int):
        show(index)
        dialog.open()

    return open_at


def exercise_details(exercise: ExerciseDetail) -> None:
    """Media (tap to enlarge), muscles/equipment/level, steps and attribution."""
    if exercise.retired_at:
        ui.badge("retirado del catálogo", color="grey").classes("mb-1")

    # Media: local images for free-exercise-db, GIF for ExerciseDB. Tap to enlarge.
    images = [m.url for m in exercise.media]
    if images:
        open_lightbox = lightbox(images, exercise.name)
        with ui.row().classes("w-full justify-center gap-2 wrap"):
            for i, url in enumerate(images):
                gif = url.endswith(".gif")
                exercise_image(url, 240 if gif else 150, fit="contain" if gif else "cover").classes(
                    "cursor-pointer"
                ).on("click", lambda i=i: open_lightbox(i)).mark(f"detail-image-{i}")
    else:
        ui.icon("fitness_center").classes("text-6xl text-gray-400")

    with ui.card().classes("w-full"):
        ui.label("Músculos").classes("font-semibold")
        ui.label(terms(exercise.primary_muscles) or "—").classes("text-sm")
        if exercise.secondary_muscles:
            ui.label(f"Secundarios: {terms(exercise.secondary_muscles)}").classes("text-xs text-gray-500")
        if exercise.body_parts:
            ui.label(f"Zonas del cuerpo: {terms(exercise.body_parts)}").classes("text-xs text-gray-500")
        if exercise.equipment:
            ui.label(f"Equipo: {terms(exercise.equipment)}").classes("text-xs text-gray-500")
        if exercise.level:
            ui.label(f"Nivel: {term(exercise.level)}").classes("text-xs text-gray-500")

    if exercise.instructions:
        with ui.card().classes("w-full"):
            ui.label("Cómo se hace").classes("font-semibold")
            for i, step in enumerate(exercise.instructions, start=1):
                ui.label(f"{i}. {step}").classes("text-sm")

    if exercise.attribution:
        ui.label(exercise.attribution).classes("text-xs text-gray-400")


async def open_exercise_dialog(ctx: UserContext, exercise_id: int) -> None:
    """Show an exercise's details in a dialog, keeping the caller's page state."""
    try:
        exercise = await run.io_bound(catalog.get_exercise, ctx, exercise_id)
    except ServiceError as exc:
        ui.notify(error_message(exc), type="negative", position="top")
        return

    with ui.dialog().props("maximized") as dialog, ui.card().classes("w-full h-full no-shadow p-0"):
        with ui.column().classes("w-full h-full p-4 gap-3 no-wrap"):
            with ui.row().classes("items-center w-full no-wrap"):
                ui.label(exercise.name).classes("text-xl font-bold grow").mark("detail-title")
                ui.button(icon="close", on_click=dialog.close).props("flat round").mark("detail-close")
            with ui.scroll_area().classes("w-full grow"):
                with ui.column().classes("w-full gap-3 p-1"):
                    exercise_details(exercise)
    # Built per open; drop it once closed (the lightbox inside goes with it).
    dialog.on_value_change(lambda e: dialog.delete() if not e.value else None)
    dialog.open()
