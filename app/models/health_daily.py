from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HealthDaily(Base):
    """One day of phone health data, rolled up on the device.

    Roll-ups, not samples: see ddl_phase_8.sql. Every measure is nullable
    because a phone without a watch has steps and nothing else.
    """

    __tablename__ = "health_daily"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_health_daily_user_day"),)

    health_daily_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)

    steps: Mapped[int | None] = mapped_column(Integer)
    distance_m: Mapped[Decimal | None] = mapped_column(Numeric(9, 1))
    heart_rate_avg: Mapped[int | None] = mapped_column(SmallInteger)
    heart_rate_min: Mapped[int | None] = mapped_column(SmallInteger)
    heart_rate_max: Mapped[int | None] = mapped_column(SmallInteger)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
