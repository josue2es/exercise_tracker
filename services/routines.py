"""Routines service: a routine ("Volumen") groups ordered days, and each day is
a workout ("Día 1: Pecho") with its own exercises (services/workouts.py).

Days are created with workouts.create_workout(..., routine_id=...) and deleted
with workouts.delete_workout. Like workouts, deleting a routine never touches
logged sessions or sets.
"""

from __future__ import annotations

from sqlalchemy import select

from db.models import Routine, Workout, utcnow
from db.session import get_session
from services.audit import record_audit
from services.context import UserContext
from services.errors import NotFoundError, ScopeError, ValidationError
from services.schemas import RoutineSummary, utc as utc_
from services.workouts import (
    get_owned_routine,
    exercise_count_map,
    last_performed_map,
    summary,
)


def _audit(session, ctx: UserContext, action: str, target: str | None = None) -> None:
    """Audit writes that arrive through REST or MCP."""
    if ctx.actor in ("api", "mcp"):
        record_audit(session, ctx, action, target)


def _days(session, routine_ids: list[int]) -> dict[int, list[Workout]]:
    by_routine: dict[int, list[Workout]] = {rid: [] for rid in routine_ids}
    if not routine_ids:
        return by_routine
    for w in session.scalars(
        select(Workout)
        .where(Workout.routine_id.in_(routine_ids), Workout.deleted_at.is_(None))
        .order_by(Workout.position, Workout.id)
    ):
        by_routine[w.routine_id].append(w)
    return by_routine


def _summaries(session, ctx: UserContext, routines: list[Routine]) -> list[RoutineSummary]:
    days = _days(session, [r.id for r in routines])
    workout_ids = [w.id for ws in days.values() for w in ws]
    counts = exercise_count_map(session, workout_ids)
    last = last_performed_map(session, ctx.user_id, workout_ids)
    result = []
    for r in routines:
        day_items = [summary(w, counts.get(w.id, 0), last.get(w.id)) for w in days[r.id]]
        performed = [d.last_performed_at for d in day_items if d.last_performed_at]
        result.append(
            RoutineSummary(
                id=r.id,
                name=r.name,
                created_at=utc_(r.created_at),
                updated_at=utc_(r.updated_at),
                last_performed_at=max(performed) if performed else None,
                days=day_items,
            )
        )
    return result


# --- read operations -----------------------------------------------------------------


def list_routines(ctx: UserContext) -> list[RoutineSummary]:
    """The caller's routines with their days, most recently updated first."""
    with get_session() as session:
        routines = session.scalars(
            select(Routine)
            .where(Routine.user_id == ctx.user_id, Routine.deleted_at.is_(None))
            .order_by(Routine.updated_at.desc())
        ).all()
        return _summaries(session, ctx, list(routines))


def get_routine(ctx: UserContext, routine_id: int) -> RoutineSummary:
    with get_session() as session:
        routine = get_owned_routine(session, ctx, routine_id)
        return _summaries(session, ctx, [routine])[0]


def next_day_id(routine: RoutineSummary) -> int | None:
    """The day that follows the most recently trained one (wrapping around);
    the first day if none has been trained yet."""
    if not routine.days:
        return None
    trained = [(d.last_performed_at, i) for i, d in enumerate(routine.days) if d.last_performed_at]
    if not trained:
        return routine.days[0].id
    _, index = max(trained)
    return routine.days[(index + 1) % len(routine.days)].id


# --- write operations ------------------------------------------------------------------


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValidationError("Routine name is required")
    return name


def create_routine(ctx: UserContext, name: str) -> RoutineSummary:
    """Create an empty routine; add days with workouts.create_workout."""
    ctx.require_write()
    name = _clean_name(name)
    with get_session() as session:
        routine = Routine(user_id=ctx.user_id, name=name)
        session.add(routine)
        session.flush()
        _audit(session, ctx, "routine.create", target=routine.name)
        return _summaries(session, ctx, [routine])[0]


def rename_routine(ctx: UserContext, routine_id: int, name: str) -> RoutineSummary:
    ctx.require_write()
    name = _clean_name(name)
    with get_session() as session:
        routine = get_owned_routine(session, ctx, routine_id)
        routine.name = name
        routine.updated_at = utcnow()
        _audit(session, ctx, "routine.update", target=routine.name)
        return _summaries(session, ctx, [routine])[0]


def move_day(ctx: UserContext, workout_id: int, delta: int) -> RoutineSummary:
    """Move a day up (delta=-1) or down (delta=+1) within its routine."""
    ctx.require_write()
    with get_session() as session:
        workout = session.scalars(
            select(Workout).where(
                Workout.id == workout_id,
                Workout.user_id == ctx.user_id,
                Workout.deleted_at.is_(None),
            )
        ).one_or_none()
        if workout is None:
            raise NotFoundError("Workout not found")
        routine = get_owned_routine(session, ctx, workout.routine_id)
        days = _days(session, [routine.id])[routine.id]
        index = days.index(workout)
        target = index + delta
        if 0 <= target < len(days):
            days[index], days[target] = days[target], days[index]
        for position, day in enumerate(days):  # also closes gaps left by deletions
            day.position = position
        routine.updated_at = utcnow()
        session.flush()
        return _summaries(session, ctx, [routine])[0]


def delete_routine(ctx: UserContext, routine_id: int) -> None:
    """Soft delete the routine and its days; sessions and sets are kept.

    UI only, like workouts.delete_workout.
    """
    if ctx.actor != "ui":
        raise ScopeError("Routines can only be deleted in the app")
    with get_session() as session:
        routine = get_owned_routine(session, ctx, routine_id)
        now = utcnow()
        routine.deleted_at = now
        for day in _days(session, [routine.id])[routine.id]:
            day.deleted_at = now
