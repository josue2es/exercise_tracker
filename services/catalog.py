"""Catalog service: search and fetch exercises from the local database.

The running app never calls an external catalog source; data arrives through
the import CLI (scripts/import_catalog.py).
"""

from sqlalchemy import select

from db.models import Exercise
from db.session import get_session
from services.context import UserContext
from services.errors import NotFoundError
from services.schemas import ExerciseDetail, ExerciseSummary, Page

MAX_LIMIT = 50


def _to_summary(exercise: Exercise) -> ExerciseSummary:
    return ExerciseSummary(
        id=exercise.id,
        source=exercise.source,
        name=exercise.name,
        category=exercise.category,
        primary_muscles=exercise.primary_muscles or [],
        secondary_muscles=exercise.secondary_muscles or [],
        equipment=exercise.equipment or [],
        media=exercise.media or [],
    )


def _to_detail(exercise: Exercise) -> ExerciseDetail:
    return ExerciseDetail(
        **_to_summary(exercise).model_dump(),
        body_parts=exercise.body_parts or [],
        level=exercise.level,
        instructions=exercise.instructions or [],
        attribution=exercise.attribution,
        retired_at=exercise.retired_at,
    )


def search_exercises(
    ctx: UserContext,
    query: str = "",
    muscle: str | None = None,
    equipment: str | None = None,
    source: str | None = None,
    limit: int = 20,
    cursor: int | None = None,
) -> Page:
    """Search the active (non-retired) catalog.

    The free-text query is matched against the lowercase search_text column
    (every token must match). Muscle and equipment filters match membership in
    the stored JSON lists (primary or secondary muscles). The cursor is the
    last exercise id of the previous page.
    """
    limit = max(1, min(limit, MAX_LIMIT))

    with get_session() as session:
        stmt = select(Exercise).where(Exercise.retired_at.is_(None))
        if source:
            stmt = stmt.where(Exercise.source == source)
        rows = session.scalars(stmt.order_by(Exercise.id)).all()

    needle = query.lower().strip()
    if needle:
        tokens = needle.split()
        rows = [r for r in rows if all(t in r.search_text for t in tokens)]

    if muscle:
        m = muscle.lower()
        rows = [r for r in rows if m in (r.primary_muscles or []) or m in (r.secondary_muscles or [])]
    if equipment:
        e = equipment.lower()
        rows = [r for r in rows if e in [x.lower() for x in (r.equipment or [])]]

    if cursor is not None:
        rows = [r for r in rows if r.id > cursor]

    more = len(rows) > limit
    rows = rows[:limit]
    return Page(
        items=[_to_summary(r) for r in rows],
        next_cursor=str(rows[-1].id) if more and rows else None,
        more_available=more,
    )


def get_exercise(ctx: UserContext, exercise_id: int) -> ExerciseDetail:
    """Fetch one exercise by id, including retired ones (history stays readable)."""
    with get_session() as session:
        exercise = session.get(Exercise, exercise_id)
        if exercise is None:
            raise NotFoundError("Exercise not found")
        return _to_detail(exercise)


def filter_options(ctx: UserContext) -> dict[str, list[str]]:
    """Distinct muscle and equipment values plus sources, for picker filters.

    Values are stored as given by each source (phase 2 will normalize them)."""
    muscles: set[str] = set()
    equipment: set[str] = set()
    with get_session() as session:
        rows = session.execute(
            select(Exercise.primary_muscles, Exercise.secondary_muscles, Exercise.equipment).where(
                Exercise.retired_at.is_(None)
            )
        ).all()
        sources = sorted(session.scalars(select(Exercise.source).distinct()).all())
    for primary, secondary, equip in rows:
        muscles.update(m.lower() for m in (primary or []) + (secondary or []))
        equipment.update(e.lower() for e in (equip or []))
    return {
        "muscles": sorted(muscles),
        "equipment": sorted(equipment),
        "sources": sources,
    }
