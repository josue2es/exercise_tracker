"""Admin page: users (deactivate) and invites (create, copy link, revoke)."""

from nicegui import run, ui

import services.users as users
from services.errors import ServiceError
from ui.auth import current_context
from ui.layout import page_shell


def _show_invite_link_dialog(link: str) -> None:
    with ui.dialog() as dialog, ui.card().classes("w-full max-w-md"):
        ui.label("Invite created").classes("text-lg font-semibold")
        ui.label("Copy this single-use link and send it to the invitee. It expires in 7 days.").classes(
            "text-sm text-gray-500"
        )
        ui.input("Invite link", value=link).props("readonly outlined dense").classes("w-full")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(
                "Copy",
                icon="content_copy",
                on_click=lambda: ui.run_javascript(
                    f"navigator.clipboard.writeText({link!r}).catch(()=>{{}})"
                ),
            ).props("unelevated")
            ui.button("Done", on_click=dialog.close).props("flat")
    dialog.open()


@ui.page("/admin", title="Admin — Gym Tracker")
async def admin_page():
    ctx = current_context()

    with page_shell("Admin"):
        with ui.card().classes("w-full"):
            ui.label("Invites").classes("text-lg font-semibold")
            invite_email = ui.input("Email", placeholder="friend@example.com").classes("w-full")
            invite_role = (
                ui.select({"member": "Member", "admin": "Admin"}, value="member", label="Role")
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
                    ui.notify(str(exc), type="negative", position="top")
                    return
                _show_invite_link_dialog(settings.base_url.rstrip("/") + path)
                invite_email.value = ""
                await _render()

            ui.button("Create invite", icon="person_add", on_click=do_create_invite).props("unelevated")

        invites_container = ui.column().classes("w-full gap-2")
        users_container = ui.column().classes("w-full gap-2")

        async def _render():
            invites_container.clear()
            users_container.clear()

            with invites_container:
                ui.label("Invites").classes("font-semibold mt-2")
                invites = await run.io_bound(users.list_invites, ctx)
                if not invites:
                    ui.label("No invites yet.").classes("text-sm text-gray-500")
                for invite in invites:
                    from db.models import utcnow

                    status = (
                        "used"
                        if invite.used_at
                        else "revoked"
                        if invite.revoked_at
                        else "expired"
                        if invite.expires_at < utcnow()
                        else "pending"
                    )
                    with ui.row().classes(
                        "w-full items-center justify-between bg-slate-100 dark:bg-slate-800 rounded-lg p-3"
                    ):
                        with ui.column().classes("gap-0"):
                            ui.label(invite.email).classes("font-medium")
                            ui.label(f"{invite.role} · {status}").classes("text-xs text-gray-500")
                        if status == "pending":

                            async def revoke(invite_id=invite.id):
                                try:
                                    await run.io_bound(users.revoke_invite, ctx, invite_id)
                                except ServiceError as exc:
                                    ui.notify(str(exc), type="negative", position="top")
                                    return
                                await _render()

                            ui.button(icon="block", on_click=revoke).props(
                                "flat round dense color=red"
                            ).tooltip("Revoke")

            with users_container:
                with ui.card().classes("w-full"):
                    ui.label("Users").classes("text-lg font-semibold")
                    all_users = await run.io_bound(users.list_users, ctx)
                    for u in all_users:
                        with ui.row().classes("w-full items-center justify-between border-b pb-2"):
                            with ui.column().classes("gap-0"):
                                ui.label(f"{u.display_name} · {u.role}").classes("font-medium")
                                ui.label(u.email).classes("text-xs text-gray-500")
                            with ui.row().classes("gap-1 items-center"):
                                if u.is_active:
                                    ui.badge("active", color="green").props("dense")
                                else:
                                    ui.badge("deactivated", color="grey").props("dense")
                                if u.id != ctx.user_id:

                                    async def toggle(u_id=u.id, active=not u.is_active):
                                        try:
                                            await run.io_bound(users.set_user_active, ctx, u_id, active)
                                        except ServiceError as exc:
                                            ui.notify(str(exc), type="negative", position="top")
                                            return
                                        await _render()

                                    if u.is_active:
                                        ui.button("Deactivate", on_click=toggle).props("outline dense color=red")
                                    else:
                                        ui.button("Activate", on_click=toggle).props("outline dense color=green")

        await _render()
