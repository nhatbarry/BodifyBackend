from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
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
    from app.models.exercises import Exercise
    from app.models.session_sets import SessionSet
    from app.models.workout_sessions import WorkoutSession


class SessionExercise(Base):
    """One catalog exercise as performed inside a session.

    `exercise_name` is a snapshot. Names live only in `exercise_translations`
    and can be corrected at any time; a session recorded in March should keep
    saying what it said in March. `exercise_id` is kept beside it so progress
    over time is still a single join.
    """

    __tablename__ = "session_exercises"
    __table_args__ = (
        CheckConstraint("exercise_order > 0", name="chk_session_exercise_order"),
        UniqueConstraint(
            "session_id",
            "exercise_order",
            name="uq_session_exercise_order",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("idx_session_exercises_session", "session_id"),
        Index("idx_session_exercises_exercise", "exercise_id"),
    )

    session_exercise_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workout_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )

    exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("exercises.exercise_id", ondelete="RESTRICT"),
        nullable=False,
    )
    exercise_name: Mapped[str | None] = mapped_column(String(200))

    exercise_order: Mapped[int] = mapped_column(nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped["WorkoutSession"] = relationship(back_populates="exercises")
    exercise: Mapped["Exercise"] = relationship(viewonly=True)
    sets: Mapped[list["SessionSet"]] = relationship(
        back_populates="session_exercise",
        cascade="all, delete-orphan",
        order_by="SessionSet.set_order",
    )
