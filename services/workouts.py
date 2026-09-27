"""Workouts service: plans (workouts) with ordered exercises and targets.

Plans are separate from logs: deleting a workout never touches sessions or
logged sets; removing an exercise from a workout never touches logged sets.
"""

from __future__ import annotations

from sqlalchemy import func, select

from db.models import Exercise, TrainingSession, Workout, WorkoutExercise, utcnow
from db.session import get_session
from services.audit import record_audit
from services.context import UserContext
from services.errors import NotFoundError, ScopeError, ValidationError
from services.schemas import WorkoutDetail, WorkoutExerciseItem, WorkoutSummary, utc as utc_


def _get_owned_workout(session, ctx: UserContext, workout_id: int) -> Workout:
    """Fetch a workout scoped to the caller; foreign or missing IDs look the same."""
    workout = session.scalars(
        select(Workout).where(
            Workout.id == workout_id,
            Workout.user_id == ctx.user_id,
            Workout.deleted_at.is_(None),
        )
    ).one_or_none()
    if workout is None:
        raise NotFoundError("Workout not found")
    return workout


def _validate_targets(sets: int, reps_min: int, reps_max: int) -> None:
    if not (1 <= sets <= 20):
        raise ValidationError("Target sets must be between 1 and 20")
    if not (1 <= reps_min <= reps_max <= 100):
        raise ValidationError("Rep range must satisfy 1 <= min <= max <= 100")


def _audit(session, ctx: UserContext, action: str, target: str | None = None) -> None:
    """Audit writes that arrive through REST or MCP."""
    if ctx.actor in ("api", "mcp"):
        record_audit(session, ctx, action, target)


def _exercise_count_map(session, workout_ids: list[int]) -> dict[int, int]:
    if not workout_ids:
        return {}
    rows = session.execute(
        select(WorkoutExercise.workout_id, func.count())
        .where(WorkoutExercise.workout_id.in_(workout_ids))
        .group_by(WorkoutExercise.workout_id)
    ).all()
    return dict(rows)


def _last_performed_map(session, user_id: int, workout_ids: list[int]) -> dict[int, object]:
    if not workout_ids:
        return {}
    rows = session.execute(
        select(TrainingSession.workout_id, func.max(TrainingSession.started_at))
        .where(
            TrainingSession.user_id == user_id,
            TrainingSession.workout_id.in_(workout_ids),
        )
        .group_by(TrainingSession.workout_id)
    ).all()
    return dict(rows)


def _detail(session, workout: Workout) -> WorkoutDetail:
    entries = sorted(workout.exercises, key=lambda we: we.position)
    items = [
        WorkoutExerciseItem(
            exercise_id=we.exercise_id,
            exercise_name=we.exercise.name,
            exercise_name_es=we.exercise.name_es,
            position=we.position,
            target_sets=we.target_sets,
            target_reps_min=we.target_reps_min,
            target_reps_max=we.target_reps_max,
            comment=we.comment,
        )
        for we in entries
    ]
    last = (
        session.execute(
            select(func.max(TrainingSession.started_at)).where(
                TrainingSession.user_id == workout.user_id,
                TrainingSession.workout_id == workout.id,
            )
        ).scalar_one_or_none()
    )
    return WorkoutDetail(
        id=workout.id,
        name=workout.name,
        notes=workout.notes,
        exercise_count=len(items),
        created_at=utc_(workout.created_at),
        updated_at=utc_(workout.updated_at),
        last_performed_at=utc_(last),
        exercises=items,
    )


# --- read operations -----------------------------------------------------------------


def list_workouts(ctx: UserContext) -> list[WorkoutSummary]:
    """List the caller's non-deleted workouts, most recently updated first."""
    with get_session() as session:
        workouts = session.scalars(
            select(Workout)
            .where(Workout.user_id == ctx.user_id, Workout.deleted_at.is_(None))
            .order_by(Workout.updated_at.desc())
        ).unique().all()
        ids = [w.id for w in workouts]
        counts = _exercise_count_map(session, ids)
        last = _last_performed_map(session, ctx.user_id, ids)
        return [
            WorkoutSummary(
                id=w.id,
                name=w.name,
                notes=w.notes,
                exercise_count=counts.get(w.id, 0),
                created_at=utc_(w.created_at),
                updated_at=utc_(w.updated_at),
                last_performed_at=utc_(last.get(w.id)),
            )
            for w in workouts
        ]


