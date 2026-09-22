"""Tests for the catalog import CLI.

Covers: transforms (null fields, Step:N prefixes, title-casing), idempotent
re-runs, retiring missing exercises, un-retiring returning ones, and the
ExerciseDB pagination stop conditions (cursor not advancing, page cap,
repeated 429s).
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

import scripts.import_catalog as import_catalog
from db import models
from db.session import get_session
from tests.conftest import make_user  # noqa: F401  (ensures db wiring helpers exist)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def free_items():
    return json.loads((FIXTURES / "free_exercise_db.json").read_text())["items"]


@pytest.fixture
def exercisedb_pages():
    return json.loads((FIXTURES / "exercisedb_v1.json").read_text())["pages"]


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class FakeClient:
    """Returns queued FakeResponse objects, recording requested URLs."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def get_exercise_by_source_id(session, source, source_id):
    return session.scalars(
        select(models.Exercise).where(
            models.Exercise.source == source,
            models.Exercise.source_id == source_id,
        )
    ).one()


# --- transforms -----------------------------------------------------------------


def test_transform_free_with_nulls(free_items):
    row = import_catalog.transform_free(free_items[1])  # Pull-up: equipment is null
    assert row["equipment"] == []
    assert row["primary_muscles"] == ["lats"]
    assert row["media"] == [{"type": "image", "url": "/media/free-exercise-db/Pull-up/0.jpg"}]
    assert row["attribution"] == import_catalog.FREE_DB_ATTRIBUTION
    assert "pull-up" in row["search_text"]
    assert "lats" in row["search_text"]


def test_transform_exercisedb_strips_step_prefixes_and_title_cases(exercisedb_pages):
    row = import_catalog.transform_exercisedb(exercisedb_pages[0]["data"][0])
    assert row["name"] == "Barbell Bench Press"
    assert row["instructions"] == ["Lie flat on a bench.", "Lower the barbell."]
    assert row["primary_muscles"] == ["pectorals"]
    assert row["body_parts"] == ["Chest"]
    assert row["media"] == [{"type": "gif", "url": "https://static.exercisedb.dev/media/0001.gif"}]


def test_transform_exercisedb_keeps_styled_words(exercisedb_pages):
    row = import_catalog.transform_exercisedb(exercisedb_pages[0]["data"][1])
    assert row["name"] == "EZ-bar Curl"  # "EZ-bar" already contains uppercase, kept as-is


# --- upsert -----------------------------------------------------------------------


def test_upsert_insert_then_idempotent(engine, free_items):
    with get_session() as session:
        inserted, updated, retired = import_catalog.upsert_exercises(
            session, import_catalog.FREE_DB_SOURCE,
            [import_catalog.transform_free(r) for r in free_items],
        )
    assert (inserted, updated, retired) == (3, 0, 0)

    # Re-run with identical data: nothing changes.
    with get_session() as session:
        inserted, updated, retired = import_catalog.upsert_exercises(
            session, import_catalog.FREE_DB_SOURCE,
            [import_catalog.transform_free(r) for r in free_items],
        )
    assert (inserted, updated, retired) == (0, 0, 0)


def test_upsert_retires_missing_and_unretires_returning(engine, free_items):
    with get_session() as session:
        import_catalog.upsert_exercises(
            session, import_catalog.FREE_DB_SOURCE,
            [import_catalog.transform_free(r) for r in free_items],
        )

    # Second run: the exercise "Plank" disappeared from the source.
    remaining = [r for r in free_items if r["id"] != "Plank"]
    with get_session() as session:
        inserted, updated, retired = import_catalog.upsert_exercises(
            session, import_catalog.FREE_DB_SOURCE,
            [import_catalog.transform_free(r) for r in remaining],
        )
    assert (inserted, updated, retired) == (0, 0, 1)

    with get_session() as session:
        plank = get_exercise_by_source_id(session, import_catalog.FREE_DB_SOURCE, "Plank")
    assert plank.retired_at is not None

    # Third run: "Plank" came back; it must be un-retired (counted as updated).
    with get_session() as session:
        inserted, updated, retired = import_catalog.upsert_exercises(
            session, import_catalog.FREE_DB_SOURCE,
            [import_catalog.transform_free(r) for r in free_items],
        )
    assert (inserted, updated, retired) == (0, 1, 0)

    with get_session() as session:
        plank = get_exercise_by_source_id(session, import_catalog.FREE_DB_SOURCE, "Plank")
    assert plank.retired_at is None


def test_upsert_updates_changed_content(engine, free_items):
    with get_session() as session:
        import_catalog.upsert_exercises(
            session, import_catalog.FREE_DB_SOURCE,
            [import_catalog.transform_free(r) for r in free_items],
        )
    changed = [dict(r) for r in free_items]
    changed[0]["primaryMuscles"] = ["pectorals", "front delts"]
    with get_session() as session:
        inserted, updated, retired = import_catalog.upsert_exercises(
            session, import_catalog.FREE_DB_SOURCE,
            [import_catalog.transform_free(r) for r in changed],
        )
    assert (inserted, updated, retired) == (0, 1, 0)
    with get_session() as session:
        bench = get_exercise_by_source_id(session, import_catalog.FREE_DB_SOURCE, "Barbell_Bench_Press")
    assert bench.primary_muscles == ["pectorals", "front delts"]


