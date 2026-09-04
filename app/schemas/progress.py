"""Phase 4 response shapes: progress, personal records, achievements."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


# ============================================================
# Progress
# ============================================================


class ProgressSummaryRead(BaseModel):
    """The headline figures for a period.

    Percentages are nullable rather than zero. Nobody has completed 0% of a
    plan they never had: with no targeted sets in the period the question does
    not apply, and a 0 on the screen would read as failure instead of absence.
    """

    planned_sets: int
    completed_planned_sets: int
    completion_percent: float | None
    performance_percent: float | None

    total_volume_kg: float
    session_count: int
    workouts_per_week: float | None
    days_span: int | None


class ProgressPointRead(BaseModel):
    """One training day for one exercise - the best set of that day."""

    day: date
    best_weight_kg: float | None
    best_reps: int | None
    best_duration_seconds: int | None
    best_distance_m: float | None
    volume_kg: float


class ExerciseProgressionRead(BaseModel):
    exercise_id: int
    name: str | None
    tracking_type: str
    points: list[ProgressPointRead]


# ============================================================
# Personal records
# ============================================================


class PersonalBestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    exercise_id: int
    name: str | None = None
    record_type: str
    value: Decimal
    unit: str
    achieved_at: datetime


class PersonalRecordRead(BaseModel):
    """A point on a record's history: 40 -> 45 -> 50 kg."""

    model_config = ConfigDict(from_attributes=True)

    pr_id: int
    exercise_id: int
    record_type: str
    value: Decimal
    unit: str
    achieved_at: datetime


# ============================================================
# Achievements
# ============================================================


class AchievementRead(BaseModel):
    """A catalog entry plus this user's standing against it.

    Locked entries are returned too, with their progress: a badge you can see
    yourself approaching is the reason to come back, and hiding it until it
    unlocks throws that away.

    `icon_url` is null in Phase 4 - the client maps `code` to a bundled asset,
    so renaming an icon file is not a database migration.
    """

    model_config = ConfigDict(from_attributes=True)

    achievement_id: int
    code: str
    name: str
    description: str | None
    icon_url: str | None

    achievement_type: str
    requirement_value: Decimal
    requirement_unit: str
    exercise_id: int | None

    unlocked: bool
    achieved_at: datetime | None = None
    # Where the user stands now, in the same unit as `requirement_value`.
    current_value: float | None = None