def get_workout(ctx: UserContext, workout_id: int) -> WorkoutDetail:
    """Fetch one workout with its ordered exercises."""
    with get_session() as session:
        workout = _get_owned_workout(session, ctx, workout_id)
        return _detail(session, workout)


# --- write operations ------------------------------------------------------------------


def create_workout(ctx: UserContext, name: str, notes: str | None = None) -> WorkoutDetail:
    """Create an empty workout. Exercises are set with set_workout_exercises."""
    ctx.require_write()
    name = name.strip()
    if not name:
        raise ValidationError("Workout name is required")
    with get_session() as session:
        workout = Workout(user_id=ctx.user_id, name=name, notes=notes or None)
        session.add(workout)
        session.flush()
        _audit(session, ctx, "workout.create", target=workout.name)
        return _detail(session, workout)


def update_workout(
    ctx: UserContext,
    workout_id: int,
    name: str | None = None,
    notes: str | None = None,
) -> WorkoutDetail:
    """Rename and/or update notes."""
    ctx.require_write()
    with get_session() as session:
        workout = _get_owned_workout(session, ctx, workout_id)
        if name is not None:
            name = name.strip()
            if not name:
                raise ValidationError("Workout name is required")
            workout.name = name
        if notes is not None:
            workout.notes = notes or None
        workout.updated_at = utcnow()
        _audit(session, ctx, "workout.update", target=workout.name)
        return _detail(session, workout)


def set_workout_exercises(
    ctx: UserContext,
    workout_id: int,
    exercises: list[WorkoutExerciseItem],
) -> WorkoutDetail:
    """Replace the ordered exercise list: exercise, sets, rep range, comment.

    A fixed rep target is min = max. Removing an exercise never touches
    logged sets.
    """
    ctx.require_write()
    seen: set[int] = set()
    for item in exercises:
        if item.exercise_id in seen:
            raise ValidationError("The same exercise appears twice in this workout")
        seen.add(item.exercise_id)
        _validate_targets(item.target_sets, item.target_reps_min, item.target_reps_max)

    with get_session() as session:
        workout = _get_owned_workout(session, ctx, workout_id)
        exercise_ids = [item.exercise_id for item in exercises]
        if exercise_ids:
            found = session.scalars(
                select(Exercise.id).where(Exercise.id.in_(exercise_ids))
            ).all()
            missing = set(exercise_ids) - set(found)
            if missing:
                raise ValidationError(f"Unknown exercise id: {sorted(missing)[0]}")

        # Replace: delete old rows, insert new ones with fresh positions.
        # (Flush so the DELETEs hit the DB before the INSERTs; otherwise the
        # unique (workout_id, exercise_id) constraint fires.)
        for old in session.scalars(
            select(WorkoutExercise).where(WorkoutExercise.workout_id == workout_id)
        ):
            session.delete(old)
        session.flush()
        for position, item in enumerate(exercises):
            session.add(
                WorkoutExercise(
                    workout_id=workout_id,
                    exercise_id=item.exercise_id,
                    position=position,
                    target_sets=item.target_sets,
                    target_reps_min=item.target_reps_min,
                    target_reps_max=item.target_reps_max,
                    comment=item.comment or None,
                )
            )
        workout.updated_at = utcnow()
        session.flush()
        _audit(session, ctx, "workout.set_exercises", target=workout.name)
        return _detail(session, workout)


def delete_workout(ctx: UserContext, workout_id: int) -> None:
    """Soft delete; the caller's sessions keep workout_name and all sets.

    UI only: agents have no delete operations in phase 1.
    """
    if ctx.actor != "ui":
        raise ScopeError("Workouts can only be deleted in the app")
    with get_session() as session:
        workout = _get_owned_workout(session, ctx, workout_id)
        workout.deleted_at = utcnow()
        _audit(session, ctx, "workout.delete", target=workout.name)
