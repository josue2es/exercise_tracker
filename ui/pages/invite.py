"""Accept-invite page: the invitee sets a name and password."""

from nicegui import run, ui

import services.users as users
from services.errors import ServiceError
from ui.auth import login_user
from ui.i18n import error_message


@ui.page("/invite/{token}", title="Aceptar invitación — Gym Tracker")
def invite_page(token: str):
    async def accept():
        if password.value != confirm.value:
            ui.notify("Las contraseñas no coinciden", type="negative", position="top")
            return
        try:
            user_id = await run.io_bound(users.accept_invite, token, name.value, password.value)
        except ServiceError as exc:
            ui.notify(error_message(exc), type="negative", position="top")
            return
        login_user(user_id)
        ui.notify("¡Bienvenido/a! Tu cuenta está lista.", type="positive", position="top")
        ui.navigate.to("/")

    with ui.column().classes("max-w-sm mx-auto w-full px-6 py-12 gap-4 self-center"):
        from ui.layout import page_head

        page_head()
        ui.label("Configura tu cuenta").classes("text-2xl font-bold text-center")
        ui.label("Te invitaron a Gym Tracker. Elige un nombre y una contraseña (10 caracteres o más).").classes(
            "text-sm text-gray-500 text-center"
        )
        # HTML autocomplete goes in props; NiceGUI's `autocomplete=` kwarg is a suggestion list.
        name = ui.input("Tu nombre").props("autocomplete=name").classes("w-full")
        password = (
            ui.input("Contraseña", password=True, password_toggle_button=True)
            .props("autocomplete=new-password")
            .classes("w-full")
        )
        confirm = (
            ui.input("Repite la contraseña", password=True, password_toggle_button=True)
            .props("autocomplete=new-password")
            .classes("w-full")
        )
        ui.button("Crear cuenta", on_click=accept).classes("w-full").props("unelevated size=md")
        name.on("keydown.enter", lambda: password.focus())
        password.on("keydown.enter", lambda: confirm.focus())
        confirm.on("keydown.enter", accept)
