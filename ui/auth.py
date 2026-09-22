"""Authentication plumbing for the NiceGUI UI.

A single middleware enforces login on every page and re-checks is_active on
each page load, so no page can forget the check. It must be added to the
NiceGUI app *before* ``ui.run_with`` is called, so that it runs after the
session and request-tracking middlewares (innermost).
"""

from __future__ import annotations

import time
from urllib.parse import quote

from nicegui import app
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

import services.users as users_service
from services.context import UserContext, ui_context
from services.errors import AuthError

SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30-day sliding expiry

PUBLIC_PATHS = {
    "/login",
    "/healthz",
    "/favicon.ico",
    "/manifest.webmanifest",
    "/robots.txt",
}

PUBLIC_PATH_PREFIXES = (
    "/invite/",
    "/_nicegui",  # NiceGUI assets and websocket
    "/_nicegui_ws",
    "/media/",
    "/api/",  # agent API: Bearer-key auth, not cookie auth
    "/mcp",  # MCP server: Bearer-key auth
    "/_iconify",
    "/_static",
)


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path == p.rstrip("/") or path.startswith(p) for p in PUBLIC_PATH_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Require a logged-in, active user for every non-public page."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if is_public_path(path):
            return await call_next(request)

        try:
            user_id = app.storage.user.get("user_id")
        except Exception:  # no session context: treat as logged out
            user_id = None
        if user_id is None:
            return RedirectResponse(f"/login?next={quote(path)}", status_code=303)

        # Re-check is_active on page loads (skip static asset requests for speed).
        if not path.startswith("/_"):
            active = await run_in_threadpool(users_service.is_user_active, user_id)
            if not active:
                app.storage.user.clear()
                return RedirectResponse("/login", status_code=303)

        # Sliding 30-day session: touching the session re-issues the cookie.
        request.session["seen"] = int(time.time())
        return await call_next(request)


# --- helpers for page handlers ---------------------------------------------------


def current_user():
    """Return the logged-in UserInfo, or None (defense in depth; the
    middleware should already have redirected)."""
    user_id = app.storage.user.get("user_id")
    if user_id is None:
        return None
    return users_service.get_user(user_id)


def current_context() -> UserContext:
    """Build the UserContext for page handlers; raises if not logged in."""
    user = current_user()
    if user is None:
        raise AuthError("Not logged in")
    return ui_context(user.id, user.role)


def login_user(user_id: int) -> None:
    app.storage.user["user_id"] = user_id


def logout_user() -> None:
    app.storage.user.clear()
