"""Headless UI tests (NiceGUI User simulation): picker add/remove toggle,
the workout editor's add/remove flow, and the exercise image lightbox."""

import asyncio
from contextlib import contextmanager

import pytest
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction
from nicegui.testing.user_simulation import user_simulation

import ui.components.exercise_details as details
import ui.components.picker as picker
import ui.pages.workout_editor as editor
from services.schemas import ExerciseDetail, ExerciseSummary, MediaItem, Page
from ui.components.exercise_details import lightbox

EXERCISES = [
    ExerciseSummary(
        id=1,
        source="free_exercise_db",
        name="Ab Roller",
        primary_muscles=["abdominals"],
        media=[MediaItem(type="image", url="/media/free-exercise-db/Ab_Roller/0.jpg")],
    ),
    ExerciseSummary(
        id=2,
        source="exercisedb_v1",
        name="Pull-up",
        primary_muscles=["lats"],
        media=[MediaItem(type="gif", url="https://static.exercisedb.dev/media/lBDjFxJ.gif")],
    ),
]


EXERCISE_DETAILS = {
    ex.id: ExerciseDetail(
        id=ex.id,
        source=ex.source,
        name=ex.name,
        primary_muscles=ex.primary_muscles,
        equipment=["barbell"],
        level="intermediate",
        instructions=["Grab the bar", "Pull"],
        media=ex.media,
    )
    for ex in EXERCISES
}


@pytest.fixture
def stub_catalog(monkeypatch):
    monkeypatch.setattr(picker, "_filter_options", lambda ctx: {"muscles": [], "equipment": [], "sources": []})
    monkeypatch.setattr(picker, "_search_exercises", lambda ctx, **kw: Page(items=EXERCISES))
    monkeypatch.setattr(details, "catalog", type("M", (), {"get_exercise": lambda ctx, eid: EXERCISE_DETAILS[eid]}))


def _icon(user, exercise_id):
    (button,) = user.find(marker=f"pick-{exercise_id}").elements
    return button.props["icon"]


def _row_names(user):
    return sorted(
        label.text
        for label in user.find(ui.label).elements
        if any(m.startswith("row-name-") for m in label._markers)  # noqa: SLF001 (test helper)
    )


def test_picker_toggles_added_state(stub_catalog):
    selected: list[int] = []

    def root():
        async def open_picker():
            await picker.exercise_picker(
                None,
                lambda ex: selected.append(ex.id),
                selected_ids=lambda: set(selected),
                on_remove=lambda ex: selected.remove(ex.id),
            )

        ui.button("Open", on_click=open_picker)

    async def scenario():
        async with user_simulation(root) as user:
            await user.open("/")
            user.find("Open").click()
            await user.should_see("Pull-up")

            # Both remote GIF and local image get a thumbnail <img>.
            srcs = {img.props.get("src") for img in user.find(ui.element).elements if img.tag == "img"}
            assert {m.url for ex in EXERCISES for m in ex.media} <= srcs

            assert _icon(user, 1) == "add"
            user.find(marker="pick-1").click()
            assert selected == [1]
            assert _icon(user, 1) == "check"
            await user.should_see("Agregar ejercicios (1 agregado)")

            user.find(marker="pick-2").click()
            user.find(marker="pick-1").click()  # tapping an added exercise removes it
            assert selected == [2]
            assert (_icon(user, 1), _icon(user, 2)) == ("add", "check")

            # Tapping the thumbnail/name opens the exercise details in a dialog
            # (same content as the detail page) without leaving the picker.
            user.find(marker="details-1").click()
            await user.should_see("Grab the bar")
            (title,) = user.find(marker="detail-title").elements
            assert title.text == "Ab Roller"
            await user.should_see("barra")  # equipment term shown in Spanish
            user.find(marker="detail-close").click()
            await user.should_see("Agregar ejercicios (1 agregado)")

    asyncio.run(scenario())


def test_editor_add_and_remove_flow(stub_catalog, monkeypatch):
    @contextmanager
    def shell(title):
        with ui.column():
            yield

    monkeypatch.setattr(editor, "current_context", lambda: None)
    monkeypatch.setattr(editor, "page_shell", shell)

    async def root():
        await editor._editor_page(None)

    async def scenario():
        async with user_simulation(root) as user:
            await user.open("/")
            user.find("Agregar ejercicio").click()
            await user.should_see("Pull-up")
            user.find(marker="pick-1").click()
            user.find(marker="pick-2").click()
            assert _row_names(user) == ["Ab Roller", "Pull-up"]

            user.find(marker="pick-1").click()  # remove from within the picker
            assert _row_names(user) == ["Pull-up"]
            user.find("Listo").click()

            # The row's close button removes it too.
            (close,) = [b for b in user.find(ui.button).elements if b.props.get("icon") == "close"]
            UserInteraction(user, {close}, None).click()
            await user.should_not_see("Pull-up")

            # Reopening shows a fresh picker (the closed one was deleted) with nothing added.
            user.find("Agregar ejercicio").click()
            await user.should_see("Pull-up")
            assert _icon(user, 2) == "add"
            await user.should_not_see("agregado")

    asyncio.run(scenario())


def test_lightbox_pages_through_images():
    urls = ["/media/a/0.jpg", "/media/a/1.jpg"]

    def root():
        open_at = lightbox(urls, "Squat")
        ui.button("Open", on_click=lambda: open_at(1))

    async def scenario():
        async with user_simulation(root) as user:
            await user.open("/")
            user.find("Open").click()
            await user.should_see("2 / 2")
            (img,) = [e for e in user.find(ui.element).elements if e.tag == "img"]
            assert img.props["src"] == urls[1]
            user.find(marker="lightbox-next").click()  # wraps around
            await user.should_see("1 / 2")
            assert img.props["src"] == urls[0]

    asyncio.run(scenario())
