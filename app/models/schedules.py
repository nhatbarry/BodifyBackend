from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.workout_days import WorkoutDay


class Schedule(Base):
    """The only table that knows about dates.

    `scheduled_date` and `day_of_week` are an exclusive arc: exactly one is
    set. A date means a one-off session; a weekday means it repeats. A row
    carrying both would have no defined meaning.

    Ownership is NOT enforceable here: `user_id` and `workout_day_id` are
    independent columns, so SQL alone lets one user schedule somebody else's
    day. The owner sits two joins away (day -> plan -> user_id). Every write
    must check it in the API - see app/services/workout_access.py.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint(
            "day_of_week IS NULL OR day_of_week BETWEEN 1 AND 7",
            name="chk_schedule_day_of_week",
        ),
        CheckConstraint(
            "num_nonnulls(scheduled_date, day_of_week) = 1", name="chk_schedule_target"
        ),
        Index("idx_schedules_user", "user_id"),
        Index("idx_schedules_date", "scheduled_date"),
        Index("idx_schedules_day_of_week", "day_of_week"),
        Index("idx_schedules_workout_day", "workout_day_id"),
        # Duplicate guards live in the DDL as two partial unique indexes, one
        # per arm of the arc. A single UNIQUE over both columns would let every
        # recurring row through, since NULL never equals NULL.
    )

    schedule_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    workout_day_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workout_days.workout_day_id", ondelete="CASCADE"),
        nullable=False,
    )

    scheduled_date: Mapped[date | None] = mapped_column(Date)
    # ISO weekday: 1 = Monday .. 7 = Sunday, matching date.isoweekday().
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    workout_day: Mapped["WorkoutDay"] = relationship(viewonly=True)
