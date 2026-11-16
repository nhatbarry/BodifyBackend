"""Second factor for the admin console: a code from an authenticator app.

The console signs in with `/auth/login` as everyone does. That session is
"aal1" and every other admin route refuses it (`require_admin`), naming what
is missing:

- `mfa_enroll_required` - no app set up yet: `POST /admin/mfa/enroll`, scan
  the QR code, then `POST /admin/mfa/verify` with the first code.
- `mfa_verify_required` - app already set up: `POST /admin/mfa/verify`.

Verify answers with a new token pair ("aal2") for the same session, which
the console stores in place of the old one. Refreshing keeps "aal2"; logging
out ends it, so the next sign-in asks for a code again.

These routes require the admin role but not the second factor - they are the
way to obtain it.
"""

from __future__ import annotations

import base64
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import (
    AdminRoleDep,
    BearerCredentialsDep,
    DbSession,
    has_verified_mfa_factor,
)
from app.schemas.admin_mfa import (
    MfaEnrollRequest,
    MfaEnrollResponse,
    MfaFactorRead,
    MfaStatusRead,
    MfaVerifyRequest,
)
from app.schemas.auth import TokenResponse
from app.services import admin_mfa

router = APIRouter(prefix="/admin/mfa", tags=["admin-mfa"])

_VIETNAM = timezone(timedelta(hours=7))


def _as_data_uri(qr_code: str) -> str:
    # GoTrue hands back the raw SVG document (supabase-js wraps it on the
    # client). A data URI drops straight into <img src> with nothing to
    # sanitise, which inlining someone else's SVG markup would need.
    if qr_code.startswith("data:"):
        return qr_code
    encoded = base64.b64encode(qr_code.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


@router.get("/status", response_model=MfaStatusRead)
def mfa_status(admin: AdminRoleDep, credentials: BearerCredentialsDep) -> MfaStatusRead:
    factors = [
        f for f in admin_mfa.list_factors(credentials.credentials) if f.get("status") == "verified"
    ]
    return MfaStatusRead(
        enrolled=bool(factors),
        verified=admin.aal == "aal2",
        factors=[
            MfaFactorRead(id=f["id"], friendly_name=f.get("friendly_name"), created_at=f.get("created_at"))
            for f in factors
        ],
    )


@router.post("/enroll", response_model=MfaEnrollResponse, status_code=status.HTTP_201_CREATED)
def enroll(
    payload: MfaEnrollRequest,
    admin: AdminRoleDep,
    credentials: BearerCredentialsDep,
    db: DbSession,
) -> MfaEnrollResponse:
    # Once an app is set up, adding another needs a code from it. Otherwise
    # somebody holding only the password could attach their own phone and
    # walk straight past the second factor. GoTrue refuses this too; checking
    # here gives the console the same `code` as everywhere else.
    if admin.aal != "aal2" and has_verified_mfa_factor(db, admin.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "mfa_verify_required",
                "message": "Enter a code from your current authenticator app first.",
            },
        )

    name = payload.friendly_name or f"Authenticator {datetime.now(_VIETNAM):%Y-%m-%d %H:%M:%S}"
    result = admin_mfa.enroll(credentials.credentials, name)
    totp = result.get("totp") or {}
    return MfaEnrollResponse(
        factor_id=result["id"],
        qr_code=_as_data_uri(totp["qr_code"]),
        secret=totp["secret"],
        uri=totp["uri"],
    )


@router.post("/verify", response_model=TokenResponse)
def verify(
    payload: MfaVerifyRequest,
    _: AdminRoleDep,
    credentials: BearerCredentialsDep,
) -> TokenResponse:
    """Check a 6-digit code. Confirms a new app and signs in with an existing
    one alike. Returns the session's new tokens; the old access token stays
    "aal1" and should be thrown away."""
    session = admin_mfa.verify(credentials.credentials, str(payload.factor_id), payload.code)
    expires_in = session.get("expires_in")
    expires_at = session.get("expires_at") or (int(time.time()) + expires_in if expires_in else None)
    return TokenResponse(
        access_token=session["access_token"],
        refresh_token=session["refresh_token"],
        expires_in=expires_in,
        expires_at=expires_at,
    )


@router.delete("/factors/{factor_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_factor(
    factor_id: uuid.UUID,
    admin: AdminRoleDep,
    credentials: BearerCredentialsDep,
) -> Response:
    """Remove an app, e.g. a phone that was replaced. Needs a session that
    already passed the code check."""
    if admin.aal != "aal2":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "mfa_verify_required", "message": "Enter a code from your authenticator app first."},
        )
    admin_mfa.unenroll(credentials.credentials, str(factor_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
