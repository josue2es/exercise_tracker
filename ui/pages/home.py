"""Home page (workout list arrives in milestone 3)."""

from nicegui import ui

from ui.layout import page_shell


@ui.page("/", title="Gym Tracker")
def home_page():
    with page_shell("Workouts"):
        ui.label("Workouts").classes("text-2xl font-bold")
        ui.label("The workout list, editor and training screens arrive in the next milestone.").classes(
            "text-gray-500"
        )
