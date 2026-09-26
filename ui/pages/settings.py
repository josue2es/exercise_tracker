"""Settings page: unit, time zone, password, API keys."""

from zoneinfo import available_timezones

from nicegui import run, ui

import services.users as users
from services.errors import ServiceError
from ui.auth import current_context, current_user
from ui.components.api_keys import api_keys_section
from ui.i18n import error_message
from ui.layout import page_shell


@ui.page("/settings", title="Ajustes — Gym Tracker")
async def settings_page():
    ctx = current_context()
    user = current_user()
    timezones = sorted(available_timezones())

    with page_shell("Ajustes"):
        with ui.card().classes("w-full"):
            ui.label("Preferencias").classes("text-lg font-semibold")
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Unidad de peso")
                unit_toggle = ui.toggle({"kg": "kg", "lb": "lb"}, value=user.unit_pref).props("dense")

            tz_select = (
                ui.select(
                    timezones,
                    value=user.time_zone,
                    label="Zona horaria",
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
                    ui.notify(error_message(exc), type="negative", position="top")
                    return
                ui.notify("Preferencias guardadas", type="positive", position="top")

            ui.button("Guardar preferencias", on_click=save_preferences).props("unelevated")

        with ui.card().classes("w-full"):
            ui.label("Cambiar contraseña").classes("text-lg font-semibold")
            current = ui.input("Contraseña actual", password=True, password_toggle_button=True).classes("w-full")
            new = ui.input("Nueva contraseña", password=True, password_toggle_button=True).classes("w-full")
            repeat = ui.input(
                "Repite la nueva contraseña", password=True, password_toggle_button=True
            ).classes("w-full")

            async def do_change_password():
                if new.value != repeat.value:
                    ui.notify("Las contraseñas nuevas no coinciden", type="negative", position="top")
                    return
                try:
                    await run.io_bound(users.change_password, ctx, current.value, new.value)
                except ServiceError as exc:
                    ui.notify(error_message(exc), type="negative", position="top")
                    return
                ui.notify("Contraseña cambiada", type="positive", position="top")
                current.value = new.value = repeat.value = ""

            ui.button("Cambiar contraseña", on_click=do_change_password).props("unelevated")

        with ui.card().classes("w-full"):
            ui.label("Claves API").classes("text-lg font-semibold")
            ui.label(
                "Las claves permiten que tus agentes locales lean tus datos y registren series por REST o MCP."
            ).classes("text-sm text-gray-500")
            await api_keys_section(ctx)
