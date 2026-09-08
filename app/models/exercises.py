from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.exercise_equipment import ExerciseEquipment
    from app.models.exercise_media import ExerciseMedia
    from app.models.exercise_muscles import ExerciseMuscle
    from app.models.exercise_translations import ExerciseTranslation


class Exercise(Base):
    __tablename__ = "exercises"

    exercise_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    exercise_type: Mapped[str | None] = mapped_column(String(50))
    mechanics: Mapped[str | None] = mapped_column(String(50))
    force_type: Mapped[str | None] = mapped_column(String(50))

    # What the effort is counted in. `exercise_type` cannot answer this: Bench
    # Press and Plank are both 'Strength', but one is reps and the other is
    # seconds, and the app has to pick an input before the user types anything.
    # Weight is orthogonal - a Farmer's Carry is metres *and* 40 kg.
    tracking_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="reps"
    )

    # Which client-side pose-detection algorithm can count and score this
    # movement from the camera, e.g. 'pushup_v1'. NULL means the exercise has
    # no AI support yet, which is the case for all but a handful.
    #
    # Deliberately free text with no CHECK: the vocabulary lives in the Flutter
    # processor factory and grows with every new algorithm, and an unrecognised
    # value already degrades safely there to "not supported".
    ai_algorithm: Mapped[str | None] = mapped_column(String(50))

    experience_level: Mapped[str | None] = mapped_column(String(30))

    overview: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str | None] = mapped_column(Text)
    tips: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    translations: Mapped[list["ExerciseTranslation"]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan"
    )
    exercise_muscles: Mapped[list["ExerciseMuscle"]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan"
    )
    exercise_equipment: Mapped[list["ExerciseEquipment"]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan"
    )
    media: Mapped[list["ExerciseMedia"]] = relationship(
        back_populates="exercise", cascade="all, delete-orphan"
    )
