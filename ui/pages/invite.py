"""Accept-invite page: the invitee sets a name and password."""

from nicegui import run, ui

import services.users as users
from services.errors import ServiceError
from ui.auth import login_user


@ui.page("/invite/{token}", title="Accept invite — Gym Tracker")
def invite_page(token: str):
    async def accept():
        if password.value != confirm.value:
            ui.notify("Passwords do not match", type="negative", position="top")
            return
        try:
            user_id = await run.io_bound(users.accept_invite, token, name.value, password.value)
        except ServiceError as exc:
            ui.notify(str(exc), type="negative", position="top")
            return
        login_user(user_id)
        ui.notify("Welcome! Your account is ready.", type="positive", position="top")
        ui.navigate.to("/")

    with ui.column().classes("max-w-sm mx-auto w-full px-6 py-12 gap-4 self-center"):
        from ui.layout import page_head

        page_head()
        ui.label("Set up your account").classes("text-2xl font-bold text-center")
        ui.label("You were invited to Gym Tracker. Choose a name and a password (10+ characters).").classes(
            "text-sm text-gray-500 text-center"
        )
        # HTML autocomplete goes in props; NiceGUI's `autocomplete=` kwarg is a suggestion list.
        name = ui.input("Your name").props("autocomplete=name").classes("w-full")
        password = (
            ui.input("Password", password=True, password_toggle_button=True)
            .props("autocomplete=new-password")
            .classes("w-full")
        )
        confirm = (
            ui.input("Repeat password", password=True, password_toggle_button=True)
            .props("autocomplete=new-password")
            .classes("w-full")
        )
        ui.button("Create account", on_click=accept).classes("w-full").props("unelevated size=md")
        name.on("keydown.enter", lambda: password.focus())
        password.on("keydown.enter", lambda: confirm.focus())
        confirm.on("keydown.enter", accept)
