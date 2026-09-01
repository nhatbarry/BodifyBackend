from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.body_metrics import BodyMetric
    from app.models.user_profiles import UserProfile


class User(Base):
    """Read-only mapping to Supabase Auth's auth.users.

    Supabase Auth owns this table's lifecycle (signup, password reset,
    deletion). Never insert/update/delete through this model - all writes
    go through the Supabase Auth API/SDK. This mapping exists only so the
    backend can SELECT/JOIN auth fields (e.g. email) alongside its own
    tables without an extra call to Supabase's API.
    """

    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped["UserProfile | None"] = relationship(
        "UserProfile",
        primaryjoin="User.id == foreign(UserProfile.user_id)",
        viewonly=True,
        uselist=False,
    )
    body_metrics: Mapped[list["BodyMetric"]] = relationship(
        "BodyMetric",
        primaryjoin="User.id == foreign(BodyMetric.user_id)",
        viewonly=True,
    )
