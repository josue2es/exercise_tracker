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

from config import settings
from db.session import configure as configure_db

log = logging.getLogger("gym_tracker")

MEDIA_DIR = Path(settings.database_path).resolve().parent / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_db(settings.database_url)
    log.info("Gym tracker ready (database: %s)", settings.database_path)
    yield


app = FastAPI(title="Gym Tracker", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    """Unauthenticated liveness endpoint for uptime checks."""
    return {"status": "ok"}


app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


def main() -> None:
    """Run the app with uvicorn (single worker, as NiceGUI requires)."""
    import uvicorn

    logging.basicConfig(level=settings.log_level.upper())
    uvicorn.run(
        "app:app",
        host=settings.bind_host,
        port=settings.bind_port,
        workers=1,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
