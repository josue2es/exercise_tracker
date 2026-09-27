"""Sessions service: training sessions and set logs.

Rules implemented here:
- At most one open session per user; start_session finishes any open one first.
- The first saved set starts the session (log_set starts one if needed).
- Idle close is lazy: reading the open session closes it after 6 idle hours.
- A repeated (session, exercise, set_number) returns the existing set unchanged,
  so retried logging is safe.
- set_logs.user_id always matches its session's user_id.
- Units are stored as entered; conversion happens only when reading.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from db.models import Exercise, SetLog, TrainingSession, Workout, utcnow
from db.session import get_session as get_db_session
from services.audit import record_audit
from services.context import UserContext
from services.errors import NotFoundError, ScopeError, ValidationError
from services.schemas import SessionDetail, SessionSummary, SetLogItem, Weight, utc as utc_
from services.workouts import session_label

IDLE_CLOSE_HOURS = 6
VALID_UNITS = {"kg", "lb"}


def _to_set_item(log: SetLog) -> SetLogItem:
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


def _to_session_detail(session: TrainingSession) -> SessionDetail:
    return SessionDetail(
        id=session.id,
        workout_id=session.workout_id,
        workout_name=session.workout_name,
        started_at=utc_(session.started_at),
        last_activity_at=utc_(session.last_activity_at),
        finished_at=utc_(session.finished_at),
        notes=session.notes,
        sets=[_to_set_item(s) for s in session.sets],
    )


def _get_owned_session(session, ctx: UserContext, session_id: int) -> TrainingSession:
    row = session.scalars(
        select(TrainingSession).where(
            TrainingSession.id == session_id,
            TrainingSession.user_id == ctx.user_id,
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Session not found")
    return row


def _validate_set_input(reps: int | None, weight_value: float | None, weight_unit: str | None) -> None:
    if reps is not None and not (0 <= reps <= 100):
        raise ValidationError("Reps must be between 0 and 100")
    if weight_value is not None:
        if weight_value < 0:
            raise ValidationError("Weight cannot be negative")
        if not weight_unit or weight_unit not in VALID_UNITS:
            raise ValidationError("Unit (kg or lb) is required when weight is set")
    if weight_value is None and weight_unit is not None:
        raise ValidationError("Unit without weight is not valid; omit both for bodyweight")


def _maybe_close_idle(session, user_id: int) -> None:
    """Lazy idle close: finish the open session after 6 idle hours."""
    row = session.scalars(
        select(TrainingSession).where(
            TrainingSession.user_id == user_id,
            TrainingSession.finished_at.is_(None),
        )
    ).one_or_none()
    if row and utcnow() - row.last_activity_at > timedelta(hours=IDLE_CLOSE_HOURS):
        row.finished_at = row.last_activity_at


def _open_session(session, user_id: int) -> TrainingSession | None:
    return session.scalars(
        select(TrainingSession).where(
            TrainingSession.user_id == user_id,
            TrainingSession.finished_at.is_(None),
        )
    ).one_or_none()


# --- read operations -------------------------------------------------------------


def get_open_session(ctx: UserContext) -> SessionDetail | None:
    """The open session, if any; closes it first when idle for over 6 hours."""
    with get_db_session() as session:
        _maybe_close_idle(session, ctx.user_id)
        row = _open_session(session, ctx.user_id)
        return _to_session_detail(row) if row else None


def get_session(ctx: UserContext, session_id: int) -> SessionDetail:
    with get_db_session() as session:
        return _to_session_detail(_get_owned_session(session, ctx, session_id))


def list_sessions(
    ctx: UserContext,
    date_from=None,
    date_to=None,
    limit: int = 20,
) -> list[SessionSummary]:
    """List the caller's sessions, newest first, optionally filtered by date."""
    stmt = select(TrainingSession).where(TrainingSession.user_id == ctx.user_id)
    if date_from is not None:
        stmt = stmt.where(TrainingSession.started_at >= date_from)
    if date_to is not None:
        stmt = stmt.where(TrainingSession.started_at < date_to)
    stmt = stmt.order_by(TrainingSession.started_at.desc()).limit(max(1, min(limit, 200)))
    with get_db_session() as session:
        rows = session.scalars(stmt).unique().all()
        return [
            SessionSummary(
                id=r.id,
                workout_id=r.workout_id,
                workout_name=r.workout_name,
                started_at=utc_(r.started_at),
                last_activity_at=utc_(r.last_activity_at),
                finished_at=utc_(r.finished_at),
                notes=r.notes,
            )
            for r in rows
        ]


