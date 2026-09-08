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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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

    # How the camera scored this set, if it was recorded with the AI coach.
    #
    # Both are snapshots for the same reason `target_*` are: they say what
    # happened at record time. `ai_algorithm` names the algorithm that did the
    # counting ('pushup_v1'), so re-tuning or renaming one later cannot rewrite
    # how an old set was judged; `ai_rep_scores` holds one 0/50/100 score per
    # rep counted, in order.
    #
    # JSONB rather than a child table: the array is small, written once,
    # displayed as a whole, and never filtered on a single element.
    ai_algorithm: Mapped[str | None] = mapped_column(String(50))
    ai_rep_scores: Mapped[list[int] | None] = mapped_column(JSONB)

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

    @property
    def ai_score_percent(self) -> Decimal | None:
        """How well the camera judged this set, 0-100.

        The score of one rep is 0, 50 or 100 depending on how deep it went, so
        a set's score is its total against the most it could have scored. As a
        percentage rather than a raw total, a 5-rep set and a 20-rep set are
        comparable - a raw sum would just reward doing more reps.

        Computed rather than stored, the same rule as `volume_kg` and BMI: a
        stored copy is a second source of truth that drifts from the array it
        was derived from.
        """
        if not self.ai_rep_scores:
            return None
        earned = Decimal(sum(self.ai_rep_scores))
        possible = Decimal(len(self.ai_rep_scores) * 100)
        return (earned / possible * 100).quantize(Decimal("0.01"))
