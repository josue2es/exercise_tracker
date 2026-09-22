"""Composition root.

One uvicorn process (single worker) hosts the NiceGUI UI, the REST API and the
MCP server as thin adapters over the service layer. This module wires them
together and owns the FastAPI lifespan.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from config import settings
from db.session import configure as configure_db

log = logging.getLogger("gym_tracker")

MEDIA_DIR = Path(settings.database_path).resolve().parent / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from db.upgrade import ensure_schema

    configure_db(settings.database_url)
    await run_in_threadpool(ensure_schema)
    await _seed_first_admin()
    log.info("Gym tracker ready (database: %s)", settings.database_path)
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


app = FastAPI(title="Gym Tracker", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    """Unauthenticated liveness endpoint for uptime checks."""
    return {"status": "ok"}


app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=Path("static")), name="static")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    """Web app manifest at the root, so it can be added to the home screen."""
    from fastapi.responses import FileResponse

    return FileResponse("static/manifest.webmanifest", media_type="application/manifest+json")


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
    favicon="/static/icon.svg",
)

import ui.pages  # noqa: E402,F401  (registers all @ui.page routes)


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
