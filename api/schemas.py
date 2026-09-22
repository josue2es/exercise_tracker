"""Request models for the REST API (responses reuse the service schemas)."""

from pydantic import BaseModel, Field

from services.schemas import WorkoutExerciseItem


class WeightIn(BaseModel):
    value: float = Field(ge=0)
    unit: str  # "kg" or "lb"


class WorkoutCreate(BaseModel):
    name: str
    notes: str | None = None


class WorkoutUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None


class WorkoutExercisesIn(BaseModel):
    exercises: list[WorkoutExerciseItem]


class SessionStart(BaseModel):
    workout_id: int


class SessionUpdate(BaseModel):
    finished: bool | None = None
    notes: str | None = None


class SetLogIn(BaseModel):
    exercise_id: int
    reps: int = Field(ge=0, le=100)
    weight: WeightIn | None = None  # omitted = bodyweight
    set_number: int | None = Field(default=None, ge=1)


class SetUpdate(BaseModel):
    reps: int | None = Field(default=None, ge=0, le=100)
    weight: WeightIn | None = None
    set_weight: bool = False  # weight=None means bodyweight when set_weight is true
