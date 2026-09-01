from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Numeric, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.users import User


class BodyMetric(Base):
    __tablename__ = "body_metrics"
    __table_args__ = (
        CheckConstraint("height_cm IS NULL OR height_cm > 0", name="chk_body_metrics_height"),
        CheckConstraint("weight_kg IS NULL OR weight_kg > 0", name="chk_body_metrics_weight"),
        Index("idx_body_metrics_user_date", "user_id", "recorded_at"),
    )

    metric_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship(
        "User",
        primaryjoin="foreign(BodyMetric.user_id) == User.id",
        viewonly=True,
        uselist=False,
    )
