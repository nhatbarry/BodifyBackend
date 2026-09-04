from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    from app.models.session_exercises import SessionExercise


class SessionSet(Base):
    """One set that was actually performed.

    The `target_*` columns are a **copy** of the plan's numbers at the moment
    the set was recorded, not a live reference to them. The plan is edited for
    months afterwards, and changing next week's target must not silently
    rewrite last week's "you aimed for 40, you did 45".

    `workout_exercise_set_id` keeps the link for anyone who wants it, but is
    ON DELETE SET NULL - deleting a plan leaves the history intact and merely
    unlinked.
    """

    __tablename__ = "session_sets"
    __table_args__ = (
        CheckConstraint("set_order > 0", name="chk_session_set_order"),
        CheckConstraint("reps IS NULL OR reps >= 0", name="chk_session_set_reps"),
        CheckConstraint(
            "weight_kg IS NULL OR weight_kg >= 0", name="chk_session_set_weight"
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="chk_session_set_duration",
        ),
        CheckConstraint(
            "rest_seconds IS NULL OR rest_seconds >= 0", name="chk_session_set_rest"
        ),
        CheckConstraint(
            "target_reps IS NULL OR target_reps > 0", name="chk_session_set_target_reps"
        ),
        CheckConstraint(
            "target_weight_kg IS NULL OR target_weight_kg >= 0",
            name="chk_session_set_target_weight",
        ),
        UniqueConstraint(
            "session_exercise_id",
            "set_order",
            name="uq_session_set_order",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("idx_session_sets_exercise", "session_exercise_id"),
    )

    session_set_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    session_exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("session_exercises.session_exercise_id", ondelete="CASCADE"),
        nullable=False,
    )

    set_order: Mapped[int] = mapped_column(nullable=False)

    # Performed. reps may be 0 - a set the user started and failed is a real
    # thing to record, which is why the check is >= 0 rather than > 0.
    reps: Mapped[int | None] = mapped_column()
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    duration_seconds: Mapped[int | None] = mapped_column()
    # Metres. A loaded carry is measured over ground, not in reps or seconds.
    distance_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    rest_seconds: Mapped[int | None] = mapped_column()
    completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    # Planned, snapshotted.
    target_reps: Mapped[int | None] = mapped_column()
    target_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    target_duration_seconds: Mapped[int | None] = mapped_column()
    target_distance_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    workout_exercise_set_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "workout_exercise_sets.workout_exercise_set_id", ondelete="SET NULL"
        ),
    )

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session_exercise: Mapped["SessionExercise"] = relationship(back_populates="sets")

    @property
    def volume_kg(self) -> Decimal | None:
        """reps x weight for this set, the usual unit of training volume."""
        if self.reps is None or self.weight_kg is None:
            return None
        return self.weight_kg * self.reps
