from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.exercises import Exercise


class ExerciseTranslation(Base):
    __tablename__ = "exercise_translations"
    __table_args__ = (
        UniqueConstraint("exercise_id", "language", name="uq_exercise_translation_language"),
    )

    translation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("exercises.exercise_id", ondelete="CASCADE", name="fk_exercise_translations_exercise"),
        nullable=False,
    )

    language: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    overview: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    tips: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    exercise: Mapped["Exercise"] = relationship(back_populates="translations")
