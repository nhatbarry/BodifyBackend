from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SessionStatus = Literal["in_progress", "paused", "completed", "abandoned"]


# ============================================================
# Session sets - what was actually lifted
# ============================================================


class SessionSetCreate(BaseModel):
    set_order: int | None = Field(default=None, gt=0)

    # reps may be 0: a set attempted and failed is worth recording, and is not
    # the same as a set never started.
    reps: int | None = Field(default=None, ge=0)
    weight_kg: Decimal | None = Field(
        default=None, ge=0, max_digits=7, decimal_places=2
    )
    duration_seconds: int | None = Field(default=None, ge=0)
    # Metres, for the loaded carries. Weight stays meaningful alongside it.
    distance_m: Decimal | None = Field(
        default=None, ge=0, max_digits=8, decimal_places=2
    )
    rest_seconds: int | None = Field(default=None, ge=0)
    completed: bool = True

    # The plan's numbers, copied in by the caller. Omitting them is fine - a
    # session need not come from a plan at all.
    target_reps: int | None = Field(default=None, gt=0)
    target_weight_kg: Decimal | None = Field(
        default=None, ge=0, max_digits=7, decimal_places=2
    )
    target_duration_seconds: int | None = Field(default=None, gt=0)
    target_distance_m: Decimal | None = Field(
        default=None, gt=0, max_digits=8, decimal_places=2
    )
    workout_exercise_set_id: int | None = None

    # Filled in only when the set was recorded through the AI camera: which
    # algorithm counted it, and what each rep scored (0, 50 or 100 by depth).
    # A set logged by hand leaves both null.
    ai_algorithm: str | None = Field(default=None, max_length=50)
    ai_rep_scores: list[int] | None = None

    notes: str | None = None

    @field_validator("ai_rep_scores")
    @classmethod
    def _scores_are_milestones(cls, value: list[int] | None) -> list[int] | None:
        """A rep is worth 0, 50 or 100 - the two depth milestones and a miss.

        Rejected rather than clamped: any other number means the client and the
        server disagree about the scoring rule, and silently accepting it would
        put a figure in the history that nothing can explain.
        """
        if value is not None and any(score not in (0, 50, 100) for score in value):
            raise ValueError("each rep score must be 0, 50 or 100")
        return value


class SessionSetUpdate(BaseModel):
    """A correction to a set already recorded.

    The target values are deliberately absent: they are a snapshot of what the
    plan said at the time, not something the user gets to revise afterwards.
    The `ai_*` fields are absent for the same reason - they record how the
    camera judged the set as it happened, which a later correction to the reps
    does not entitle anyone to rewrite.

    Omitting a key keeps its value; sending null clears it.
    """

    reps: int | None = Field(default=None, ge=0)
    weight_kg: Decimal | None = Field(
        default=None, ge=0, max_digits=7, decimal_places=2
    )
    duration_seconds: int | None = Field(default=None, ge=0)
    distance_m: Decimal | None = Field(
        default=None, ge=0, max_digits=8, decimal_places=2
    )
    rest_seconds: int | None = Field(default=None, ge=0)
    completed: bool | None = None
    notes: str | None = None


class SessionSetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_set_id: int
    set_order: int

    reps: int | None
    weight_kg: Decimal | None
    duration_seconds: int | None
    distance_m: Decimal | None = None
    rest_seconds: int | None
    completed: bool

    target_reps: int | None
    target_weight_kg: Decimal | None
    target_duration_seconds: int | None = None
    target_distance_m: Decimal | None = None
    workout_exercise_set_id: int | None

    ai_algorithm: str | None = None
    ai_rep_scores: list[int] | None = None

    notes: str | None

    # reps x weight, computed on read.
    volume_kg: Decimal | None = None
    # Total rep score against the most this set could have scored, 0-100.
    # Null for a set logged by hand. Computed on read, like volume_kg.
    ai_score_percent: Decimal | None = None


# ============================================================
# Session exercises
# ============================================================


