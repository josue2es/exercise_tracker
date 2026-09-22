"""REST API v1: a thin adapter over the service layer.

Every call needs a Bearer API key. Endpoints are synchronous ``def`` handlers,
so FastAPI runs them in its threadpool and blocking DB calls never touch the
event loop (which also serves the phones). Conventions: ISO 8601 timestamps
with offset, weights as {value, unit}, rep targets as {min, max}, cursor
pagination with next_cursor, errors as {error, message}.
"""

from datetime import datetime, UTC

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

import services.catalog as catalog
import services.sessions as sessions
import services.stats as stats
import services.users as users
import services.workouts as workouts
from api.deps import read_key, write_key
from api.schemas import (
    SessionStart,
    SessionUpdate,
    SetLogIn,
    SetUpdate,
    WorkoutCreate,
    WorkoutExercisesIn,
    WorkoutUpdate,
)
from services.context import UserContext
from services.errors import ServiceError
from services.schemas import Page

router = APIRouter(prefix="/api/v1")

MAX_LIMIT = 50


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _page_response(page: Page) -> dict:
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        "next_cursor": page.next_cursor,
        "more_available": page.more_available,
    }


# --- me ---------------------------------------------------------------------------


@router.get("/me")
def get_me(ctx: UserContext = Depends(read_key)):
    user = users.get_user(ctx.user_id)
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "unit_pref": user.unit_pref,
        "time_zone": user.time_zone,
        "scopes": sorted(ctx.scopes),
    }


# --- exercises ----------------------------------------------------------------------


@router.get("/exercises")
def search_exercises(
    q: str = "",
    muscle: str | None = None,
    equipment: str | None = None,
    source: str | None = None,
    limit: int = Query(default=20, ge=1, le=MAX_LIMIT),
    cursor: int | None = None,
    ctx: UserContext = Depends(read_key),
):
    page = catalog.search_exercises(ctx, q, muscle, equipment, source, limit, cursor)
    return _page_response(page)


@router.get("/exercises/{exercise_id}")
def get_exercise(exercise_id: int, ctx: UserContext = Depends(read_key)):
    return catalog.get_exercise(ctx, exercise_id).model_dump(mode="json")


@router.get("/exercises/{exercise_id}/last")
def get_last_performance(exercise_id: int, ctx: UserContext = Depends(read_key)):
    perf = stats.get_last_performance(ctx, exercise_id)
    if perf is None:
        return {"last_performance": None}
    return {
        "last_performance": {
            "date": perf.session_date,
            "sets": [s.model_dump(mode="json") for s in perf.sets],
        }
    }


@router.get("/exercises/{exercise_id}/history")
def get_exercise_history(
    exercise_id: int,
    limit: int = Query(default=10, ge=1, le=100),
    ctx: UserContext = Depends(read_key),
):
    history = stats.get_exercise_history(ctx, exercise_id, limit)
    return {
        "history": [
            {
                "session_id": h.session_id,
                "workout_name": h.workout_name,
                "date": h.date,
                "sets": [s.model_dump(mode="json") for s in h.sets],
            }
            for h in history
        ]
    }


# --- workouts ------------------------------------------------------------------------


@router.get("/workouts")
def list_workouts(ctx: UserContext = Depends(read_key)):
    return {"workouts": [w.model_dump(mode="json") for w in workouts.list_workouts(ctx)]}


@router.get("/workouts/{workout_id}")
def get_workout(workout_id: int, ctx: UserContext = Depends(read_key)):
    return workouts.get_workout(ctx, workout_id).model_dump(mode="json")


@router.post("/workouts", status_code=201)
def create_workout(body: WorkoutCreate, ctx: UserContext = Depends(write_key)):
    return workouts.create_workout(ctx, body.name, body.notes).model_dump(mode="json")


@router.patch("/workouts/{workout_id}")
def update_workout(workout_id: int, body: WorkoutUpdate, ctx: UserContext = Depends(write_key)):
    return workouts.update_workout(ctx, workout_id, body.name, body.notes).model_dump(mode="json")