def test_upsert_never_deletes_rows(engine, free_items):
    with get_session() as session:
        import_catalog.upsert_exercises(
            session, import_catalog.FREE_DB_SOURCE,
            [import_catalog.transform_free(r) for r in free_items],
        )
        import_catalog.upsert_exercises(session, import_catalog.FREE_DB_SOURCE, [])
        assert session.scalars(select(models.Exercise)).unique().all().__len__() == 3


# --- ExerciseDB pagination ---------------------------------------------------------


def make_exercisedb_client(pages):
    return FakeClient([FakeResponse(p) for p in pages])


def test_fetch_exercisedb_follows_cursor(exercisedb_pages):
    client = make_exercisedb_client(exercisedb_pages)
    errors: list[str] = []
    items = import_catalog.fetch_exercisedb(client, errors, sleep=lambda s: None)
    assert len(items) == 3
    assert errors == []
    # Page 1 without cursor, page 2 with after=0002.
    assert client.calls[0][1] == {"limit": "25"}
    assert client.calls[1][1] == {"limit": "25", "after": "0002"}


def test_fetch_exercisedb_stops_when_cursor_does_not_advance(exercisedb_pages):
    # hasNextPage stays true but nextCursor never changes: the API would loop
    # forever returning the same page, so we stop after the second attempt.
    loop_page = dict(exercisedb_pages[0])
    client = FakeClient([
        FakeResponse(loop_page),
        FakeResponse(dict(loop_page, meta=dict(loop_page["meta"], nextCursor="0002"))),
    ])
    errors: list[str] = []
    items = import_catalog.fetch_exercisedb(client, errors, sleep=lambda s: None)
    assert len(items) == 4  # two identical pages collected, then stop
    assert len(client.calls) == 2
    assert any("did not advance" in e for e in errors)


def test_fetch_exercisedb_stops_at_page_cap(exercisedb_pages):
    # Distinct cursors per page so pagination would run forever without a cap.
    def page(i):
        return dict(
            exercisedb_pages[0],
            meta=dict(exercisedb_pages[0]["meta"], nextCursor=f"cursor-{i}"),
        )

    client = FakeClient([FakeResponse(page(i)) for i in range(50)])
    errors: list[str] = []
    items = import_catalog.fetch_exercisedb(client, errors, page_cap=5, sleep=lambda s: None)
    assert len(client.calls) == 5
    assert len(items) == 10


def test_fetch_exercisedb_retries_429_then_stops_cleanly(exercisedb_pages):
    responses = [FakeResponse({}, status_code=429)] * 6  # every attempt fails
    client = FakeClient(responses)
    errors: list[str] = []
    sleeps: list[float] = []
    items = import_catalog.fetch_exercisedb(
        client, errors, max_attempts=3, base_delay=1.0, sleep=sleeps.append
    )
    assert items == []
    assert any("failed after 3 attempts" in e for e in errors)
    assert len(client.calls) == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff between attempts


def test_fetch_exercisedb_recovers_after_429(exercisedb_pages):
    client = FakeClient([
        FakeResponse({}, status_code=429),
        FakeResponse({}, status_code=503),
        FakeResponse(exercisedb_pages[0]),
        FakeResponse(exercisedb_pages[1]),
    ])
    errors: list[str] = []
    items = import_catalog.fetch_exercisedb(
        client, errors, max_attempts=3, base_delay=1.0, sleep=lambda s: None
    )
    assert len(items) == 3
    assert errors == []


def test_fetch_exercisedb_does_not_retry_client_errors(exercisedb_pages):
    client = FakeClient([FakeResponse({}, status_code=404)])
    errors: list[str] = []
    items = import_catalog.fetch_exercisedb(client, errors, sleep=lambda s: None)
    assert items == []
    assert any("unexpected status 404" in e for e in errors)


# --- import_runs bookkeeping ---------------------------------------------------------


def test_import_records_run(engine, monkeypatch, exercisedb_pages):
    client = make_exercisedb_client(exercisedb_pages)
    monkeypatch.setattr(import_catalog.httpx, "Client", lambda **kwargs: client)
    summary = import_catalog.import_source(
        import_catalog.EXERCISEDB_SOURCE, fetch_sleep=lambda s: None
    )
    assert summary["inserted"] == 3
    with get_session() as session:
        runs = session.scalars(select(models.ImportRun)).all()
        assert len(runs) == 1
        run = runs[0]
        assert run.source == import_catalog.EXERCISEDB_SOURCE
        assert run.inserted == 3
        assert run.finished_at is not None
