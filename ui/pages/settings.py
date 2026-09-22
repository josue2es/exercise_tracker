"""Settings page: unit, time zone, password, API keys."""

from zoneinfo import available_timezones

from nicegui import run, ui

import services.users as users
from services.errors import ServiceError
from ui.auth import current_context, current_user
from ui.layout import page_shell


@ui.page("/settings", title="Settings — Gym Tracker")
async def settings_page():
    ctx = current_context()
    user = current_user()
    timezones = sorted(available_timezones())

    with page_shell("Settings"):
        with ui.card().classes("w-full"):
            ui.label("Preferences").classes("text-lg font-semibold")
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Weight unit")
                unit_toggle = ui.toggle({"kg": "kg", "lb": "lb"}, value=user.unit_pref).props("dense")

            tz_select = (
                ui.select(
                    timezones,
                    value=user.time_zone,
                    label="Time zone",
                    with_input=True,
                )
                .classes("w-full")
                .props("dense")
            )

            async def save_preferences():
                try:
                    await run.io_bound(
                        users.update_settings, ctx, unit_toggle.value, tz_select.value
                    )
                except ServiceError as exc:
                    ui.notify(str(exc), type="negative", position="top")
                    return
                ui.notify("Preferences saved", type="positive", position="top")

            ui.button("Save preferences", on_click=save_preferences).props("unelevated")

        with ui.card().classes("w-full"):
            ui.label("Change password").classes("text-lg font-semibold")
            current = ui.input("Current password", password=True, password_toggle_button=True).classes("w-full")
            new = ui.input("New password", password=True, password_toggle_button=True).classes("w-full")
            repeat = ui.input("Repeat new password", password=True, password_toggle_button=True).classes("w-full")

            async def do_change_password():
                if new.value != repeat.value:
                    ui.notify("New passwords do not match", type="negative", position="top")
                    return
                try:
                    await run.io_bound(users.change_password, ctx, current.value, new.value)
                except ServiceError as exc:
                    ui.notify(str(exc), type="negative", position="top")
                    return
                ui.notify("Password changed", type="positive", position="top")
                current.value = new.value = repeat.value = ""

            ui.button("Change password", on_click=do_change_password).props("unelevated")

        with ui.card().classes("w-full"):
            ui.label("API keys").classes("text-lg font-semibold")
            ui.label("Keys let your local agents read your data and log sets over REST or MCP.").classes(
                "text-sm text-gray-500"
            )
            # API key management is added in milestone 5 (see ui/components/api_keys.py).