@router.put("/workouts/{workout_id}/exercises")
def set_workout_exercises(
    workout_id: int, body: WorkoutExercisesIn, ctx: UserContext = Depends(write_key)
):
    return workouts.set_workout_exercises(ctx, workout_id, body.exercises).model_dump(mode="json")


# --- sessions ---------------------------------------------------------------------------


@router.get("/sessions")
def list_sessions(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    ctx: UserContext = Depends(read_key),
):
    items = sessions.list_sessions(
        ctx,
        _naive_utc(date_from) if date_from else None,
        _naive_utc(date_to) if date_to else None,
        limit,
    )
    return {"sessions": [s.model_dump(mode="json") for s in items]}


@router.get("/sessions/open")
def get_open_session(ctx: UserContext = Depends(read_key)):
    session_detail = sessions.get_open_session(ctx)
    return {"session": session_detail.model_dump(mode="json") if session_detail else None}


@router.get("/sessions/{session_id}")
def get_session(session_id: int, ctx: UserContext = Depends(read_key)):
    return sessions.get_session(ctx, session_id).model_dump(mode="json")


@router.post("/sessions", status_code=201)
def start_session(body: SessionStart, ctx: UserContext = Depends(write_key)):
    return sessions.start_session(ctx, body.workout_id).model_dump(mode="json")


@router.patch("/sessions/{session_id}")
def update_session(session_id: int, body: SessionUpdate, ctx: UserContext = Depends(write_key)):
    if body.finished:
        return sessions.finish_session(ctx, session_id, body.notes).model_dump(mode="json")
    if body.notes is not None:
        return sessions.update_session_notes(ctx, session_id, body.notes).model_dump(mode="json")
    return sessions.get_session(ctx, session_id).model_dump(mode="json")


@router.post("/sessions/{session_id}/sets", status_code=201)
def log_set(session_id: int, body: SetLogIn, ctx: UserContext = Depends(write_key)):
    item = sessions.log_set_to_session(
        ctx,
        session_id,
        body.exercise_id,
        body.reps,
        body.weight.value if body.weight else None,
        body.weight.unit if body.weight else None,
        body.set_number,
    )
    return item.model_dump(mode="json")


@router.patch("/sets/{set_id}")
def update_set(set_id: int, body: SetUpdate, ctx: UserContext = Depends(write_key)):
    item = sessions.update_set(
        ctx,
        set_id,
        body.reps,
        body.weight.value if body.weight else None,
        body.weight.unit if body.weight else None,
        body.set_weight or body.weight is not None,
    )
    return item.model_dump(mode="json")


# --- stats -------------------------------------------------------------------------------


@router.get("/stats/summary")
def training_summary(
    date_from: datetime,
    date_to: datetime | None = None,
    ctx: UserContext = Depends(read_key),
):
    summary = stats.get_training_summary(
        ctx,
        _naive_utc(date_from),
        _naive_utc(date_to) if date_to else datetime.now(UTC).replace(tzinfo=None),
    )
    return {
        "date_from": summary.date_from,
        "date_to": summary.date_to,
        "session_count": summary.session_count,
        "total_sets": summary.total_sets,
        "total_volume_kg": summary.total_volume_kg,
        "volume_per_muscle": [v.model_dump(mode="json") for v in summary.volume_per_muscle],
        "best_sets": [b.model_dump(mode="json") for b in summary.best_sets],
    }


# --- error translation ---------------------------------------------------------------------


def service_error_handler(request, exc: ServiceError) -> JSONResponse:
    """Translate service errors into {error, message} with the right status."""
    code_by_class = {
        "AuthError": "unauthorized",
        "ScopeError": "forbidden",
        "NotFoundError": "not_found",
        "ValidationError": "validation",
        "ConflictError": "conflict",
        "RateLimitError": "rate_limited",
    }
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": code_by_class.get(type(exc).__name__, "error"), "message": str(exc)},
    )


__all__ = ["router", "service_error_handler"]
