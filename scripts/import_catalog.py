"""Catalog import CLI: loads the two exercise datasets into the local DB.

Re-runnable and idempotent: upserts on (source, source_id), never deletes, and
retires exercises that disappeared from a source. The running app never calls
any external catalog source.

Usage:
    python -m scripts.import_catalog --source all|free|exercisedb [--no-images] [--db PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, UTC
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import select

from config import settings
from db.models import Exercise, ImportRun
from db.session import configure, get_session

FREE_DB_SOURCE = "free_exercise_db"
# Directory under the media root (served at /media/<dir>/); hyphenated, unlike the source id.
FREE_DB_MEDIA_DIR = "free-exercise-db"
EXERCISEDB_SOURCE = "exercisedb_v1"

FREE_DB_JSON_URL = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json"
FREE_DB_IMAGE_BASE = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/"
FREE_DB_ATTRIBUTION = "free-exercise-db (public domain, Unlicense) - github.com/yuhonas/free-exercise-db"

EXERCISEDB_BASE = "https://oss.exercisedb.dev/api/v1"
EXERCISEDB_ATTRIBUTION = "ExerciseDB (exercisedb.dev) - non-commercial use, attribution required"

STEP_PREFIX_RE = re.compile(r"^\s*Step\s*[:.)]?\s*\d+\s*[:.)]?\s*", re.IGNORECASE)
MAX_STORED_ERRORS = 100


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# --- search_text ---------------------------------------------------------------


def build_search_text(name: str, muscles: list[str], equipment: list[str]) -> str:
    """Lowercase concatenation of name, muscles and equipment for the picker."""
    return " ".join([name.lower(), *[m.lower() for m in muscles], *[e.lower() for e in equipment]])


def title_case(name: str) -> str:
    """Title-case an ExerciseDB name, leaving words that already look styled alone."""
    words = []
    for word in name.split():
        if any(c.isupper() or c.isdigit() for c in word):
            words.append(word)
        else:
            words.append(word.capitalize())
    return " ".join(words)


# --- Free ExerciseDB transforms ------------------------------------------------


def transform_free(raw: dict) -> dict:
    """Map one free-exercise-db record to Exercise columns.

    force, mechanic and equipment can be null; nulls are accepted.
    """
    source_id = raw["id"]
    equipment = [raw["equipment"]] if raw.get("equipment") else []
    primary = raw.get("primaryMuscles") or []
    secondary = raw.get("secondaryMuscles") or []
    media = [
        {"type": "image", "url": f"/media/{FREE_DB_MEDIA_DIR}/{quote(image)}"}
        for image in (raw.get("images") or [])
    ]
    return {
        "source": FREE_DB_SOURCE,
        "source_id": source_id,
        "name": raw["name"],
        "category": raw.get("category"),
        "body_parts": [],
        "primary_muscles": primary,
        "secondary_muscles": secondary,
        "equipment": equipment,
        "level": raw.get("level"),
        "instructions": raw.get("instructions") or [],
        "media": media,
        "attribution": FREE_DB_ATTRIBUTION,
        "search_text": build_search_text(raw["name"], primary + secondary, equipment),
    }


def transform_exercisedb(raw: dict) -> dict:
    """Map one ExerciseDB V1 record to Exercise columns."""
    source_id = raw["exerciseId"]
    name = title_case(raw["name"])
    primary = raw.get("targetMuscles") or []
    secondary = raw.get("secondaryMuscles") or []
    equipment = raw.get("equipments") or []
    instructions = [STEP_PREFIX_RE.sub("", step).strip() for step in (raw.get("instructions") or [])]
    media = [{"type": "gif", "url": raw["gifUrl"]}] if raw.get("gifUrl") else []
    return {
        "source": EXERCISEDB_SOURCE,
        "source_id": source_id,
        "name": name,
        "category": None,
        "body_parts": raw.get("bodyParts") or [],
        "primary_muscles": primary,
        "secondary_muscles": secondary,
        "equipment": equipment,
        "level": None,
        "instructions": instructions,
        "media": media,
        "attribution": EXERCISEDB_ATTRIBUTION,
        "search_text": build_search_text(name, primary + secondary, equipment),
    }


# --- Fetching ------------------------------------------------------------------


def fetch_free_db(client: httpx.Client, errors: list[str]) -> list[dict]:
    """Download the combined free-exercise-db dataset."""
    resp = client.get(FREE_DB_JSON_URL)
    resp.raise_for_status()
    return resp.json()


def fetch_exercisedb(
    client: httpx.Client,
    errors: list[str],
    *,
    limit: int = 25,
    page_cap: int = 100,
    throttle_seconds: float = 0.75,
    max_attempts: int = 6,
    base_delay: float = 2.0,
    sleep=time.sleep,
) -> list[dict]:
    """Fetch all ExerciseDB exercises page by page.

    Handles the documented pagination: ``limit`` silently caps at 25 and the
    cursor parameter is ``after`` (copy ``meta.nextCursor`` until
    ``meta.hasNextPage`` is false). A cursor that doesn't advance means the API
    is silently returning the first page, so we stop. 429 and 5xx are retried
    with exponential backoff; after repeated failures we stop cleanly with the
    pages collected so far.
    """
    items: list[dict] = []
    after: str | None = None
    for page in range(1, page_cap + 1):
        params: dict = {"limit": str(limit)}
        if after:
            params["after"] = after
        try:
            data = _get_with_retry(
                client,
                f"{EXERCISEDB_BASE}/exercises",
                params,
                errors=errors,
                max_attempts=max_attempts,
                base_delay=base_delay,
                sleep=sleep,
            )
        except RuntimeError as exc:
            errors.append(f"stopped at page {page}: {exc}")
            break
        payload = data.json()
        items.extend(payload.get("data") or [])
        meta = payload.get("meta") or {}
        if not meta.get("hasNextPage"):
            break
        next_cursor = meta.get("nextCursor")
        if not next_cursor or next_cursor == after:
            errors.append(f"pagination stopped at page {page}: cursor did not advance")
            break
        after = next_cursor
        sleep(throttle_seconds)
    return items


def _get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict,
    *,
    errors: list[str],
    max_attempts: int,
    base_delay: float,
    sleep,
) -> httpx.Response:
    """GET with retry on 429/5xx/transport errors, exponential backoff."""
    last_status = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = client.get(url, params=params)
        except httpx.TransportError as exc:
            last_status = f"network error ({exc.__class__.__name__})"
            resp = None
        if resp is not None:
            if resp.status_code < 400:
                return resp
            last_status = resp.status_code
            if resp.status_code not in (429, *range(500, 600)):
                raise RuntimeError(f"unexpected status {resp.status_code} from {url}")
        if attempt == max_attempts:
            raise RuntimeError(f"failed after {max_attempts} attempts, last: {last_status}")
        sleep(base_delay * 2 ** (attempt - 1))
    raise RuntimeError("unreachable")  # pragma: no cover


# --- Image downloads -------------------------------------------------------------


def _safe_relative(path_str: str) -> Path | None:
    """Reject relative paths escaping the media directory."""
    path = Path(path_str)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def download_free_db_images(
    client: httpx.Client,
    items: list[dict],
    media_root: Path,
    errors: list[str],
    *,
    workers: int = 8,
) -> int:
    """Download every free-exercise-db image into media_root.

    Existing files are skipped, making re-runs cheap. Returns the number of
    files downloaded this run.
    """
    targets: list[tuple[str, Path]] = []
    for item in items:
        for image in item.get("images") or []:
            rel = _safe_relative(image)
            if rel is None:
                errors.append(f"skipped unsafe image path: {image!r}")
                continue
            dest = media_root / FREE_DB_MEDIA_DIR / rel
            if not dest.exists():
                targets.append((image, dest))

    downloaded = 0

    def download(target: tuple[str, Path]) -> None:
        nonlocal downloaded
        image, dest = target
        url = FREE_DB_IMAGE_BASE + "/".join(quote(part) for part in Path(image).parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, 4):
            try:
                resp = client.get(url)
                if resp.status_code < 400:
                    dest.write_bytes(resp.content)
                    downloaded += 1
                    return
                if resp.status_code not in (429, *range(500, 600)):
                    errors.append(f"image {image}: status {resp.status_code}")
                    return
            except httpx.TransportError as exc:
                if attempt == 3:
                    errors.append(f"image {image}: {exc.__class__.__name__}")
                    return
            time.sleep(1.0 * attempt)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(download, t) for t in targets]
        for _ in as_completed(futures):
            pass
    return downloaded


# --- Upsert -----------------------------------------------------------------------


def _content_tuple(row: dict) -> tuple:
    return (
        row["name"],
        row["category"],
        json.dumps(row["body_parts"]),
        json.dumps(row["primary_muscles"]),
        json.dumps(row["secondary_muscles"]),
        json.dumps(row["equipment"]),
        row["level"],
        json.dumps(row["instructions"]),
        json.dumps(row["media"]),
        row["attribution"],
        row["search_text"],
    )


def upsert_exercises(session, source: str, transformed: list[dict]) -> tuple[int, int, int]:
    """Upsert on (source, source_id). Returns (inserted, updated, retired).

    A re-run with unchanged data updates nothing. Exercises missing from the
    source get retired_at set; returning ones get it cleared.
    """
    existing = {
        ex.source_id: ex
        for ex in session.scalars(select(Exercise).where(Exercise.source == source))
    }
    fetched_ids = set()
    inserted = updated = 0
    for row in transformed:
        fetched_ids.add(row["source_id"])
        current = existing.get(row["source_id"])
        if current is None:
            session.add(Exercise(retired_at=None, **row))
            inserted += 1
            continue
        new_content = _content_tuple(row)
        old_content = (
            current.name,
            current.category,
            json.dumps(current.body_parts or []),
            json.dumps(current.primary_muscles or []),
            json.dumps(current.secondary_muscles or []),
            json.dumps(current.equipment or []),
            current.level,
            json.dumps(current.instructions or []),
            json.dumps(current.media or []),
            current.attribution,
            current.search_text,
        )
        if new_content != old_content:
            for key, value in row.items():
                setattr(current, key, value)
            current.retired_at = None
            current.updated_at = _utcnow()
            updated += 1
        elif current.retired_at is not None:
            current.retired_at = None
            current.updated_at = _utcnow()
            updated += 1

    retired = 0
    for source_id, ex in existing.items():
        if source_id not in fetched_ids and ex.retired_at is None:
            ex.retired_at = _utcnow()
            retired += 1
    return inserted, updated, retired


# --- Runner ------------------------------------------------------------------------


def import_source(
    source: str,
    *,
    database_url: str | None = None,
    media_root: Path | None = None,
    no_images: bool = False,
    fetch_sleep=time.sleep,
    errors: list[str] | None = None,
) -> dict:
    """Fetch and import one source. Returns a summary dict."""
    errors = errors if errors is not None else []
    run = ImportRun(source=source)
    started = _utcnow()
    inserted = updated = retired = 0
    images_downloaded = 0

    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": "gym-tracker/0.1"}) as client:
        if source == FREE_DB_SOURCE:
            raw_items = fetch_free_db(client, errors)
            transformed = [transform_free(r) for r in raw_items]
            if not no_images:
                images_downloaded = download_free_db_images(client, raw_items, media_root, errors)
        else:
            raw_items = fetch_exercisedb(client, errors, sleep=fetch_sleep)
            transformed = [transform_exercisedb(r) for r in raw_items]

    with get_session() as session:
        inserted, updated, retired = upsert_exercises(session, source, transformed)
        run.inserted, run.updated, run.retired = inserted, updated, retired
        run.errors = errors[:MAX_STORED_ERRORS]
        run.started_at = started
        run.finished_at = _utcnow()
        session.add(run)

    return {
        "source": source,
        "fetched": len(transformed),
        "inserted": inserted,
        "updated": updated,
        "retired": retired,
        "images_downloaded": images_downloaded,
        "errors": errors,
    }


def ensure_schema() -> None:
    """Apply Alembic migrations if the database has no tables yet."""
    from db.upgrade import ensure_schema as _ensure

    _ensure()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import exercise catalog datasets")
    parser.add_argument("--source", required=True, choices=["all", "free", "exercisedb"])
    parser.add_argument("--db", default=None, help="Database path override (default: settings.database_path)")
    parser.add_argument("--no-images", action="store_true", help="Skip downloading free-exercise-db images")
    args = parser.parse_args(argv)

    database_url = f"sqlite:///{Path(args.db).resolve()}" if args.db else settings.database_url
    media_root = Path(settings.database_path).resolve().parent / "media"
    configure(database_url)
    ensure_schema()

    sources = (
        [FREE_DB_SOURCE, EXERCISEDB_SOURCE]
        if args.source == "all"
        else [FREE_DB_SOURCE if args.source == "free" else EXERCISEDB_SOURCE]
    )

    exit_code = 0
    for source in sources:
        print(f"Importing {source} ...", flush=True)
        summary = import_source(source, media_root=media_root, no_images=args.no_images)
        print(
            f"  fetched={summary['fetched']} inserted={summary['inserted']} "
            f"updated={summary['updated']} retired={summary['retired']} "
            f"images_downloaded={summary['images_downloaded']}"
        )
        for error in summary["errors"][:10]:
            print(f"  error: {error}", file=sys.stderr)
        if summary["errors"]:
            print(f"  ({len(summary['errors'])} errors in total)", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
