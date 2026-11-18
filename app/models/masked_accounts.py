from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MaskedAccount(Base):
    """Read-only mapping to the view `masked.accounts` (ddl_pii_masking.sql).

    `auth.users` joined to `user_profiles`, one row per account. Email, name,
    avatar and date of birth are the real values for a superadmin and masked
    for everyone else - the view decides from the database role the request
    runs under, so the admin console reads accounts only through here.
    """

    __tablename__ = "accounts"
    __table_args__ = {"schema": "masked", "info": {"is_view": True}}

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sign_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    banned_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_banned: Mapped[bool] = mapped_column(Boolean)
    is_seeded: Mapped[bool] = mapped_column(Boolean)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean)
    providers: Mapped[list[str]] = mapped_column(ARRAY(Text))
    has_profile: Mapped[bool] = mapped_column(Boolean)
    avatar_key: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    birth_year: Mapped[int | None] = mapped_column(Integer)
    gender: Mapped[str | None] = mapped_column(String)
    pii_masked: Mapped[bool] = mapped_column(Boolean)
