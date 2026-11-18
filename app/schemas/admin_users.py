"""Admin console: accounts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.admin_roles import AdminRoleRef

Role = Literal["user", "admin", "superadmin"]


class AdminUserSummary(BaseModel):
    user_id: uuid.UUID
    email: str | None
    name: str | None
    role: Role
    created_at: datetime | None
    last_sign_in_at: datetime | None
    banned_until: datetime | None
    is_banned: bool
    is_seeded: bool
    mfa_enabled: bool
    # True when email and name above are masked (anyone but a superadmin).
    pii_masked: bool = False
    # The roles an admin holds; empty for users and superadmins.
    admin_roles: list[AdminRoleRef] = []


class AdminUserActivity(BaseModel):
    workouts_completed: int
    meal_items: int
    body_metrics: int
    coach_questions: int
    last_workout_at: datetime | None


class AdminUserDetail(AdminUserSummary):
    avatar_url: str | None
    # Null when masked; the year is always given.
    date_of_birth: date | None
    birth_year: int | None
    gender: str | None
    has_profile: bool
    providers: list[str]
    activity: AdminUserActivity


class RoleUpdate(BaseModel):
    """Superadmin, or back to a regular user. Making someone an admin is
    giving them a role: `PUT /admin/users/{id}/admin-roles`."""

    role: Literal["user", "superadmin"]


class BanRequest(BaseModel):
    # Hours; omitted means until an admin unlocks it (100 years).
    hours: int | None = Field(default=None, ge=1, le=876000)
