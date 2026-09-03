from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlanStatus = Literal["active", "archived"]


# ============================================================
# Target sets
# ============================================================
# A planned set, not a performed one. Actual reps/weight are Phase 3.


class WorkoutSetCreate(BaseModel):
    # Omit set_order to append at the end - the client should not have to know
    # how many sets already exist.
    set_order: int | None = Field(default=None, gt=0)
    target_reps: int | None = Field(default=None, gt=0)
    target_weight_kg: Decimal | None = Field(
        default=None, ge=0, max_digits=7, decimal_places=2
    )
    rest_seconds: int | None = Field(default=None, ge=0)
    notes: str | None = None


class WorkoutSetUpdate(BaseModel):
    """set_order is deliberately absent - reordering goes through
    PUT /workout-exercises/{id}/sets/reorder, which needs one transaction.

    Omitting a key keeps the current value; sending null clears it.
    """

    target_reps: int | None = Field(default=None, gt=0)
    target_weight_kg: Decimal | None = Field(
        default=None, ge=0, max_digits=7, decimal_places=2
    )
    rest_seconds: int | None = Field(default=None, ge=0)
    notes: str | None = None


class WorkoutSetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workout_exercise_set_id: int
    set_order: int
    target_reps: int | None
    target_weight_kg: Decimal | None
    rest_seconds: int | None
    notes: str | None


# ============================================================
# Workout exercises
# ============================================================


class WorkoutExerciseCreate(BaseModel):
    exercise_id: int
    exercise_order: int | None = Field(default=None, gt=0)
    notes: str | None = None
    # Convenience: create the exercise and its target sets in one call. The
    # sets endpoints stay available for editing them afterwards.
    sets: list[WorkoutSetCreate] | None = None


class WorkoutExerciseUpdate(BaseModel):
    """Metadata only. exercise_order is absent for the same reason as
    set_order: a single-row PUT cannot express a reorder without breaking
    uniqueness partway through."""

    notes: str | None = None


class WorkoutExerciseRead(BaseModel):
    workout_exercise_id: int
    exercise_id: int
    exercise_order: int
    notes: str | None

    # Names live only in exercise_translations, resolved requested language ->
    # en -> any. Null for the single catalog row that has no translation at
    # all, so the client must tolerate it.
    name: str | None
    language: str | None
    # Small variant for list rows; null until the icon is uploaded, so clients
    # fall back to thumbnail_url.
    icon_url: str | None
    # Null for the 16 exercises with no thumbnail on file.
    thumbnail_url: str | None

    sets: list[WorkoutSetRead]


# ============================================================
# Workout days
# ============================================================


class WorkoutDayCreate(BaseModel):
    name: str = Field(max_length=150)
    day_order: int | None = Field(default=None, gt=0)
    is_rest_day: bool = False
    description: str | None = None


class WorkoutDayUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    is_rest_day: bool | None = None
    description: str | None = None


class WorkoutDaySummary(BaseModel):
    """Day as it appears in the plan list: enough for a preview, no exercises."""

    workout_day_id: int
    name: str
    day_order: int
    is_rest_day: bool
    exercise_count: int


class WorkoutDayDetail(BaseModel):
    workout_day_id: int
    name: str
    day_order: int
    is_rest_day: bool
    description: str | None
    exercises: list[WorkoutExerciseRead]


# ============================================================
# Workout plans
# ============================================================


class WorkoutPlanCreate(BaseModel):
    name: str = Field(max_length=150)
    description: str | None = None
    goal: str | None = Field(default=None, max_length=50)
    duration_weeks: int | None = Field(default=None, gt=0)


class WorkoutPlanUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    description: str | None = None
    goal: str | None = Field(default=None, max_length=50)
    duration_weeks: int | None = Field(default=None, gt=0)
    # Archiving is an update, not a delete: DELETE removes the plan for good.
    status: PlanStatus | None = None


class WorkoutPlanListItem(BaseModel):
    """One plan in the list: plan fields plus a shallow day preview.

    Deliberately stops above the exercises. Day names are text the user typed,
    not translations, so this response needs no ?language at all.
    """

    plan_id: int
    name: str
    description: str | None
    goal: str | None
    duration_weeks: int | None
    status: str
    # Exactly one of the user's plans is true - the one they are following.
    is_active: bool
    created_at: datetime
    updated_at: datetime
    days: list[WorkoutDaySummary]


class WorkoutPlanDetail(BaseModel):
    plan_id: int
    name: str
    description: str | None
    goal: str | None
    duration_weeks: int | None
    status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    days: list[WorkoutDayDetail]


# ============================================================
# Reorder
# ============================================================
# One endpoint per order column. Each applies its whole list inside a single
# transaction, which is what lets the DEFERRABLE unique constraints tolerate a
# duplicate position mid-flight. Sending the items one at a time over separate
# requests cannot work: each request commits on its own.


class DayReorderItem(BaseModel):
    workout_day_id: int
    day_order: int = Field(gt=0)


class DayReorderRequest(BaseModel):
    items: list[DayReorderItem] = Field(min_length=1)


class ExerciseReorderItem(BaseModel):
    workout_exercise_id: int
    exercise_order: int = Field(gt=0)


class ExerciseReorderRequest(BaseModel):
    items: list[ExerciseReorderItem] = Field(min_length=1)


class SetReorderItem(BaseModel):
    workout_exercise_set_id: int
    set_order: int = Field(gt=0)


class SetReorderRequest(BaseModel):
    items: list[SetReorderItem] = Field(min_length=1)


# ============================================================
# Following a plan / editing one date
# ============================================================


class PlanActiveRequest(BaseModel):
    """Which plan the user is currently following.

    Setting one to true clears whichever plan was active before, in the same
    transaction - a partial unique index makes two active plans impossible, so
    it has to be clear-then-set rather than set-then-clear.
    """

    is_active: bool


class DateExercisesRequest(BaseModel):
    """The complete exercise list for one calendar date.

    A replace, not an append: the picker hands back everything that is ticked,
    so unticking is how an exercise is removed. Sending an empty list clears
    the date entirely.
    """

    exercise_ids: list[int]