# --- write operations ---------------------------------------------------------------


def start_session(ctx: UserContext, workout_id: int) -> SessionDetail:
    """Open a session for a workout; finishes any open session first."""
    ctx.require_write()
    with get_db_session() as session:
        workout = session.scalars(
            select(Workout).where(
                Workout.id == workout_id,
                Workout.user_id == ctx.user_id,
                Workout.deleted_at.is_(None),
            )
        ).one_or_none()
        if workout is None:
            raise NotFoundError("Workout not found")

        _maybe_close_idle(session, ctx.user_id)
        for row in session.scalars(
            select(TrainingSession).where(
                TrainingSession.user_id == ctx.user_id,
                TrainingSession.finished_at.is_(None),
            )
        ):
            row.finished_at = row.last_activity_at

        row = TrainingSession(
            user_id=ctx.user_id,
            workout_id=workout.id,
            workout_name=session_label(workout),  # snapshot: history survives plan edits
        )
        session.add(row)
        session.flush()
        _audit(session, ctx, "session.start", target=workout.name)
        return _to_session_detail(row)


def log_set(
    ctx: UserContext,
    workout_id: int,
    exercise_id: int,
    reps: int,
    weight_value: float | None = None,
    weight_unit: str | None = None,
    set_number: int | None = None,
) -> SetLogItem:
    """Log one set. Starts the session if none is open (finishing any other
    open one). Without set_number the next number is used; a repeated
    set_number returns the existing set unchanged (safe retries).

    Accepts any catalog exercise, not only the workout's.
    """
    ctx.require_write()
    _validate_set_input(reps, weight_value, weight_unit)
    if set_number is not None and set_number < 1:
        raise ValidationError("set_number must be at least 1")

    with get_db_session() as session:
        workout = session.scalars(
            select(Workout).where(
                Workout.id == workout_id,
                Workout.user_id == ctx.user_id,
                Workout.deleted_at.is_(None),
            )
        ).one_or_none()
        if workout is None:
            raise NotFoundError("Workout not found")
        exercise = session.get(Exercise, exercise_id)
        if exercise is None:
            raise NotFoundError("Exercise not found")

        _maybe_close_idle(session, ctx.user_id)
        row = _open_session(session, ctx.user_id)
        if row is None:
            row = TrainingSession(
                user_id=ctx.user_id, workout_id=workout.id, workout_name=session_label(workout)
            )
            session.add(row)
            session.flush()
        elif row.workout_id != workout.id:
            # At most one open session: switching workouts finishes the old one.
            row.finished_at = row.last_activity_at
            row = TrainingSession(
                user_id=ctx.user_id, workout_id=workout.id, workout_name=session_label(workout)
            )
            session.add(row)
            session.flush()

        return _insert_set(
            session, ctx, row, exercise_id, reps, weight_value, weight_unit, set_number
        )


def log_set_to_session(
    ctx: UserContext,
    session_id: int,
    exercise_id: int,
    reps: int,
    weight_value: float | None = None,
    weight_unit: str | None = None,
    set_number: int | None = None,
) -> SetLogItem:
    """Log one set into a specific (open) session. Same retry semantics as
    log_set: a repeated set_number returns the existing set unchanged."""
    ctx.require_write()
    _validate_set_input(reps, weight_value, weight_unit)
    if set_number is not None and set_number < 1:
        raise ValidationError("set_number must be at least 1")
    with get_db_session() as session:
        row = _get_owned_session(session, ctx, session_id)
        if row.finished_at is not None:
            raise ValidationError("This session is already finished")
        exercise = session.get(Exercise, exercise_id)
        if exercise is None:
            raise NotFoundError("Exercise not found")
        return _insert_set(
            session, ctx, row, exercise_id, reps, weight_value, weight_unit, set_number
        )


