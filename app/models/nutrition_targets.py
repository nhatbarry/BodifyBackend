from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NutritionTarget(Base):
    """What the user is aiming for in a day.

    One row per user, upserted on the unique key. Deliberately not an
    effective-dated history: a target is a setting somebody changes when their
    goal changes, and every screen compares *today* against it. "What was I
    aiming for in March" would need an `effective_from` column and a real
    decision about what the old totals mean, and no screen asks it.

    Only energy is required. Counting calories and nothing else is the common
    case, and demanding three macros the user has not thought about is how a
    setup screen gets abandoned.
    """

    __tablename__ = "nutrition_targets"
    __table_args__ = (
        CheckConstraint("energy_kcal > 0", name="chk_nutrition_targets_energy"),
        UniqueConstraint("user_id", name="uq_nutrition_targets_user"),
    )

    nutrition_target_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    energy_kcal: Mapped[Decimal] = mapped_column(Numeric(7, 1), nullable=False)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))
    carb_g: Mapped[Decimal | None] = mapped_column(Numeric(7, 1))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
