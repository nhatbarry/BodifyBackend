import uuid

from fastapi import APIRouter, HTTPException, status
from supabase_auth.errors import AuthApiError, AuthError

from app.api.dependencies import CurrentUserDep, DbSession
from app.core.supabase_client import get_supabase, get_supabase_admin
from app.models.user_profiles import UserProfile
from app.schemas.auth import (
    AuthUser,
    LoginRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)
from app.schemas.user_profiles import UserProfileRead

router = APIRouter(prefix="/auth", tags=["auth"])


def _to_token_response(session) -> TokenResponse | None:
    if session is None:
        return None
    return TokenResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
        expires_at=session.expires_at,
    )


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> RegisterResponse:
    """Create a Supabase Auth account plus its profile row."""
    try:
        result = get_supabase().auth.sign_up(
            {"email": payload.email, "password": payload.password}
        )
    except (AuthApiError, AuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=getattr(exc, "message", str(exc)),
        ) from exc

    if result.user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signup failed",
        )

    user_id = uuid.UUID(str(result.user.id))

    profile = UserProfile(user_id=user_id, name=payload.name)
    db.add(profile)
    try:
        db.commit()
    except Exception:
        # The auth account already exists at this point; without a profile the
        # user would be half-registered and unable to retry (email taken), so
        # roll the account back too.
        db.rollback()
        try:
            get_supabase_admin().auth.admin.delete_user(str(user_id))
        except Exception:
            pass
        raise

    db.refresh(profile)

    return RegisterResponse(
        user=AuthUser(
            id=user_id,
            email=result.user.email,
            created_at=result.user.created_at,
        ),
        profile=UserProfileRead.model_validate(profile),
        session=_to_token_response(result.session),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    try:
        result = get_supabase().auth.sign_in_with_password(
            {"email": payload.email, "password": payload.password}
        )
    except (AuthApiError, AuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=getattr(exc, "message", "Invalid credentials"),
        ) from exc

    token = _to_token_response(result.session)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    return token


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest) -> TokenResponse:
    try:
        result = get_supabase().auth.refresh_session(payload.refresh_token)
    except (AuthApiError, AuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=getattr(exc, "message", "Invalid refresh token"),
        ) from exc

    token = _to_token_response(result.session)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    return token


@router.get("/me", response_model=MeResponse)
def me(current_user: CurrentUserDep) -> MeResponse:
    return MeResponse(
        user=AuthUser(
            id=current_user.id,
            email=current_user.email,
            created_at=current_user.created_at,
        ),
        profile=(
            UserProfileRead.model_validate(current_user.profile)
            if current_user.profile
            else None
        ),
        role=current_user.role,
    )
