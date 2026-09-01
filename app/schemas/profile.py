from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

from app.schemas.user_profiles import UserRole


class ProfileRead(BaseModel):
    user_id: uuid.UUID
    profile_id: int

    name: str
    avatar_url: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None

    role: UserRole

    # From Supabase Auth, not from user_profiles.
    email: EmailStr | None = None

    # Convenience copy of the most recent body metric, so the profile screen
    # does not need a second request.
    height_cm: Decimal | None = None
    weight_kg: Decimal | None = None

    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseModel):
    """Partial update - omitted fields keep their current value.

    `role` is absent on purpose: raising your own privileges must not be
    something this endpoint can do.
    """

    name: str | None = Field(default=None, max_length=150)
    avatar_url: str | None = None
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=30)
