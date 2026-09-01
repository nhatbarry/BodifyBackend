from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.users import User


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="chk_user_profiles_role"),
    )

    profile_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)

    name: Mapped[str] = mapped_column(String(150), nullable=False)

    avatar_url: Mapped[str | None] = mapped_column(Text)

    date_of_birth: Mapped[date | None] = mapped_column(Date)

    gender: Mapped[str | None] = mapped_column(String(30))

    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default="user")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped["User"] = relationship(
        "User",
        primaryjoin="foreign(UserProfile.user_id) == User.id",
        viewonly=True,
        uselist=False,
    )
