from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.session_exercises import SessionExercise

IN_PROGRESS = "in_progress"
PAUSED = "paused"
COMPLETED = "completed"
ABANDONED = "abandoned"

# A session that is still the user's current workout. Paused belongs here:
# the clock has stopped, the workout has not.
ACTIVE_STATUSES = (IN_PROGRESS, PAUSED)


class WorkoutSession(Base):
    """One actual training session - what was done, not what was planned.

    `plan_name` and `day_name` are snapshots taken when the session starts.
    The plan they came from is a living document: it gets renamed, reordered
    and eventually deleted, and none of that may rewrite what the history says
    about a workout already finished.
    """

    __tablename__ = "workout_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'paused', 'completed', 'abandoned')",
            name="chk_workout_session_status",
        ),
        CheckConstraint("paused_seconds >= 0", name="chk_workout_session_paused"),
        CheckConstraint(
            "(paused_at IS NOT NULL) = (status = 'paused')",
            name="chk_workout_session_pause_state",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="chk_workout_session_times",
        ),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="chk_workout_session_completion",
        ),
        Index(
            "idx_workout_sessions_user_date",
            "user_id",
            "scheduled_date",
            "started_at",
        ),
        Index("idx_workout_sessions_day", "workout_day_id"),
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # SET NULL, never CASCADE: deleting a plan must not erase the record of
    # workouts already performed from it.
    workout_day_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("workout_days.workout_day_id", ondelete="SET NULL"),
    )

    plan_name: Mapped[str | None] = mapped_column(String(150))
    day_name: Mapped[str | None] = mapped_column(String(150))

    scheduled_date: Mapped[date | None] = mapped_column(Date)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # When the current pause began; NULL whenever the clock is running.
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How much has already been sat out, across every earlier pause.
    paused_seconds: Mapped[int] = mapped_column(nullable=False, server_default="0")

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=IN_PROGRESS
    )

    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    exercises: Mapped[list["SessionExercise"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionExercise.exercise_order",
    )

    @property
    def duration_seconds(self) -> int | None:
        """Time actually spent training, in seconds.

        Computed, never stored: a stored duration would drift from its own
        start and end times the moment either is corrected. Pauses are
        subtracted, so this is training time rather than wall-clock time - a
        workout interrupted by a half-hour phone call did not last half an
        hour longer.

        Returns a live figure while the session is still running, which is
        what the "elapsed" display on the workout screen reads.
        """
        end = self.completed_at or datetime.now(timezone.utc)

        paused = self.paused_seconds
        if self.paused_at is not None:
            # Currently paused: the clock froze when the pause started.
            end = min(end, self.paused_at)

        elapsed = int((end - self.started_at).total_seconds()) - paused
        return max(elapsed, 0)
