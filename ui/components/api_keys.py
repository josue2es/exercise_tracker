"""API keys section for the Settings page: create, list, revoke.

The full key is shown exactly once, in a dialog right after creation.
"""

from nicegui import run, ui

import services.api_keys as api_keys
from services.context import UserContext
from services.errors import ServiceError


def _show_new_key_dialog(raw_key: str, label: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label(f"Key “{label}” created").classes("text-lg font-semibold")
        ui.label(
            "Copy it now — for security, the full key is shown only this once. "
            "Send it as: Authorization: Bearer <key>"
        ).classes("text-sm text-gray-500")
        ui.input("API key", value=raw_key).props("readonly outlined dense").classes("w-full")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(
                "Copy",
                icon="content_copy",
                on_click=lambda: ui.run_javascript(
                    f"navigator.clipboard.writeText({raw_key!r}).catch(()=>{{}})"
                ),
            ).props("unelevated")
            ui.button("Done", on_click=dialog.close).props("flat")
    dialog.open()


async def api_keys_section(ctx: UserContext) -> None:
    """Render the API keys card into the current page."""
    with ui.card().classes("w-full"):
        ui.label("API keys").classes("text-lg font-semibold")
        label_input = ui.input("Label", placeholder="e.g. laptop agent").props("outlined dense").classes("w-full")
        scope_toggle = (
            ui.toggle({"read": "Read only", "read+write": "Read + write"}, value="read")
            .props("dense")
            .classes("w-full")
        )

        async def do_create():
            scopes = ["read", "write"] if scope_toggle.value == "read+write" else ["read"]
            try:
                info, raw_key = await run.io_bound(
                    api_keys.create_key, ctx, label_input.value, scopes
                )
            except ServiceError as exc:
                ui.notify(str(exc), type="negative", position="top")
                return
            _show_new_key_dialog(raw_key, info.label)
            label_input.value = ""
            await render()

        ui.button("Create key", icon="key", on_click=do_create).props("unelevated")

        container = ui.column().classes("w-full gap-1")

        async def render():
            container.clear()
            with container:
                keys = await run.io_bound(api_keys.list_keys, ctx)
                if not keys:
                    ui.label("No keys yet.").classes("text-sm text-gray-500")
                for key in keys:
                    status = "revoked" if key.revoked_at else "active"
                    with ui.row().classes(
                        "w-full items-center justify-between bg-slate-100 dark:bg-slate-800 rounded-lg p-3"
                    ):
                        with ui.column().classes("gap-0"):
                            ui.label(f"{key.label} ({key.key_prefix}…)").classes("font-medium")
                            scopes = "+".join(key.scopes)
                            ui.label(f"{scopes} · {status}").classes("text-xs text-gray-500")
                        if not key.revoked_at:

                            async def revoke(key_id=key.id):
                                try:
                                    await run.io_bound(api_keys.revoke_key, ctx, key_id)
                                except ServiceError as exc:
                                    ui.notify(str(exc), type="negative", position="top")
                                    return
                                await render()

                            ui.button(icon="block", on_click=revoke).props(
                                "flat round dense color=red"
                            ).tooltip("Revoke")

        await render()
