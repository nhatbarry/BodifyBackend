from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.exercise_muscles import ExerciseMuscle


class MuscleGroup(Base):
    __tablename__ = "muscle_groups"

    muscle_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("muscle_groups.muscle_id", ondelete="SET NULL", name="fk_muscle_groups_parent"),
    )

    description: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    parent: Mapped["MuscleGroup | None"] = relationship(
        remote_side="MuscleGroup.muscle_id", back_populates="children"
    )
    children: Mapped[list["MuscleGroup"]] = relationship(back_populates="parent")

    exercise_muscles: Mapped[list["ExerciseMuscle"]] = relationship(back_populates="muscle")
