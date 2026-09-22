"""MCP server: 15 tools over the service layer, authenticated with API keys.

A sibling of the REST API in the same process, not a client of it. Responses
are sized for an LLM context window: summary first, then detail; default
limits plus a "more available" flag; units, ISO dates and exercise names next
to IDs; errors in plain language with valid options.

The user always comes from the API key; no tool accepts a user_id. Tools call
the synchronous service layer via anyio.to_thread.run_sync so the event loop
(which also serves phones) never blocks.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import anyio.to_thread
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from mcp.types import ToolAnnotations

import services.api_keys as api_keys
import services.catalog as catalog
import services.sessions as sessions
import services.stats as stats
import services.workouts as workouts
from services.context import UserContext
from services.errors import AuthError, ServiceError
from services.schemas import Weight

SEARCH_LIMIT = 10
HISTORY_LIMIT = 5
SESSIONS_LIMIT = 10


# --- authentication ---------------------------------------------------------------


class ApiKeyTokenVerifier(TokenVerifier):
    """Resolves gym_ Bearer tokens through the same service as the REST API."""

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            resolved = await anyio.to_thread.run_sync(api_keys.resolve_key, token)
        except AuthError:
            return None
        return AccessToken(
            token=token,
            client_id=f"user-{resolved.user_id}",
            scopes=sorted(resolved.scopes),
            claims={
                "user_id": resolved.user_id,
                "role": resolved.role,
                "api_key_id": resolved.api_key_id,
            },
        )


def _ctx() -> UserContext:
    """Build the UserContext from the authenticated access token."""
    token = get_access_token()
    claims = token.claims or {}
    return UserContext(
        user_id=int(claims["user_id"]),
        role=str(claims.get("role", "member")),
        scopes=set(token.scopes or []),
        actor="mcp",
        api_key_id=claims.get("api_key_id"),
    )


class ToolCallError(ToolError):
    """Plain-language tool errors, optionally with valid options."""


def _service_error(exc: ServiceError) -> ToolError:
    hints = {
        "forbidden": " Ask the user for a key with write scope.",
        "not_found": " Use the id exactly as returned by search_exercises or list_workouts.",
        "validation": None,
    }
    name = type(exc).__name__
    kind = {"AuthError": "unauthorized", "ScopeError": "forbidden", "NotFoundError": "not_found",
            "ValidationError": "validation"}.get(name, "error")
    hint = hints.get(kind)
    return ToolCallError(str(exc) + (hint or ""))


async def _call(fn, *args, **kwargs):
    try:
        return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))
    except ServiceError as exc:
        raise _service_error(exc) from exc


# --- formatting helpers --------------------------------------------------------------


def _fmt_weight(weight: Weight | None) -> str:
    if weight is None:
        return "bodyweight"
    return f"{weight.value:g} {weight.unit}"


def _fmt_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _fmt_set(s) -> str:
    weight = _fmt_weight(s.weight) if s.weight is not None else "bodyweight"
    return f"set {s.set_number}: {s.reps} reps @ {weight} (exercise: {s.exercise_name}, id {s.exercise_id})"


def _exercise_summary_line(ex) -> str:
    muscles = ", ".join(ex.primary_muscles[:3]) or "unknown muscles"
    equipment = ", ".join(ex.equipment[:2]) or "no equipment"
    return f"- {ex.name} [id {ex.id}] ({muscles}; {equipment}; source: {ex.source})"


async def _resolve_exercise(reference: str):
    """Resolve an exercise by id or (unique) name; ambiguous names return
    candidate options in the error, as required by the response rules."""
    reference = reference.strip()
    if reference.isdigit():
        return await _call(catalog.get_exercise, _ctx(), int(reference))
    page = await _call(catalog.search_exercises, _ctx(), reference, limit=SEARCH_LIMIT + 5)
    exact = [e for e in page.items if e.name.lower() == reference.lower()]
    if len(exact) == 1:
        return await _call(catalog.get_exercise, _ctx(), exact[0].id)
    if len(page.items) == 1:
        return await _call(catalog.get_exercise, _ctx(), page.items[0].id)
    if not page.items:
        raise ToolCallError(
            f"No exercise found for {reference!r}. Use search_exercises to find one."
        )
    candidates = "\n".join(_exercise_summary_line(e) for e in page.items[:5])
    raise ToolCallError(
        f"{reference!r} is ambiguous. Pick one of these candidates:\n{candidates}"
    )


# --- server and tools ------------------------------------------------------------------


mcp = FastMCP(
    "Gym Tracker",
    instructions=(
        "Tools for tracking workouts: search the exercise catalog, read and edit "
        "workout plans, log training sets and read training history. The acting "
        "user is always the owner of the API key; weights are {value, unit} and "
        "never converted on write."
    ),
    auth=ApiKeyTokenVerifier(),
)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="search_exercises",
    description=(
        "Search the shared exercise catalog by name (optionally filtered by "
        "muscle, equipment or source). Use this first to find exercise ids. "
        "Example: search_exercises(query='bench press')"
    ),
)
async def search_exercises(
    query: str = "",
    muscle: str | None = None,
    equipment: str | None = None,
    source: str | None = None,
) -> str:
    page = await _call(catalog.search_exercises, _ctx(), query, muscle, equipment, source, SEARCH_LIMIT)
    lines = [_exercise_summary_line(e) for e in page.items]
    more = "\nMore results available; call again with cursor." if page.more_available else ""
    return "\n".join(lines) + more


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="get_exercise",
    description=(
        "Get one exercise's instructions, muscles and equipment by id (no media). "
        "Example: get_exercise(exercise_id=123)"
    ),
)
async def get_exercise(exercise_id: int) -> str:
    detail = await _call(catalog.get_exercise, _ctx(), exercise_id)
    lines = [f"{detail.name} [id {detail.id}] ({detail.source})"]
    if detail.primary_muscles:
        lines.append(f"Primary muscles: {', '.join(detail.primary_muscles)}")
    if detail.secondary_muscles:
        lines.append(f"Secondary muscles: {', '.join(detail.secondary_muscles)}")
    if detail.equipment:
        lines.append(f"Equipment: {', '.join(detail.equipment)}")
    if detail.level:
        lines.append(f"Level: {detail.level}")
    if detail.instructions:
        lines.append("Instructions:")
        lines.extend(f"{i}. {step}" for i, step in enumerate(detail.instructions, 1))
    if detail.attribution:
        lines.append(f"Attribution: {detail.attribution}")
    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="list_workouts",
    description=(
        "List the user's workouts with exercise counts and last performed dates. "
        "Example: list_workouts()"
    ),
)
async def list_workouts() -> str:
    items = await _call(workouts.list_workouts, _ctx())
    if not items:
        return "No workouts yet. Create one with create_workout."
    lines = [
        f"- {w.name} [id {w.id}]: {w.exercise_count} exercises, "
        f"last performed {w.last_performed_at.strftime('%Y-%m-%d') if w.last_performed_at else 'never'}"
        for w in items
    ]
    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="get_workout",
    description=(
        "Get a workout's exercises with sets, rep ranges, comments and the last "
        "performance of each. Example: get_workout(workout_id=1)"
    ),
)
async def get_workout(workout_id: int) -> str:
    detail = await _call(workouts.get_workout, _ctx(), workout_id)
    lines = [f"{detail.name} [id {detail.id}]"]
    if detail.notes:
        lines.append(f"Notes: {detail.notes}")
    for e in detail.exercises:
        target = f"{e.target_sets} x {e.target_reps_min}" + (
            f"-{e.target_reps_max}" if e.target_reps_max != e.target_reps_min else ""
        )
        lines.append(f"- {e.exercise_name} [id {e.exercise_id}]: target {target}")
        if e.comment:
            lines.append(f"  comment: {e.comment}")
        perf = await _call(stats.get_last_performance, _ctx(), e.exercise_id)
        if perf:
            sets = ", ".join(
                f"{s.weight.value:g} {s.weight.unit} x {s.reps}" if s.weight else f"bw x {s.reps}"
                for s in perf.sets
            )
            lines.append(f"  last ({_fmt_date(perf.session_date)}): {sets}")
    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="get_last_performance",
    description=(
        "Get the sets and date of the user's most recent session with one "
        "exercise (across all workouts). Example: "
        "get_last_performance(exercise='Bench Press')"
    ),
)
async def get_last_performance(exercise: str) -> str:
    detail = await _resolve_exercise(exercise)
    perf = await _call(stats.get_last_performance, _ctx(), detail.id)
    if perf is None:
        return f"No history yet for {detail.name} [id {detail.id}]."
    sets = "; ".join(
        f"{s.weight.value:g} {s.weight.unit} x {s.reps}" if s.weight else f"bodyweight x {s.reps}"
        for s in perf.sets
    )
    return f"Last session with {detail.name} ({_fmt_date(perf.session_date)}): {sets}"


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="get_exercise_history",
    description=(
        "Get the user's past sessions with one exercise, newest first "
        f"(default last {HISTORY_LIMIT}). Example: get_exercise_history(exercise='Bench Press')"
    ),
)
async def get_exercise_history(exercise: str, limit: int = HISTORY_LIMIT) -> str:
    detail = await _resolve_exercise(exercise)
    history = await _call(stats.get_exercise_history, _ctx(), detail.id, limit)
    if not history:
        return f"No history yet for {detail.name} [id {detail.id}]."
    lines = [f"History for {detail.name} [id {detail.id}]:"]
    for entry in history:
        sets = ", ".join(
            f"{s.weight.value:g}{s.weight.unit}x{s.reps}" if s.weight else f"bwx{s.reps}"
            for s in entry.sets
        )
        lines.append(f"- {_fmt_date(entry.date)} ({entry.workout_name}): {sets}")
    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="list_sessions",
    description=(
        f"List the user's training sessions, newest first (default last "
        f"{SESSIONS_LIMIT}). Example: list_sessions(limit=5)"
    ),
)
async def list_sessions(limit: int = SESSIONS_LIMIT) -> str:
    items = await _call(sessions.list_sessions, _ctx(), limit=limit)
    if not items:
        return "No sessions logged yet. Start one with start_session."
    lines = [
        f"- {_fmt_date(s.started_at)} {s.workout_name} [session id {s.id}]"
        + (" (open)" if s.finished_at is None else "")
        for s in items
    ]
    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="get_open_session",
    description=(
        "Get the user's session in progress with its logged sets, if any. "
        "Example: get_open_session()"
    ),
)
async def get_open_session() -> str:
    detail = await _call(sessions.get_open_session, _ctx())
    if detail is None:
        return "No open session. Use start_session to begin a workout."
    lines = [f"Open session: {detail.workout_name} (started {_fmt_date(detail.started_at)}, session id {detail.id})"]
    lines.extend(_fmt_set(s) for s in detail.sets)
    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=True),
    name="get_training_summary",
    description=(
        "Training summary for a period (defaults to the last 30 days): session "
        "count, total sets, volume per muscle (kg) and best set per exercise. "
        "Example: get_training_summary(days=7)"
    ),
)
async def get_training_summary(days: int = 30) -> str:
    from datetime import timedelta

    from db.models import utcnow

    date_to = utcnow()
    date_from = date_to - timedelta(days=days)
    summary = await _call(stats.get_training_summary, _ctx(), date_from, date_to)
    lines = [
        f"Summary for the last {days} days:",
        f"- sessions: {summary.session_count}",
        f"- total sets: {summary.total_sets}",
        f"- total volume: {summary.total_volume_kg:g} kg",
    ]
    if summary.volume_per_muscle:
        volume = ", ".join(f"{v.muscle}: {v.volume_kg:g} kg" for v in summary.volume_per_muscle[:10])
        lines.append(f"- volume per muscle: {volume}")
    for b in summary.best_sets:
        lines.append(f"- best {b.exercise_name} [id {b.exercise_id}]: {_fmt_weight(b.weight)} x {b.reps}")
    return "\n".join(lines)


# --- write tools -------------------------------------------------------------------------


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    name="create_workout",
    description=(
        "Create a new workout plan with exercises, sets and rep ranges. "
        "Example: create_workout(name='Push Day', exercises=["
        "{'exercise': 'Bench Press', 'sets': 3, 'reps_min': 8, 'reps_max': 12}])"
    ),
)
async def create_workout(
    name: str,
    exercises: list[dict] | None = None,
    notes: str | None = None,
) -> str:
    ctx = _ctx()
    detail = await _call(workouts.create_workout, ctx, name, notes)
    lines = [f"Created workout '{detail.name}' [id {detail.id}]."]
    if exercises:
        items = []
        for position, spec in enumerate(exercises):
            resolved = await _resolve_exercise(str(spec["exercise"]))
            items.append(
                {
                    "exercise_id": resolved.id,
                    "exercise_name": resolved.name,
                    "position": position,
                    "target_sets": int(spec.get("sets", 3)),
                    "target_reps_min": int(spec.get("reps_min", 8)),
                    "target_reps_max": int(spec.get("reps_max", spec.get("reps_min", 12))),
                    "comment": spec.get("comment"),
                }
            )
        detail = await _call(workouts.set_workout_exercises, ctx, detail.id, items)
        lines.append(f"{len(items)} exercises added: " + ", ".join(e.exercise_name for e in detail.exercises))
    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    name="update_workout",
    description=(
        "Update a workout: rename it, change notes, or replace the whole ordered "
        "exercise list (replacing overwrites the previous list). "
        "Example: update_workout(workout_id=1, name='Push Day A')"
    ),
)
async def update_workout(
    workout_id: int,
    name: str | None = None,
    notes: str | None = None,
    exercises: list[dict] | None = None,
) -> str:
    ctx = _ctx()
    detail = await _call(workouts.update_workout, ctx, workout_id, name, notes)
    lines = [f"Updated workout '{detail.name}' [id {detail.id}]."]
    if exercises is not None:
        items = []
        for position, spec in enumerate(exercises):
            resolved = await _resolve_exercise(str(spec["exercise"]))
            items.append(
                {
                    "exercise_id": resolved.id,
                    "exercise_name": resolved.name,
                    "position": position,
                    "target_sets": int(spec.get("sets", 3)),
                    "target_reps_min": int(spec.get("reps_min", 8)),
                    "target_reps_max": int(spec.get("reps_max", spec.get("reps_min", 12))),
                    "comment": spec.get("comment"),
                }
            )
        detail = await _call(workouts.set_workout_exercises, ctx, workout_id, items)
        lines.append(f"Exercise list replaced with {len(items)} exercises.")
    return "\n".join(lines)


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    name="start_session",
    description=(
        "Open a training session for a workout; any open session is finished "
        "first. Example: start_session(workout_id=1)"
    ),
)
async def start_session(workout_id: int) -> str:
    detail = await _call(sessions.start_session, _ctx(), workout_id)
    return (
        f"Session started for '{detail.workout_name}' (session id {detail.id}). "
        "Log sets with log_set."
    )


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    name="log_set",
    description=(
        "Log one set: exercise (id or unique name), reps and optionally weight "
        "as {value, unit} (omitted = bodyweight). Without set_number the next "
        "number is used; repeating a set_number returns the existing set "
        "unchanged. Requires an open session (start_session first) unless "
        "workout_id is given. Example: "
        "log_set(exercise='Bench Press', reps=8, weight={'value': 80, 'unit': 'kg'})"
    ),
)
async def log_set(
    exercise: str,
    reps: int,
    weight: dict | None = None,
    set_number: int | None = None,
    workout_id: int | None = None,
) -> str:
    ctx = _ctx()
    detail = await _resolve_exercise(exercise)
    weight_value = float(weight["value"]) if weight else None
    weight_unit = weight.get("unit") if weight else None
    if workout_id is not None:
        item = await _call(
            sessions.log_set, ctx, workout_id, detail.id, reps, weight_value, weight_unit, set_number
        )
    else:
        open_session = await _call(sessions.get_open_session, ctx)
        if open_session is None:
            raise ToolCallError(
                "No open session. Call start_session first, or pass workout_id."
            )
        item = await _call(
            sessions.log_set_to_session, ctx, open_session.id, detail.id, reps,
            weight_value, weight_unit, set_number,
        )
    return (
        f"Logged {detail.name} set {item.set_number}: {item.reps} reps @ "
        f"{_fmt_weight(item.weight)} (session id {item.session_id})."
    )


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    name="update_set",
    description=(
        "Correct a logged set (id from get_open_session or get_session). Pass "
        "weight=null with set_weight=true to switch to bodyweight. "
        "Example: update_set(set_id=42, reps=9)"
    ),
)
async def update_set(
    set_id: int,
    reps: int | None = None,
    weight: dict | None = None,
    set_weight: bool = False,
) -> str:
    weight_value = float(weight["value"]) if weight else None
    weight_unit = weight.get("unit") if weight else None
    item = await _call(
        sessions.update_set, _ctx(), set_id, reps, weight_value, weight_unit,
        set_weight or weight is not None,
    )
    return (
        f"Updated set {item.set_number} of {item.exercise_name}: {item.reps} reps @ "
        f"{_fmt_weight(item.weight)}."
    )


@mcp.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    name="finish_session",
    description=(
        "Close the open session (or a specific one). Example: finish_session()"
    ),
)
async def finish_session(session_id: int | None = None, notes: str | None = None) -> str:
    detail = await _call(sessions.finish_session, _ctx(), session_id, notes)
    return f"Session '{detail.workout_name}' finished with {len(detail.sets)} sets."
