import uuid
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.jwt_verify import KeysUnavailable, TokenInvalid, verify
from app.db.session import SessionLocal
from app.models.user_profiles import UserProfile
from app.models.users import AuthSession, User

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
    # "aal2" once an authenticator code was verified on this session.
    aal: str = "aal1"


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

    The signature is checked here against the project's published key rather
    than by calling GoTrue, which took one network round trip per request.
    What a signature cannot tell is whether the session has since ended, so
    the same single query that loads the profile also requires the session
    row to still exist: logging out, changing the password or deleting the
    account refuses the token immediately, exactly as GoTrue did.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = verify(credentials.credentials)
    except TokenInvalid as exc:
        raise unauthorized from exc
    except KeysUnavailable as exc:
        # Not the caller's fault, and not a verdict on their token.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot verify sign-in right now. Please try again.",
        ) from exc

    row = db.execute(
        select(User.email, User.created_at, UserProfile)
        .select_from(AuthSession)
        .join(User, User.id == AuthSession.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(
            AuthSession.id == claims.session_id,
            AuthSession.user_id == claims.user_id,
            or_(AuthSession.not_after.is_(None), AuthSession.not_after > func.now()),
        )
    ).first()
    if row is None:
        raise unauthorized

    email, created_at, profile = row
    return CurrentUser(
        id=claims.user_id,
        email=email,
        created_at=created_at,
        profile=profile,
        # A user without a profile row yet is still just a regular user.
        role=profile.role if profile else "user",
        aal=claims.aal,
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_admin_role(current_user: CurrentUserDep) -> CurrentUser:
    """Admin role, signed in with a password only.

    For the second-factor routes alone (`/admin/mfa/*`): they are how a
    password-only session becomes a two-factor one, so they cannot demand
    the second factor themselves. Everything else uses `require_admin`.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


AdminRoleDep = Annotated[CurrentUser, Depends(require_admin_role)]


def has_verified_mfa_factor(db: Session, user_id: uuid.UUID) -> bool:
    """Whether the account has an authenticator app set up and confirmed.

    Read straight from GoTrue's table (read-only, like `auth.users`): it is
    one indexed lookup, against a network round trip to ask GoTrue.
    """
    return bool(
        db.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM auth.mfa_factors "
                "WHERE user_id = :uid AND status = 'verified')"
            ),
            {"uid": user_id},
        ).scalar()
    )


def require_admin(current_user: AdminRoleDep, db: DbSession) -> CurrentUser:
    """Guard for admin routes: admin role *and* an authenticator code.

    A password alone is not enough for the console. The session has to be
    at "aal2", which GoTrue grants only after a code from an authenticator
    app (Microsoft Authenticator, Google Authenticator, ...) was verified
    through `/admin/mfa/verify`. Hiding pages in the browser is navigation,
    never authorization - this is the check.

    The refusal carries a machine-readable `code` so the console knows which
    screen to show: set up an authenticator first, or enter its code.
    """
    if current_user.aal == "aal2":
        return current_user

    if has_verified_mfa_factor(db, current_user.id):
        code, message = "mfa_verify_required", "Enter the code from your authenticator app."
    else:
        code, message = "mfa_enroll_required", "Set up an authenticator app to use the admin console."
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": code, "message": message},
    )


AdminUserDep = Annotated[CurrentUser, Depends(require_admin)]
