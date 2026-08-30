from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.exercises import Exercise


class ExerciseMedia(Base):
    __tablename__ = "exercise_media"
    __table_args__ = (
        CheckConstraint("media_type IN ('thumbnail', 'video', 'image')", name="chk_exercise_media_type"),
        CheckConstraint("sort_order >= 0", name="chk_exercise_media_sort_order"),
        Index("idx_exercise_media_exercise", "exercise_id"),
    )

    media_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("exercises.exercise_id", ondelete="CASCADE", name="fk_exercise_media_exercise"),
        nullable=False,
    )

    media_type: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    exercise: Mapped["Exercise"] = relationship(back_populates="media")
