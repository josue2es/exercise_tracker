"""Login page."""

from nicegui import run, ui

import services.users as users
from services.errors import ServiceError
from ui.auth import login_user


def _client_ip() -> str:
    """Client IP for login throttling (the real IP comes from Caddy)."""
    from nicegui.storage import request_contextvar

    request = request_contextvar.get()
    if request is None:
        return "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@ui.page("/login", title="Log in — Gym Tracker")
def login_page():
    from nicegui.storage import request_contextvar

    request = request_contextvar.get()
    next_path = request.query_params.get("next", "/") if request else "/"

    async def do_login():
        try:
            user = await run.io_bound(
                users.authenticate, email.value, password.value, _client_ip()
            )
        except ServiceError as exc:
            ui.notify(str(exc), type="negative", position="top")
            return
        login_user(user.id)
        # Only same-site paths: "//host" and "/\\host" are protocol-relative URLs.
        safe = next_path.startswith("/") and not next_path.startswith(("//", "/\\"))
        ui.navigate.to(next_path if safe else "/")

    with ui.column().classes("max-w-sm mx-auto w-full px-6 py-12 gap-4 self-center"):
        from ui.layout import page_head

        page_head()
        ui.label("Gym Tracker").classes("text-2xl font-bold text-center")
        # NiceGUI's `autocomplete=` kwarg is a suggestion list, not the HTML
        # attribute; passing a string crashes the input on the first keystroke.
        email = ui.input("Email").props("type=email autocomplete=email").classes("w-full")
        password = (
            ui.input("Password", password=True, password_toggle_button=True)
            .props("autocomplete=current-password")
            .classes("w-full")
        )
        ui.button("Log in", on_click=do_login).classes("w-full").props("unelevated size=md")
        email.on("keydown.enter", do_login)
        password.on("keydown.enter", do_login)
