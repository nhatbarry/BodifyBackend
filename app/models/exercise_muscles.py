from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.exercises import Exercise
    from app.models.muscle_groups import MuscleGroup


class ExerciseMuscle(Base):
    __tablename__ = "exercise_muscles"
    __table_args__ = (
        UniqueConstraint("exercise_id", "muscle_id", name="uq_exercise_muscle"),
        CheckConstraint("role IN ('primary', 'secondary')", name="chk_exercise_muscle_role"),
        Index("idx_exercise_muscles_muscle", "muscle_id"),
    )

    exercise_muscle_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("exercises.exercise_id", ondelete="CASCADE", name="fk_exercise_muscles_exercise"),
        nullable=False,
    )
    muscle_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("muscle_groups.muscle_id", ondelete="CASCADE", name="fk_exercise_muscles_muscle"),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="primary")

    exercise: Mapped["Exercise"] = relationship(back_populates="exercise_muscles")
    muscle: Mapped["MuscleGroup"] = relationship(back_populates="exercise_muscles")
