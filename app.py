"""Composition root.

One uvicorn process (single worker) hosts the NiceGUI UI, the REST API and the
MCP server as thin adapters over the service layer. This module wires them
together and owns the FastAPI lifespan.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from api.router import router as api_router, service_error_handler
from config import settings
from db.session import configure as configure_db
from services.errors import ServiceError

log = logging.getLogger("gym_tracker")

MEDIA_DIR = Path(settings.database_path).resolve().parent / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from db.upgrade import ensure_schema

    configure_db(settings.database_url)
    await run_in_threadpool(ensure_schema)
    await _seed_first_admin()
    log.info("Gym tracker ready (database: %s)", settings.database_path)
    # The MCP session manager only starts if its lifespan is entered here.
    async with mcp_http.lifespan(mcp_http):
        yield


async def _seed_first_admin() -> None:
    """On first start, seed the admin from FIRST_ADMIN_EMAIL and print a
    one-time setup link to the log."""
    import services.users as users_service

    if not settings.first_admin_email:
        return
    try:
        link = await run_in_threadpool(users_service.create_first_admin, settings.first_admin_email)
    except Exception:
        log.exception("Failed to seed first admin")
        return
    if link:
        log.warning(
            "*** First admin setup link (one-time, expires in 7 days): %s%s ***",
            settings.base_url.rstrip("/"),
            link,
        )


app = FastAPI(
    title="Gym Tracker",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
)

# REST API: versioned JSON under /api/v1 (Bearer API key auth).
app.include_router(api_router)


@app.exception_handler(ServiceError)
async def handle_service_error(request: Request, exc: ServiceError):
    return service_error_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "validation", "message": str(exc.errors()[:1])},
    )


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    """Unauthenticated liveness endpoint for uptime checks."""
    return {"status": "ok"}


app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# --- MCP server -------------------------------------------------------------------
# The public endpoint must be exactly /mcp. Two traps: FastMCP's own path
# combines with the mount path (silently doubling it to /mcp/mcp), and
# Starlette's Mount("/mcp") only matches "/mcp/…", not the bare "/mcp". So we
# build the FastMCP app with its internal route at "/" (keeping its full auth
# middleware stack) and expose it through a shim route at exactly /mcp. Its
# lifespan is entered in our own lifespan below.

from starlette.routing import Route  # noqa: E402

from mcp_server.server import mcp  # noqa: E402


class _BareMcpPath:
    """ASGI shim: forward requests at /mcp into the FastMCP app's root."""

    def __init__(self, sub_app):
        self._sub_app = sub_app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope["path"] = "/"
        await self._sub_app(scope, receive, send)


mcp_http = mcp.http_app(path="/")
app.router.routes.append(Route("/mcp", endpoint=_BareMcpPath(mcp_http), name="mcp"))


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    """Web app manifest at the root, so it can be added to the home screen."""
    from fastapi.responses import FileResponse

    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


# --- NiceGUI UI -----------------------------------------------------------------
# Attach NiceGUI to this app. Routes registered above (healthz, media, API,
# MCP) take precedence over NiceGUI's mount.

from nicegui import ui  # noqa: E402
from nicegui import app as nicegui_app  # noqa: E402

from ui.auth import SESSION_MAX_AGE_SECONDS, AuthMiddleware  # noqa: E402

# Added before ui.run_with so it runs after (inside) the session and
# request-tracking middlewares, where app.storage.user is available.
nicegui_app.add_middleware(AuthMiddleware)

ui.run_with(
    app,
    title="Gym Tracker",
    dark=None,  # dark mode follows the phone setting
    storage_secret=settings.storage_secret,
    session_middleware_kwargs={
        "max_age": SESSION_MAX_AGE_SECONDS,
        "same_site": "lax",
        "https_only": settings.base_url.startswith("https"),
    },
    reconnect_timeout=30.0,
    show_welcome_message=False,
    favicon=STATIC_DIR / "icon.svg",
)

import ui.pages  # noqa: E402  (registers all @ui.page routes)


def main() -> None:
    """Run the app with uvicorn (single worker, as NiceGUI requires).

    The app object is passed directly: an import string would re-import this
    module (it may already be running as __main__) and wire NiceGUI twice.
    """
    import uvicorn

    logging.basicConfig(level=settings.log_level.upper())
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        workers=1,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
