"""Stats service: last performance, exercise history, training summaries.

Weight is stored as entered (kg or lb); summaries convert to kg only when
reading (1 lb = 0.45359237 kg), displayed to one decimal.
"""

from __future__ import annotations

from sqlalchemy import select

from db.models import Exercise, SetLog, TrainingSession
from db.session import get_session
from services.context import UserContext
from services.errors import NotFoundError
from services.schemas import (
    BestSet,
    ExerciseHistorySession,
    LastPerformance,
    MuscleVolume,
    SetLogItem,
    TrainingSummary,
    Weight,
    utc as utc_,
)

LB_TO_KG = 0.45359237


def _weight_kg(log: SetLog) -> float | None:
    if log.weight_value is None:
        return None
    return log.weight_value if log.weight_unit == "kg" else log.weight_value * LB_TO_KG


def _to_item(log: SetLog) -> SetLogItem:
    return SetLogItem(
        id=log.id,
        session_id=log.session_id,
        exercise_id=log.exercise_id,
        exercise_name=log.exercise.name,
        set_number=log.set_number,
        reps=log.reps,
        weight=Weight(value=log.weight_value, unit=log.weight_unit)
        if log.weight_value is not None
        else None,
        logged_at=utc_(log.logged_at),
    )


def _sets_for(session, exercise_id: int, session_row: TrainingSession) -> list[SetLog]:
    return [
        log
        for log in session.scalars(
            select(SetLog)
            .where(SetLog.session_id == session_row.id, SetLog.exercise_id == exercise_id)
            .order_by(SetLog.set_number)
        ).unique().all()
    ]


def get_last_performance(
    ctx: UserContext,
    exercise_id: int,
    exclude_session_id: int | None = None,
) -> LastPerformance | None:
    """All sets of an exercise from the user's most recent session containing
    it (excluding the given session, usually the one in progress), plus that
    session's date. Looks across all workouts, not just the current one."""
    with get_session() as session:
        stmt = (
            select(TrainingSession)
            .join(SetLog, SetLog.session_id == TrainingSession.id)
            .where(
                TrainingSession.user_id == ctx.user_id,
                SetLog.exercise_id == exercise_id,
            )
        )
        if exclude_session_id is not None:
            stmt = stmt.where(TrainingSession.id != exclude_session_id)
        session_row = session.scalars(
            stmt.order_by(TrainingSession.started_at.desc(), TrainingSession.id.desc()).limit(1)
        ).unique().one_or_none()
        if session_row is None:
            return None
        sets = _sets_for(session, exercise_id, session_row)
        return LastPerformance(
            session_date=utc_(session_row.started_at),
            sets=[_to_item(log) for log in sets],
        )


def get_exercise_history(
    ctx: UserContext,
    exercise_id: int,
    limit: int = 10,
) -> list[ExerciseHistorySession]:
    """Past sessions containing this exercise, newest first."""
    with get_session() as session:
        if session.get(Exercise, exercise_id) is None:
            raise NotFoundError("Exercise not found")
        rows = (
            session.scalars(
                select(TrainingSession)
                .join(SetLog, SetLog.session_id == TrainingSession.id)
                .where(
                    TrainingSession.user_id == ctx.user_id,
                    SetLog.exercise_id == exercise_id,
                )
                .order_by(TrainingSession.started_at.desc(), TrainingSession.id.desc())
                .limit(max(1, min(limit, 100)))
            )
            .unique()
            .all()
        )
        return [
            ExerciseHistorySession(
                session_id=row.id,
                workout_name=row.workout_name,
                date=utc_(row.started_at),
                sets=[_to_item(log) for log in _sets_for(session, exercise_id, row)],
            )
            for row in rows
        ]


def get_training_summary(ctx: UserContext, date_from, date_to) -> TrainingSummary:
    """Summary per period: session count, total sets, volume (weight x reps in
    kg) per primary muscle, best set per exercise (heaviest, then most reps).

    Bodyweight sets count toward totals but add no volume."""
    with get_session() as session:
        sessions = (
            session.scalars(
                select(TrainingSession)
                .where(
                    TrainingSession.user_id == ctx.user_id,
                    TrainingSession.started_at >= date_from,
                    TrainingSession.started_at < date_to,
                )
                .order_by(TrainingSession.started_at.desc())
            )
            .unique()
            .all()
        )
        session_ids = [s.id for s in sessions]
        logs = (
            session.scalars(
                select(SetLog)
                .where(SetLog.session_id.in_(session_ids) if session_ids else False)
                .order_by(SetLog.logged_at)
            )
            .unique()
            .all()
            if session_ids
            else []
        )

        volume_by_muscle: dict[str, float] = {}
        best: dict[int, tuple[float, int, SetLog]] = {}  # exercise_id -> (kg, reps, log)
        session_dates = {s.id: s.started_at for s in sessions}

        for log in logs:
            kg = _weight_kg(log)
            volume = (kg or 0) * log.reps
            for muscle in log.exercise.primary_muscles or []:
                volume_by_muscle[muscle] = volume_by_muscle.get(muscle, 0.0) + volume
            if kg is not None:
                key = (kg, log.reps)
                current = best.get(log.exercise_id)
                if current is None or key > (current[0], current[1]):
                    best[log.exercise_id] = (kg, log.reps, log)

        best_sets = [
            BestSet(
                exercise_id=exercise_id,
                exercise_name=log.exercise.name,
                weight=Weight(value=log.weight_value, unit=log.weight_unit)
                if log.weight_value is not None
                else None,
                reps=reps,
                date=utc_(session_dates.get(log.session_id)),
            )
            for exercise_id, (kg, reps, log) in best.items()
        ]
        best_sets.sort(key=lambda b: b.exercise_name.lower())

        return TrainingSummary(
            date_from=utc_(date_from),
            date_to=utc_(date_to),
            session_count=len(sessions),
            total_sets=len(logs),
            total_volume_kg=round(_total_volume(logs), 1),
            volume_per_muscle=[
                MuscleVolume(muscle=m, volume_kg=round(v, 1))
                for m, v in sorted(volume_by_muscle.items(), key=lambda kv: -kv[1])
            ],
            best_sets=best_sets,
        )


def _total_volume(logs) -> float:
    return sum((_weight_kg(log) or 0) * log.reps for log in logs)
