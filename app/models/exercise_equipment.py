from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.equipment import Equipment
    from app.models.exercises import Exercise


class ExerciseEquipment(Base):
    __tablename__ = "exercise_equipment"
    __table_args__ = (
        UniqueConstraint("exercise_id", "equipment_id", name="uq_exercise_equipment"),
        Index("idx_exercise_equipment_equipment", "equipment_id"),
    )

    exercise_equipment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("exercises.exercise_id", ondelete="CASCADE", name="fk_exercise_equipment_exercise"),
        nullable=False,
    )
    equipment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("equipment.equipment_id", ondelete="CASCADE", name="fk_exercise_equipment_equipment"),
        nullable=False,
    )

    exercise: Mapped["Exercise"] = relationship(back_populates="exercise_equipment")
    equipment: Mapped["Equipment"] = relationship(back_populates="exercise_equipment")
