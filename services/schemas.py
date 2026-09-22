"""Pydantic models shared by the service layer, REST API and MCP tools.

Timestamps are UTC (naive internally, serialized with a Z suffix by the API
layer). Weights are stored and returned exactly as entered: value + unit.
"""

from datetime import datetime, UTC

from pydantic import BaseModel, Field


def utc(dt: datetime | None) -> datetime | None:
    """Attach UTC to a naive stored datetime, for transport as ISO 8601."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# --- Catalog -----------------------------------------------------------------


class MediaItem(BaseModel):
    type: str  # "image" or "gif"
    url: str


class ExerciseSummary(BaseModel):
    id: int
    source: str
    name: str
    category: str | None = None
    primary_muscles: list[str] = []
    secondary_muscles: list[str] = []
    equipment: list[str] = []
    media: list[MediaItem] = []


class ExerciseDetail(ExerciseSummary):
    body_parts: list[str] = []
    level: str | None = None
    instructions: list[str] = []
    attribution: str | None = None
    retired_at: datetime | None = None


class Page(BaseModel):
    """Cursor pagination envelope used by catalog search."""

    items: list
    next_cursor: str | None = None
    more_available: bool = False


# --- Workouts ----------------------------------------------------------------


class WorkoutExerciseItem(BaseModel):
    exercise_id: int
    exercise_name: str
    position: int
    target_sets: int
    target_reps_min: int
    target_reps_max: int
    comment: str | None = None


class WorkoutSummary(BaseModel):
    id: int
    name: str
    notes: str | None = None
    exercise_count: int = 0
    created_at: datetime
    updated_at: datetime
    last_performed_at: datetime | None = None


class WorkoutDetail(WorkoutSummary):
    exercises: list[WorkoutExerciseItem] = []


# --- Sessions and sets ---------------------------------------------------------


class Weight(BaseModel):
    """Weight exactly as entered: value + unit, never converted."""

    value: float = Field(ge=0)
    unit: str  # "kg" or "lb"


class SetLogItem(BaseModel):
    id: int
    session_id: int
    exercise_id: int
    exercise_name: str
    set_number: int
    reps: int
    weight: Weight | None = None  # None = bodyweight
    logged_at: datetime


class SessionSummary(BaseModel):
    id: int
    workout_id: int | None
    workout_name: str
    started_at: datetime
    last_activity_at: datetime
    finished_at: datetime | None = None
    notes: str | None = None


class SessionDetail(SessionSummary):
    sets: list[SetLogItem] = []


# --- Stats ---------------------------------------------------------------------


class LastPerformance(BaseModel):
    """All sets of an exercise from the user's most recent session containing it."""

    session_date: datetime
    sets: list[SetLogItem]


class ExerciseHistorySession(BaseModel):
    session_id: int
    workout_name: str
    date: datetime
    sets: list[SetLogItem]


class BestSet(BaseModel):
    exercise_id: int
    exercise_name: str
    weight: Weight | None = None
    reps: int
    date: datetime


class MuscleVolume(BaseModel):
    muscle: str
    volume_kg: float


class TrainingSummary(BaseModel):
    date_from: datetime
    date_to: datetime
    session_count: int
    total_sets: int
    total_volume_kg: float
    volume_per_muscle: list[MuscleVolume]
    best_sets: list[BestSet]


# --- Users and invites ------------------------------------------------------------


class UserInfo(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    unit_pref: str
    time_zone: str
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class InviteInfo(BaseModel):
    id: int
    email: str
    role: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None
    used_by: int | None = None
    revoked_at: datetime | None = None

    @property
    def is_pending(self) -> bool:  # pragma: no cover - trivial helper for UI
        return self.used_at is None and self.revoked_at is None


class ApiKeyInfo(BaseModel):
    id: int
    label: str
    key_prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
