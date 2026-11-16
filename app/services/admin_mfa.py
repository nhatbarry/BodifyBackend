"""Authenticator-app second factor for the admin console, through GoTrue.

Supabase Auth already does TOTP (RFC 6238) - storing the secret, checking a
code, refusing one twice - so nothing here is cryptography. This module only
calls GoTrue's `/factors` endpoints *as the user* (their own access token),
because the browser talks to this API and never to Supabase directly.

Any TOTP app works: Microsoft Authenticator, Google Authenticator, Authy.
The QR code GoTrue returns is an ordinary `otpauth://totp/...` URI.

Verifying a code is what turns a password-only session ("aal1") into a
two-factor one ("aal2"): GoTrue answers with a fresh token pair for the same
session, and `require_admin` accepts only those.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

# Shown in the authenticator app as the account's heading.
ISSUER = "Bodify Admin"

_TIMEOUT = httpx.Timeout(15.0)


def _base() -> str:
    return f"{settings.supabase_url.rstrip('/')}/auth/v1"


def _headers(access_token: str) -> dict[str, str]:
    return {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {access_token}",
    }


def _call(method: str, path: str, access_token: str, json: dict | None = None) -> Any:
    try:
        response = httpx.request(
            method,
            f"{_base()}{path}",
            headers=_headers(access_token),
            json=json,
            timeout=_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cannot reach the sign-in service right now. Please try again.",
        ) from exc

    if response.status_code >= 400:
        raise _translate_error(response)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _translate_error(response: httpx.Response) -> HTTPException:
    try:
        body = response.json()
    except ValueError:
        body = {}
    error_code = body.get("error_code") or body.get("code")
    message = body.get("msg") or body.get("message") or body.get("error_description") or "Request failed"

    if error_code == "mfa_verification_failed":
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "mfa_code_invalid", "message": "Wrong code. Check the app and try again."},
        )
    if response.status_code == 429:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "mfa_rate_limited", "message": "Too many attempts. Wait a few minutes."},
        )
    if response.status_code == 404:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Factor not found")
    if response.status_code in (401, 403):
        # GoTrue refusing on assurance level ("AAL2 required ...") or a
        # disabled TOTP setting. Its own sentence is the most useful thing to
        # pass on.
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)
    if response.status_code < 500:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="The sign-in service failed. Please try again.",
    )


def list_factors(access_token: str) -> list[dict]:
    user = _call("GET", "/user", access_token) or {}
    return [f for f in (user.get("factors") or []) if f.get("factor_type") == "totp"]


def enroll(access_token: str, friendly_name: str) -> dict:
    """Start setting up an app. The factor stays unverified until a code is
    verified against it, and an unverified factor does not count anywhere.

    Abandoned set-ups are removed first: each one holds a slot of the
    project's per-user factor limit, and a friendly name must be unique.
    """
    for factor in list_factors(access_token):
        if factor.get("status") != "verified":
            _call("DELETE", f"/factors/{factor['id']}", access_token)

    return _call(
        "POST",
        "/factors",
        access_token,
        json={"factor_type": "totp", "friendly_name": friendly_name, "issuer": ISSUER},
    )


def verify(access_token: str, factor_id: str, code: str) -> dict:
    """Challenge and verify in one step; returns GoTrue's new token pair."""
    challenge = _call("POST", f"/factors/{factor_id}/challenge", access_token, json={})
    return _call(
        "POST",
        f"/factors/{factor_id}/verify",
        access_token,
        json={"challenge_id": challenge["id"], "code": code},
    )


def unenroll(access_token: str, factor_id: str) -> None:
    _call("DELETE", f"/factors/{factor_id}", access_token)
