"""Admin page: users (deactivate) and invites (create, copy link, revoke)."""

from nicegui import run, ui

import services.users as users
from services.errors import ServiceError
from ui.auth import current_context
from ui.i18n import error_message
from ui.layout import page_shell

ROLES = {"member": "Miembro", "admin": "Administrador"}
INVITE_STATUS = {"used": "usada", "revoked": "revocada", "expired": "expirada", "pending": "pendiente"}


def _show_invite_link_dialog(link: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Invitación creada").classes("text-lg font-semibold")
        ui.label("Copia este enlace de un solo uso y envíaselo a la persona invitada. Expira en 7 días.").classes(
            "text-sm text-gray-500"
        )
        ui.input("Enlace de invitación", value=link).props("readonly outlined dense").classes("w-full")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(
                "Copiar",
                icon="content_copy",
                on_click=lambda: ui.run_javascript(
                    f"navigator.clipboard.writeText({link!r}).catch(()=>{{}})"
                ),
            ).props("unelevated")
            ui.button("Listo", on_click=dialog.close).props("flat")
    dialog.open()


@ui.page("/admin", title="Administración — Gym Tracker")
async def admin_page():
    ctx = current_context()
    if not ctx.is_admin:
        ui.navigate.to("/")
        return

    with page_shell("Administración"):
        with ui.card().classes("w-full"):
            ui.label("Invitaciones").classes("text-lg font-semibold")
            invite_email = ui.input("Correo electrónico", placeholder="amigo@ejemplo.com").classes("w-full")
            invite_role = (
                ui.select(ROLES, value="member", label="Rol")
                .classes("w-full")
                .props("dense")
            )

            async def do_create_invite():
                from config import settings

                try:
                    path = await run.io_bound(
                        users.create_invite, ctx, invite_email.value, invite_role.value
                    )
                except ServiceError as exc:
                    ui.notify(error_message(exc), type="negative", position="top")
                    return
                _show_invite_link_dialog(settings.base_url.rstrip("/") + path)
                invite_email.value = ""
                await _render()

            ui.button("Crear invitación", icon="person_add", on_click=do_create_invite).props("unelevated")

        invites_container = ui.column().classes("w-full gap-2")
        users_container = ui.column().classes("w-full gap-2")

        async def _render():
            invites_container.clear()
            users_container.clear()

            with invites_container:
                ui.label("Invitaciones").classes("font-semibold mt-2")
                invites = await run.io_bound(users.list_invites, ctx)
                if not invites:
                    ui.label("Aún no hay invitaciones.").classes("text-sm text-gray-500")
                for invite in invites:
                    from datetime import UTC, datetime

                    status = (
                        "used"
                        if invite.used_at
                        else "revoked"
                        if invite.revoked_at
                        else "expired"
                        if invite.expires_at < datetime.now(UTC)  # expires_at is UTC-aware
                        else "pending"
                    )
                    with ui.row().classes(
                        "w-full items-center justify-between bg-slate-100 dark:bg-slate-800 rounded-lg p-3"
                    ):
                        with ui.column().classes("gap-0"):
                            ui.label(invite.email).classes("font-medium")
                            role = ROLES.get(invite.role, invite.role)
                            ui.label(f"{role} · {INVITE_STATUS[status]}").classes("text-xs text-gray-500")
                        if status == "pending":

                            async def revoke(invite_id=invite.id):
                                try:
                                    await run.io_bound(users.revoke_invite, ctx, invite_id)
                                except ServiceError as exc:
                                    ui.notify(error_message(exc), type="negative", position="top")
                                    return
                                await _render()

                            ui.button(icon="block", on_click=revoke).props(
                                "flat round dense color=red"
                            ).tooltip("Revocar")

            with users_container:
                with ui.card().classes("w-full"):
                    ui.label("Usuarios").classes("text-lg font-semibold")
                    all_users = await run.io_bound(users.list_users, ctx)
                    for u in all_users:
                        with ui.row().classes("w-full items-center justify-between border-b pb-2"):
                            with ui.column().classes("gap-0"):
                                ui.label(f"{u.display_name} · {ROLES.get(u.role, u.role)}").classes("font-medium")
                                ui.label(u.email).classes("text-xs text-gray-500")
                            with ui.row().classes("gap-1 items-center"):
                                if u.is_active:
                                    ui.badge("activo", color="green").props("dense")
                                else:
                                    ui.badge("desactivado", color="grey").props("dense")
                                if u.id != ctx.user_id:

                                    async def toggle(u_id=u.id, active=not u.is_active):
                                        try:
                                            await run.io_bound(users.set_user_active, ctx, u_id, active)
                                        except ServiceError as exc:
                                            ui.notify(error_message(exc), type="negative", position="top")
                                            return
                                        await _render()

                                    if u.is_active:
                                        ui.button("Desactivar", on_click=toggle).props("outline dense color=red")
                                    else:
                                        ui.button("Activar", on_click=toggle).props("outline dense color=green")

        await _render()
