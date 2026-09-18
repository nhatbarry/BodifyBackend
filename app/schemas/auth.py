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


class OAuthConfigResponse(BaseModel):
    """Which social sign-ins this server can actually complete.

    The app asks before drawing the buttons. A provider that has not been set
    up produces a failure the user can do nothing about, and the app already
    treats "a tap that always fails is worse than no button" as the rule -
    same as the rest-day checkbox that greys out when the API would 409.
    """

    google: bool
    facebook: bool
    # The audience the app must ask Android to sign its ID token for. Null
    # whenever `google` is false.
    google_client_id: str | None = None


class GoogleSignInRequest(BaseModel):
    """The ID token Android's account picker produced."""

    id_token: str = Field(min_length=1)
    # Passed through to Supabase when the app generated one. It ties the token
    # to this particular sign-in attempt, so a token lifted from another one
    # cannot be replayed here.
    nonce: str | None = None


class OAuthUrlResponse(BaseModel):
    """Where to send the browser for a provider that needs one."""

    url: str


class SocialSignInResponse(BaseModel):
    """Signed in through a provider: who it is, and the session to use.

    Same three parts as registering, because that is what the client needs in
    both cases - and unlike registering, the session is never null: a social
    sign-in that produced no session is a failure, not a pending confirmation.
    """

    user: AuthUser
    profile: UserProfileRead
    session: TokenResponse


class ChangePasswordRequest(BaseModel):
    """Changing a password proves you know the old one first.

    A stolen phone with an unlocked app is the case this defends against: an
    access token alone must not be enough to lock the real owner out.
    """

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6, max_length=72)


class ChangeEmailRequest(BaseModel):
    """Same rule as the password: the current one is re-checked first."""

    current_password: str = Field(min_length=1)
    new_email: EmailStr
