from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.exercises import Exercise
    from app.models.workout_days import WorkoutDay
    from app.models.workout_exercise_sets import WorkoutExerciseSet


class WorkoutExercise(Base):
    """One catalog exercise placed in a workout day, at a given position."""

    __tablename__ = "workout_exercises"
    __table_args__ = (
        CheckConstraint("exercise_order > 0", name="chk_workout_exercise_order"),
        UniqueConstraint(
            "workout_day_id",
            "exercise_order",
            name="uq_workout_exercise_order",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("idx_workout_exercises_day", "workout_day_id"),
        Index("idx_workout_exercises_exercise", "exercise_id"),
    )

    workout_exercise_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    workout_day_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workout_days.workout_day_id", ondelete="CASCADE"),
        nullable=False,
    )

    # RESTRICT: a catalog exercise somebody has already planned must not vanish
    # from under them.
    exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("exercises.exercise_id", ondelete="RESTRICT"),
        nullable=False,
    )

    exercise_order: Mapped[int] = mapped_column(nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    day: Mapped["WorkoutDay"] = relationship(back_populates="exercises")
    exercise: Mapped["Exercise"] = relationship(viewonly=True)
    sets: Mapped[list["WorkoutExerciseSet"]] = relationship(
        back_populates="workout_exercise",
        cascade="all, delete-orphan",
        order_by="WorkoutExerciseSet.set_order",
    )
