"""SQLAlchemy models for every table in the phase 1 data model.

All timestamps are stored as naive UTC; conversion to the user's time zone
happens only when displaying. Lists are stored as JSON. Exercises are shared
and read-only; owned tables all carry ``user_id``.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Naive UTC now (what we store in the database)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    unit_pref: Mapped[str] = mapped_column(String(5), nullable=False, default="kg")
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/El_Salvador")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    # Null only for the bootstrap invite that creates the first admin.
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # JSON list of scopes: ["read"] or ["read", "write"]
    scopes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Exercise(Base):
    __tablename__ = "exercises"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_exercises_source_source_id"),
        Index("ix_exercises_search_text", "search_text"),
        Index("ix_exercises_source_retired", "source", "retired_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    body_parts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    primary_muscles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    secondary_muscles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    equipment: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    instructions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # JSON list of media entries, e.g. {"type": "image", "url": "/media/..."} or {"type": "gif", "url": "https://..."}
    media: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Workout(Base):
    __tablename__ = "workouts"
    __table_args__ = (Index("ix_workouts_user_deleted", "user_id", "deleted_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"
    __table_args__ = (
        UniqueConstraint("workout_id", "exercise_id", name="uq_workout_exercises"),
        CheckConstraint("target_sets >= 1 AND target_sets <= 20", name="ck_workout_exercises_sets"),
        CheckConstraint(
            "target_reps_min >= 1 AND target_reps_min <= target_reps_max AND target_reps_max <= 100",
            name="ck_workout_exercises_reps",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workout_id: Mapped[int] = mapped_column(ForeignKey("workouts.id"), nullable=False, index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_sets: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    target_reps_min: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    target_reps_max: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrainingSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        # At most one open session per user.
        Index(
            "uq_sessions_open_per_user",
            "user_id",
            unique=True,
            sqlite_where=text("finished_at IS NULL"),
        ),
        Index("ix_sessions_user_started", "user_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workout_id: Mapped[int | None] = mapped_column(ForeignKey("workouts.id"), nullable=True)
    # Snapshot so history stays readable after the plan is edited or deleted.
    workout_name: Mapped[str] = mapped_column(String(200), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SetLog(Base):
    __tablename__ = "set_logs"
    __table_args__ = (
        # Makes retried logging safe.
        UniqueConstraint("session_id", "exercise_id", "set_number", name="uq_set_logs_session_exercise_set"),
        CheckConstraint("reps >= 0 AND reps <= 100", name="ck_set_logs_reps"),
        CheckConstraint("weight_value IS NULL OR weight_value >= 0", name="ck_set_logs_weight"),
        Index("ix_set_logs_last_performance", "user_id", "exercise_id", "logged_at"),
        Index("ix_set_logs_session_exercise", "session_id", "exercise_id", "set_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    # Must match the session's user_id; enforced by the service layer.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), nullable=False)
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reps: Mapped[int] = mapped_column(Integer, nullable=False)
    # Null weight = bodyweight. Unit required when weight is set (checked in service layer).
    weight_value: Mapped[float | None] = mapped_column(nullable=True)
    weight_unit: Mapped[str | None] = mapped_column(String(5), nullable=True)
    logged_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id"), nullable=True)
    # Actor: "api", "mcp" or "ui".
    actor: Mapped[str] = mapped_column(String(10), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retired: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # JSON list of error strings.
    errors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
