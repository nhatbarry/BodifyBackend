from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.user_profiles import UserProfileRead, UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    # GoTrue hashes with bcrypt, which silently truncates past 72 bytes.
    password: str = Field(min_length=6, max_length=72)
    name: str = Field(min_length=1, max_length=150)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int | None = None
    expires_at: int | None = None


class AuthUser(BaseModel):
    """The Supabase Auth account (auth.users), as returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr | None = None
    created_at: datetime | None = None


class RegisterResponse(BaseModel):
    user: AuthUser
    profile: UserProfileRead
    # None when the project requires email confirmation: the account exists
    # but has no session until the user confirms.
    session: TokenResponse | None = None


class MeResponse(BaseModel):
    user: AuthUser
    profile: UserProfileRead | None = None
    role: UserRole
