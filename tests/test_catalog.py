"""Tests for the catalog service (search filters, pagination, detail)."""

from datetime import datetime

import pytest
from sqlalchemy import select

from services.catalog import get_exercise, search_exercises
from services.context import UserContext
from services.errors import NotFoundError
from tests.conftest import make_exercise


def _ctx(user_id=1):
    return UserContext(user_id=user_id)


def test_search_matches_name_tokens(engine, user_id):
    make_exercise(name="Barbell Bench Press")
    make_exercise(name="Bulgarian Split Squat", source="exercisedb_v1", source_id="0002")
    page = search_exercises(_ctx(user_id), query="bench")
    assert [i.name for i in page.items] == ["Barbell Bench Press"]


def test_search_all_tokens_must_match(engine, user_id):
    make_exercise(name="Barbell Bench Press")
    page = search_exercises(_ctx(user_id), query="bench squat")
    assert page.items == []


def test_search_filters_muscle_and_equipment(engine, user_id):
    from db.models import Exercise
    from db.session import get_session

    make_exercise(name="Bench Press")  # pectorals / barbell (from make_exercise)
    make_exercise(name="Squat", source="free_exercise_db", source_id="Squat")
    with get_session() as session:
        squat = session.scalars(
            select(Exercise).where(Exercise.source_id == "Squat")
        ).one()
        squat.primary_muscles = ["quadriceps"]
        squat.equipment = ["body only"]
        squat.search_text = "squat quadriceps body only"

    page = search_exercises(_ctx(user_id), muscle="pectorals")
    assert [i.name for i in page.items] == ["Bench Press"]
    page = search_exercises(_ctx(user_id), equipment="barbell")
    assert [i.name for i in page.items] == ["Bench Press"]
    page = search_exercises(_ctx(user_id), muscle="quadriceps")
    assert [i.name for i in page.items] == ["Squat"]


def test_search_filters_source(engine, user_id):
    make_exercise(name="Bench Press")
    make_exercise(name="Bench Press V2", source="exercisedb_v1", source_id="bp2")
    page = search_exercises(_ctx(user_id), source="exercisedb_v1")
    assert [i.name for i in page.items] == ["Bench Press V2"]


def test_search_pagination(engine, user_id):
    for i in range(5):
        make_exercise(name=f"Exercise {i}", source_id=f"ex{i}")
    page = search_exercises(_ctx(user_id), limit=2)
    assert len(page.items) == 2
    assert page.more_available and page.next_cursor
    page2 = search_exercises(_ctx(user_id), limit=2, cursor=int(page.next_cursor))
    assert len(page2.items) == 2
    assert page2.items[0].id > page.items[-1].id


def test_get_exercise_includes_retired(engine, user_id):

    from db.models import Exercise
    from db.session import get_session

    make_exercise(name="Retired Move")
    with get_session() as session:
        exercise = session.scalars(select(Exercise)).first()
        exercise.retired_at = datetime.now()
    detail = get_exercise(_ctx(user_id), exercise.id)
    assert detail.retired_at is not None
    # Retired exercises stay out of search results.
    page = search_exercises(_ctx(user_id), query="retired")
    assert page.items == []


def test_get_exercise_missing_raises_404(engine, user_id):
    with pytest.raises(NotFoundError):
        get_exercise(_ctx(user_id), 99999)
