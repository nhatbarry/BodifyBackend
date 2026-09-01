from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TermTranslation(Base):
    """Translations for muscle groups and equipment.

    One table covers both via an exclusive arc: exactly one of muscle_id /
    equipment_id is set, enforced by chk_term_translations_target. That keeps
    real foreign keys (and their ON DELETE CASCADE) instead of the untyped
    (domain, ref_id) pair a fully generic table would need.
    """

    __tablename__ = "term_translations"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(muscle_id, equipment_id) = 1",
            name="chk_term_translations_target",
        ),
        UniqueConstraint("muscle_id", "language", name="uq_term_translations_muscle"),
        UniqueConstraint(
            "equipment_id", "language", name="uq_term_translations_equipment"
        ),
        Index("idx_term_translations_language", "language"),
    )

    translation_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    muscle_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("muscle_groups.muscle_id", ondelete="CASCADE"),
    )
    equipment_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("equipment.equipment_id", ondelete="CASCADE"),
    )

    language: Mapped[str] = mapped_column(String(10), nullable=False)

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
