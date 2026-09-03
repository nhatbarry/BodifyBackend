from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.workout_exercises import WorkoutExercise
    from app.models.workout_plans import WorkoutPlan


class WorkoutDay(Base):
    """One session inside a plan ("Day 1 - Push").

    `day_order` is the position within the plan, NOT a weekday - which weekday
    it lands on is decided by `schedules`.
    """

    __tablename__ = "workout_days"
    __table_args__ = (
        CheckConstraint("day_order > 0", name="chk_workout_day_order"),
        # DEFERRABLE so a reorder can pass through a duplicate order mid
        # transaction. Two separate requests each commit on their own and would
        # still collide, which is why reordering has its own bulk endpoint.
        UniqueConstraint(
            "plan_id",
            "day_order",
            name="uq_workout_day_order",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("idx_workout_days_plan", "plan_id"),
    )

    workout_day_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    plan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workout_plans.plan_id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    day_order: Mapped[int] = mapped_column(nullable=False)

    # A planned rest is a row, not an absence: the calendar has to tell
    # "resting today" apart from "nothing scheduled yet".
    is_rest_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    description: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    plan: Mapped["WorkoutPlan"] = relationship(back_populates="days")
    exercises: Mapped[list["WorkoutExercise"]] = relationship(
        back_populates="day",
        cascade="all, delete-orphan",
        order_by="WorkoutExercise.exercise_order",
    )
