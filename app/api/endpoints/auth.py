import uuid

from fastapi import APIRouter, HTTPException, Response, status
from supabase_auth.errors import AuthApiError, AuthError

from app.api.dependencies import BearerCredentialsDep, CurrentUserDep, DbSession
from app.core.config import settings
from app.core.supabase_client import get_supabase, get_supabase_admin
from app.models.user_profiles import UserProfile
from app.schemas.auth import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    AuthUser,
    ForgotPasswordRequest,
    GoogleSignInRequest,
    LoginRequest,
    MeResponse,
    OAuthConfigResponse,
    OAuthUrlResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    SocialSignInResponse,
    TokenResponse,
)
from app.schemas.user_profiles import UserProfileRead
from app.services import social_auth

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


# ============================================================
# Social sign-in
# ============================================================
#
# Google and Facebook arrive by different routes - see
# app/services/social_auth.py for why - but both end in the same place: a
# Supabase session, and a `user_profiles` row that exists whether or not this
# is the user's first time.


@router.get("/oauth/config", response_model=OAuthConfigResponse)
def oauth_config() -> OAuthConfigResponse:
    """What the app is allowed to offer.

    Unauthenticated on purpose: it is asked on the login screen, before there
    is anybody to authenticate, and it reveals nothing a client could not
    learn by pressing the buttons.
    """
    return OAuthConfigResponse(
        google=settings.can_use_google_oauth,
        facebook=settings.facebook_oauth_enabled,
        google_client_id=settings.google_oauth_client_id,
    )


@router.post("/oauth/google", response_model=SocialSignInResponse)
def sign_in_with_google(
    payload: GoogleSignInRequest, db: DbSession
) -> SocialSignInResponse:
    """Exchange Android's Google ID token for a Supabase session.

    The token is not decoded here. Supabase verifies its signature, issuer and
    audience against the Google provider configured on the project, which is
    the check that matters - a token this server merely parsed would prove
    nothing.
    """
    if not settings.can_use_google_oauth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured on this server",
        )

    credentials: dict[str, str] = {
        "provider": "google",
        "token": payload.id_token,
    }
    if payload.nonce:
        credentials["nonce"] = payload.nonce

    try:
        result = get_supabase().auth.sign_in_with_id_token(credentials)
    except (AuthApiError, AuthError) as exc:
        # 401, not 400: the credential offered was rejected, which is an
        # authentication answer - the same shape /login gives.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=getattr(exc, "message", "Google sign-in was rejected"),
        ) from exc

    return _social_response(db, result)


@router.get("/oauth/{provider}/url", response_model=OAuthUrlResponse)
def oauth_url(provider: str) -> OAuthUrlResponse:
    """Where to point the browser for a provider that needs one.

    The app asks rather than building this itself, so the Supabase project URL
    stays server-side and the redirect target is fixed by configuration
    instead of by whoever is holding the phone.
    """
    if provider not in social_auth.BROWSER_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown sign-in provider",
        )
    if provider == "facebook" and not settings.facebook_oauth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Facebook sign-in is not configured on this server",
        )

    return OAuthUrlResponse(
        url=social_auth.authorize_url(provider, settings.oauth_redirect_url)
    )


@router.post("/oauth/complete", response_model=MeResponse)
def complete_oauth_sign_in(
    credentials: BearerCredentialsDep, db: DbSession
) -> MeResponse:
    """Finish a browser sign-in: make sure the profile row exists.

    The browser flow hands the session straight to the app, so no request ever
    passed through this server while the account was being created. Without
    this call a Facebook user would reach the app with a Supabase account and
    no profile - nameless on every screen that reads one.

    Takes the new session as its bearer token, so the account it creates a
    profile for is the one Supabase just authenticated, never one named in a
    request body.
    """
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
    profile = social_auth.ensure_profile(db, user)

    return MeResponse(
        user=AuthUser(
            id=profile.user_id, email=user.email, created_at=user.created_at
        ),
        profile=UserProfileRead.model_validate(profile),
        role=profile.role,
    )


