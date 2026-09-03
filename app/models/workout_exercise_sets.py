from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.workout_exercises import WorkoutExercise


class WorkoutExerciseSet(Base):
    """One *planned* set - a target, not something that was performed.

    "10 / 10 / 8 reps" is three rows, each free to carry its own weight and
    rest. Actual reps and weight belong to Phase 3 (`workout_sessions`).
    """

    __tablename__ = "workout_exercise_sets"
    __table_args__ = (
        CheckConstraint("set_order > 0", name="chk_workout_set_order"),
        CheckConstraint("target_reps IS NULL OR target_reps > 0", name="chk_target_reps"),
        CheckConstraint(
            "target_weight_kg IS NULL OR target_weight_kg >= 0", name="chk_target_weight"
        ),
        CheckConstraint(
            "rest_seconds IS NULL OR rest_seconds >= 0", name="chk_rest_seconds"
        ),
        UniqueConstraint(
            "workout_exercise_id",
            "set_order",
            name="uq_workout_set_order",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("idx_workout_sets_exercise", "workout_exercise_id"),
    )

    workout_exercise_set_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    workout_exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workout_exercises.workout_exercise_id", ondelete="CASCADE"),
        nullable=False,
    )

    set_order: Mapped[int] = mapped_column(nullable=False)

    target_reps: Mapped[int | None] = mapped_column()
    target_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    rest_seconds: Mapped[int | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workout_exercise: Mapped["WorkoutExercise"] = relationship(back_populates="sets")
