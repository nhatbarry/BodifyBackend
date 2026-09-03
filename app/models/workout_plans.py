from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.workout_days import WorkoutDay


class WorkoutPlan(Base):
    """A training template ("PPL Beginner"), not workout history.

    Nothing here knows about dates - a plan is scheduled by pointing a
    `schedules` row at one of its days.
    """

    __tablename__ = "workout_plans"
    __table_args__ = (
        CheckConstraint(
            "duration_weeks IS NULL OR duration_weeks > 0",
            name="chk_workout_plan_duration",
        ),
        CheckConstraint(
            "status IN ('active', 'archived')", name="chk_workout_plan_status"
        ),
        Index("idx_workout_plans_user", "user_id"),
    )

    plan_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # UUID with no FK: there is no public.users table, auth.users belongs to
    # Supabase Auth. Same shape as body_metrics and user_profiles.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    goal: Mapped[str | None] = mapped_column(String(50))
    duration_weeks: Mapped[int | None] = mapped_column()

    # DELETE removes the plan outright; archiving is PUT status='archived'.
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")

    # "The plan I am following right now" - a different question from status,
    # which only says whether a plan is still relevant. At most one row per
    # user may be true; a partial unique index enforces it, not this column.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    days: Mapped[list["WorkoutDay"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="WorkoutDay.day_order",
    )