def _social_response(db: DbSession, result) -> SocialSignInResponse:
    """The common tail of a social sign-in this server performed itself."""
    token = _to_token_response(result.session)
    if result.user is None or token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The provider did not return a session",
        )

    profile = social_auth.ensure_profile(db, result.user)

    return SocialSignInResponse(
        user=AuthUser(
            id=profile.user_id,
            email=result.user.email,
            created_at=result.user.created_at,
        ),
        profile=UserProfileRead.model_validate(profile),
        session=token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(credentials: BearerCredentialsDep) -> Response:
    """Revoke the caller's Supabase session.

    The client must still drop its stored tokens: the access token stays
    cryptographically valid until it expires.
    """
    try:
        # Revoking someone's session is an admin action: the publishable key
        # cannot do it, so this goes through the service_role client.
        get_supabase_admin().auth.admin.sign_out(credentials.credentials)
    except (AuthApiError, AuthError):
        # An already-invalid session is the state the caller asked for.
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(payload: ForgotPasswordRequest) -> dict[str, str]:
    """Send a password reset email.

    Always answers the same way: telling an anonymous caller whether an address
    is registered would turn this into an account-enumeration oracle.
    """
    try:
        get_supabase().auth.reset_password_email(payload.email)
    except (AuthApiError, AuthError):
        pass
    return {"message": "If that email is registered, a reset link has been sent."}


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


def _reauthenticate(email: str | None, password: str) -> None:
    """Prove the caller knows the current password before changing anything.

    An access token is not enough on its own. A phone left unlocked already
    carries one, and "change the password" is exactly the move that locks the
    real owner out of their own account.

    The failure is reported as 401 rather than 403: the credential offered was
    wrong, which is an authentication answer.
    """
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account has no email address to re-authenticate with",
        )
    try:
        get_supabase().auth.sign_in_with_password(
            {"email": email, "password": password}
        )
    except (AuthApiError, AuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        ) from exc


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    current_user: CurrentUserDep,
    credentials: BearerCredentialsDep,
) -> Response:
    """Set a new password, having checked the old one.

    GoTrue owns password hashing, so this goes through the admin client rather
    than touching any table - the same reason `app/models/users.py` maps
    `auth.users` read-only.
    """
    _reauthenticate(current_user.email, payload.current_password)

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The new password is the same as the current one",
        )

    try:
        get_supabase_admin().auth.admin.update_user_by_id(
            str(current_user.id), {"password": payload.new_password}
        )
    except (AuthApiError, AuthError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=getattr(exc, "message", "Could not change the password"),
        ) from exc

    # Every other device keeps a refresh token that still works, and a password
    # change is usually somebody trying to end exactly that. Signing the
    # sessions out is the point of changing it.
    try:
        get_supabase_admin().auth.admin.sign_out(credentials.credentials)
    except (AuthApiError, AuthError):
        # The password did change; failing the request now would tell the user
        # the opposite of what happened.
        pass

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/email", status_code=status.HTTP_204_NO_CONTENT)
def change_email(
    payload: ChangeEmailRequest,
    current_user: CurrentUserDep,
) -> Response:
    """Change the address the account signs in with.

    Whether the new address has to be confirmed before it takes effect is a
    Supabase project setting, not something this endpoint decides - so the
    response deliberately promises nothing about when the change lands.
    """
    _reauthenticate(current_user.email, payload.current_password)

    if payload.new_email.lower() == (current_user.email or "").lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That is already the address on this account",
        )

    try:
        get_supabase_admin().auth.admin.update_user_by_id(
            str(current_user.id), {"email": payload.new_email}
        )
    except (AuthApiError, AuthError) as exc:
        message = getattr(exc, "message", "Could not change the email")
        # A duplicate address is the one failure worth naming precisely; the
        # rest are passed through as GoTrue phrased them.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT
            if "already" in message.lower()
            else status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
