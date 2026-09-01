import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase_auth.errors import AuthApiError, AuthError
from sqlalchemy.orm import Session

from app.core.supabase_client import get_supabase
from app.db.session import SessionLocal
from app.models.user_profiles import UserProfile

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


@dataclass
class CurrentUser:
    id: uuid.UUID
    email: str | None
    created_at: datetime | None
    profile: UserProfile | None
    role: str


def require_bearer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> HTTPAuthorizationCredentials:
    """The raw token, for routes that hand it to Supabase rather than decode it."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials


BearerCredentialsDep = Annotated[HTTPAuthorizationCredentials, Depends(require_bearer)]


def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> CurrentUser:
    """Resolve the caller from their Supabase access token.

    The token is validated by Supabase itself (GoTrue /user), so this works
    regardless of which signing algorithm the project uses. Cost is one
    network call per request; swap for local JWKS verification if that
    latency ever matters.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        response = get_supabase().auth.get_user(credentials.credentials)
    except (AuthApiError, AuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if response is None or response.user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = response.user
    user_id = uuid.UUID(str(user.id))

    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()

    return CurrentUser(
        id=user_id,
        email=user.email,
        created_at=user.created_at,
        profile=profile,
        # A user without a profile row yet is still just a regular user.
        role=profile.role if profile else "user",
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_admin(current_user: CurrentUserDep) -> CurrentUser:
    """Guard for admin-only routes (the future admin dashboard)."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


AdminUserDep = Annotated[CurrentUser, Depends(require_admin)]