class SessionExerciseCreate(BaseModel):
    exercise_id: int
    exercise_order: int | None = Field(default=None, gt=0)
    notes: str | None = None
    # Log an exercise and its sets in one call, the way the screen collects it.
    sets: list[SessionSetCreate] | None = None


class SessionExerciseRead(BaseModel):
    session_exercise_id: int
    exercise_id: int

    # Snapshotted when recorded, so renaming the catalog entry later does not
    # rewrite this session. Null only if the catalog had no name at all.
    exercise_name: str | None
    exercise_order: int
    notes: str | None

    # Resolved live for display; the snapshot above is what the history means.
    thumbnail_url: str | None = None

    # How this exercise is measured, so the client knows which inputs to show:
    # reps and weight for a Bench Press, seconds for a Plank, metres for a
    # carry. Read live from the catalog rather than snapshotted - it describes
    # the movement, not the performance, and correcting it should correct
    # every screen at once.
    tracking_type: str = "reps"

    # Which camera algorithm can score this movement, or null if none can.
    # Read live from the catalog for the same reason `tracking_type` is: it
    # describes the movement, and the client uses it to decide whether to offer
    # the AI camera at all.
    ai_algorithm: str | None = None

    sets: list[SessionSetRead]


# ============================================================
# Sessions
# ============================================================


class WorkoutSessionCreate(BaseModel):
    """Start a workout.

    Pass `workout_day_id` to start from a planned day: the plan and day names
    are snapshotted, and the day's exercises and target sets are copied in as
    a starting point unless `copy_plan` says otherwise.
    """

    workout_day_id: int | None = None
    copy_plan: bool = True
    scheduled_date: date | None = None
    started_at: datetime | None = None
    notes: str | None = None


class WorkoutSessionUpdate(BaseModel):
    notes: str | None = None
    scheduled_date: date | None = None


class WorkoutSessionFinish(BaseModel):
    completed_at: datetime | None = None
    notes: str | None = None
    # A session the user gave up on is still worth keeping - it says something
    # that a deleted row does not.
    abandoned: bool = False


class SessionSummary(BaseModel):
    """The numbers a list row shows, computed rather than stored."""

    exercise_count: int
    set_count: int
    completed_set_count: int
    total_reps: int
    total_volume_kg: Decimal


class WorkoutSessionListItem(BaseModel):
    session_id: int
    workout_day_id: int | None
    plan_name: str | None
    day_name: str | None
    scheduled_date: date | None
    started_at: datetime
    completed_at: datetime | None
    status: str
    # Training time so far, pauses already subtracted. Live while the session
    # is running, so the screen can show it without doing the arithmetic.
    duration_seconds: int | None
    paused_seconds: int = 0
    notes: str | None
    summary: SessionSummary


class WorkoutSessionDetail(BaseModel):
    session_id: int
    workout_day_id: int | None
    plan_name: str | None
    day_name: str | None
    scheduled_date: date | None
    started_at: datetime
    completed_at: datetime | None
    status: str
    # Training time so far, pauses already subtracted. Live while the session
    # is running, so the screen can show it without doing the arithmetic.
    duration_seconds: int | None
    paused_seconds: int = 0
    notes: str | None
    summary: SessionSummary
    exercises: list[SessionExerciseRead]


# ============================================================
# History
# ============================================================


class HistoryTotals(BaseModel):
    session_count: int
    total_duration_seconds: int
    total_sets: int
    total_reps: int
    total_volume_kg: Decimal


class WorkoutHistoryResponse(BaseModel):
    """Completed sessions over a period, with the totals a summary card wants.

    Aggregated on the server because the client would otherwise have to pull
    every set of every session to add them up.
    """

    from_date: date | None = Field(default=None, serialization_alias="from")
    to_date: date | None = Field(default=None, serialization_alias="to")
    totals: HistoryTotals
    items: list[WorkoutSessionListItem]

    model_config = ConfigDict(populate_by_name=True)