def _insert_set(session, ctx: UserContext, session_row: TrainingSession, exercise_id, reps,
                weight_value, weight_unit, set_number) -> SetLogItem:
    exercise = session.get(Exercise, exercise_id)
    if set_number is None:
        highest = session.scalars(
            select(SetLog.set_number).where(
                SetLog.session_id == session_row.id, SetLog.exercise_id == exercise_id
            )
        ).all()
        set_number = max(highest, default=0) + 1
    else:
        existing = session.scalars(
            select(SetLog).where(
                SetLog.session_id == session_row.id,
                SetLog.exercise_id == exercise_id,
                SetLog.set_number == set_number,
            )
        ).one_or_none()
        if existing is not None:
            # Retried call: return the existing set unchanged.
            return _to_set_item(existing)

    log = SetLog(
        session_id=session_row.id,
        user_id=ctx.user_id,  # always inherits from the session
        exercise_id=exercise_id,
        set_number=set_number,
        reps=reps,
        weight_value=weight_value,
        weight_unit=weight_unit,
    )
    session.add(log)
    session_row.last_activity_at = utcnow()
    session.flush()
    _audit(session, ctx, "set.log", target=f"{exercise.name} #{set_number}")
    return _to_set_item(log)


def update_session_notes(ctx: UserContext, session_id: int, notes: str | None) -> SessionDetail:
    """Update only the notes of a session."""
    with get_db_session() as session:
        row = _get_owned_session(session, ctx, session_id)
        row.notes = notes or None
        return _to_session_detail(row)


def update_set(
    ctx: UserContext,
    set_id: int,
    reps: int | None = None,
    weight_value: float | None = None,
    weight_unit: str | None = None,
    set_weight: bool = False,
) -> SetLogItem:
    """Correct a logged set.

    ``reps`` is updated when given (it cannot be null in the data model);
    weight is only updated when ``set_weight`` is true, and
    ``weight_value=None`` with ``set_weight=True`` means bodyweight.
    """
    ctx.require_write()
    if reps is not None and not (0 <= reps <= 100):
        raise ValidationError("Reps must be between 0 and 100")
    if set_weight and weight_value is not None:
        if weight_value < 0:
            raise ValidationError("Weight cannot be negative")
        if not weight_unit or weight_unit not in VALID_UNITS:
            raise ValidationError("Unit (kg or lb) is required when weight is set")
    if set_weight and weight_value is None:
        weight_unit = None

    with get_db_session() as session:
        log = _get_owned_set(session, ctx, set_id)
        if reps is not None:
            log.reps = reps
        if set_weight:
            log.weight_value = weight_value
            log.weight_unit = weight_unit
        log.session.last_activity_at = utcnow()
        session.flush()
        _audit(session, ctx, "set.update", target=f"set #{set_id}")
        return _to_set_item(log)


def delete_set(ctx: UserContext, set_id: int) -> None:
    """Delete a logged set. UI only: agents cannot delete in phase 1."""
    if ctx.actor != "ui":
        raise ScopeError("Sets can only be deleted in the app")
    with get_db_session() as session:
        log = _get_owned_set(session, ctx, set_id)
        log.session.last_activity_at = utcnow()
        session.delete(log)
        _audit(session, ctx, "set.delete", target=f"set #{set_id}")


def _get_owned_set(session, ctx: UserContext, set_id: int) -> SetLog:
    log = session.scalars(
        select(SetLog).where(SetLog.id == set_id, SetLog.user_id == ctx.user_id)
    ).one_or_none()
    if log is None:
        raise NotFoundError("Set not found")
    return log


def finish_session(ctx: UserContext, session_id: int | None = None, notes: str | None = None) -> SessionDetail:
    """Close a session (the open one when no id is given)."""
    ctx.require_write()
    with get_db_session() as session:
        if session_id is None:
            _maybe_close_idle(session, ctx.user_id)
            row = _open_session(session, ctx.user_id)
            if row is None:
                raise NotFoundError("No open session")
        else:
            row = _get_owned_session(session, ctx, session_id)
            if row.finished_at is not None:
                raise ValidationError("Session is already finished")
        if notes is not None:
            row.notes = notes or None
        row.finished_at = utcnow()
        _audit(session, ctx, "session.finish", target=row.workout_name)
        return _to_session_detail(row)


def _audit(session, ctx: UserContext, action: str, target: str | None = None) -> None:
    """Audit writes that arrive through REST or MCP."""
    if ctx.actor in ("api", "mcp"):
        record_audit(session, ctx, action, target)
