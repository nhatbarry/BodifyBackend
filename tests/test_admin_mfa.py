"""Admin console second factor: a code from an authenticator app.

A password alone must not open the console; the app user's routes must not
notice any of this.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time
from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal
from tests.conftest import API

ADMIN_ROUTE = f"{API}/admin/analytics/overview"


def _totp(secret: str, at: float | None = None) -> str:
    """RFC 6238, as Microsoft Authenticator computes it: SHA-1, 30 s, 6 digits."""
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = int((time.time() if at is None else at) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def _make_admin(email: str) -> None:
    """Give the account the whole console: a superadmin holds every permission.

    Narrower admins are made through the roles API (tests/test_admin_roles.py).
    """
    with SessionLocal() as db:
        db.execute(
            text(
                "UPDATE user_profiles SET role = 'superadmin' "
                "WHERE user_id = (SELECT id FROM auth.users WHERE email = :e)"
            ),
            {"e": email},
        )
        db.commit()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, creds: dict) -> dict:
    response = client.post(
        f"{API}/auth/login", json={"email": creds["email"], "password": creds["password"]}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _enroll_and_verify(client: TestClient, headers: dict) -> tuple[str, str, dict]:
    enrolled = client.post(f"{API}/admin/mfa/enroll", headers=headers, json={})
    assert enrolled.status_code == 201, enrolled.text
    body = enrolled.json()
    verified = client.post(
        f"{API}/admin/mfa/verify",
        headers=headers,
        json={"factor_id": body["factor_id"], "code": _totp(body["secret"])},
    )
    assert verified.status_code == 200, verified.text
    return body["factor_id"], body["secret"], verified.json()


def test_a_regular_user_is_refused_before_any_second_factor(
    client: TestClient, account: Callable[[], dict[str, str]]
):
    headers = account()
    for path in (ADMIN_ROUTE, f"{API}/admin/mfa/status"):
        response = client.get(path, headers=headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "Admin privileges required"


def test_an_admin_with_a_password_only_is_told_to_set_up_an_app(
    client: TestClient, credentials: Callable[[], dict]
):
    creds = credentials()
    _make_admin(creds["email"])

    response = client.get(ADMIN_ROUTE, headers=creds["headers"])
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "mfa_enroll_required"

    status_ = client.get(f"{API}/admin/mfa/status", headers=creds["headers"]).json()
    assert status_ == {"enrolled": False, "verified": False, "factors": []}

    # The app's own routes do not ask for a second factor.
    assert client.get(f"{API}/profile", headers=creds["headers"]).status_code == 200


def test_enrolling_and_verifying_opens_the_console(
    client: TestClient, credentials: Callable[[], dict]
):
    creds = credentials()
    _make_admin(creds["email"])
    headers = creds["headers"]

    enrolled = client.post(f"{API}/admin/mfa/enroll", headers=headers, json={"friendly_name": "Phone"})
    assert enrolled.status_code == 201, enrolled.text
    body = enrolled.json()
    assert body["qr_code"].startswith("data:image/svg")
    assert body["uri"].startswith("otpauth://totp/")

    # Scanned but not confirmed: still not set up.
    assert client.get(ADMIN_ROUTE, headers=headers).json()["detail"]["code"] == "mfa_enroll_required"

    good = _totp(body["secret"])
    wrong = f"{(int(good) + 1) % 1_000_000:06d}"
    refused = client.post(
        f"{API}/admin/mfa/verify", headers=headers, json={"factor_id": body["factor_id"], "code": wrong}
    )
    assert refused.status_code == 400
    assert refused.json()["detail"]["code"] == "mfa_code_invalid"

    verified = client.post(
        f"{API}/admin/mfa/verify", headers=headers, json={"factor_id": body["factor_id"], "code": good}
    )
    assert verified.status_code == 200, verified.text
    tokens = verified.json()

    assert client.get(ADMIN_ROUTE, headers=_bearer(tokens["access_token"])).status_code == 200
    status_ = client.get(f"{API}/admin/mfa/status", headers=_bearer(tokens["access_token"])).json()
    assert status_["enrolled"] is True and status_["verified"] is True
    assert [f["friendly_name"] for f in status_["factors"]] == ["Phone"]

    # The password-only token of the same session stays password-only.
    old = client.get(ADMIN_ROUTE, headers=headers)
    assert old.status_code == 403
    assert old.json()["detail"]["code"] == "mfa_verify_required"

    # Refreshing keeps the second factor.
    refreshed = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    assert client.get(ADMIN_ROUTE, headers=_bearer(refreshed.json()["access_token"])).status_code == 200


def test_the_next_sign_in_asks_for_a_code_again(
    client: TestClient, credentials: Callable[[], dict]
):
    creds = credentials()
    _make_admin(creds["email"])
    factor_id, secret, _ = _enroll_and_verify(client, creds["headers"])

    fresh = _bearer(_login(client, creds)["access_token"])
    response = client.get(ADMIN_ROUTE, headers=fresh)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "mfa_verify_required"

    # The next 30 s step: the code just used may not be accepted twice.
    verified = client.post(
        f"{API}/admin/mfa/verify",
        headers=fresh,
        json={"factor_id": factor_id, "code": _totp(secret, time.time() + 30)},
    )
    assert verified.status_code == 200, verified.text
    assert client.get(ADMIN_ROUTE, headers=_bearer(verified.json()["access_token"])).status_code == 200


def test_a_password_alone_cannot_attach_or_remove_an_app(
    client: TestClient, credentials: Callable[[], dict]
):
    """Otherwise a stolen password could add the thief's phone, or take the
    owner's away, and the second factor would protect nothing."""
    creds = credentials()
    _make_admin(creds["email"])
    factor_id, _, _ = _enroll_and_verify(client, creds["headers"])

    fresh = _bearer(_login(client, creds)["access_token"])
    attach = client.post(f"{API}/admin/mfa/enroll", headers=fresh, json={})
    assert attach.status_code == 403
    assert attach.json()["detail"]["code"] == "mfa_verify_required"

    remove = client.delete(f"{API}/admin/mfa/factors/{factor_id}", headers=fresh)
    assert remove.status_code == 403

    status_ = client.get(f"{API}/admin/mfa/status", headers=fresh).json()
    assert status_["enrolled"] is True and len(status_["factors"]) == 1


def test_a_malformed_code_is_refused_before_reaching_gotrue(
    client: TestClient, credentials: Callable[[], dict]
):
    creds = credentials()
    _make_admin(creds["email"])
    response = client.post(
        f"{API}/admin/mfa/verify",
        headers=creds["headers"],
        json={"factor_id": "00000000-0000-0000-0000-000000000000", "code": "12ab56"},
    )
    assert response.status_code == 422
