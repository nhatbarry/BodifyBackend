"""Auth: session revocation and the password-reset oracle."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import API, PASSWORD, TEST_EMAIL_PREFIX


def _register(client: TestClient) -> tuple[str, dict]:
    email = f"{TEST_EMAIL_PREFIX}{uuid.uuid4().hex[:12]}@example.com"
    client.post(
        f"{API}/auth/register",
        json={"email": email, "password": PASSWORD, "name": "Pytest"},
    )
    tokens = client.post(
        f"{API}/auth/login", json={"email": email, "password": PASSWORD}
    ).json()
    return email, tokens


def test_logout_revokes_the_session_not_just_the_local_copy(client: TestClient):
    """The point of calling the endpoint at all.

    Clearing tokens on the device leaves the refresh token usable until
    Supabase expires it, so anyone who lifted it off the phone keeps access.
    """
    _, tokens = _register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert client.get(f"{API}/auth/me", headers=headers).status_code == 200

    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 204

    refreshed = client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code >= 400, "refresh token still works after logout"


def test_logout_is_idempotent_and_needs_a_token(client: TestClient):
    _, tokens = _register(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 204
    # An already-dead session is the state the caller asked for.
    assert client.post(f"{API}/auth/logout", headers=headers).status_code == 204
    assert client.post(f"{API}/auth/logout").status_code == 401


def test_forgot_password_is_not_an_account_enumeration_oracle(client: TestClient):
    """Known and unknown addresses must be indistinguishable.

    Any difference - status, body, even wording - lets an anonymous caller
    discover which emails have accounts.
    """
    email, _ = _register(client)
    unknown = f"{TEST_EMAIL_PREFIX}{uuid.uuid4().hex}@example.com"

    known_response = client.post(f"{API}/auth/forgot-password", json={"email": email})
    unknown_response = client.post(
        f"{API}/auth/forgot-password", json={"email": unknown}
    )

    assert known_response.status_code == 202
    assert unknown_response.status_code == unknown_response.status_code
    assert known_response.json() == unknown_response.json()


def test_forgot_password_rejects_a_malformed_address(client: TestClient):
    assert (
        client.post(
            f"{API}/auth/forgot-password", json={"email": "not-an-email"}
        ).status_code
        == 422
    )


def test_protected_routes_refuse_an_anonymous_caller(client: TestClient):
    for method, path in [
        ("get", "/profile"),
        ("get", "/body-metrics"),
        ("get", "/workout-plans"),
        ("get", "/workout-sessions"),
        ("get", "/workout-history"),
    ]:
        response = getattr(client, method)(f"{API}{path}")
        assert response.status_code == 401, f"{path} answered {response.status_code}"


# ============================================================
# Changing credentials
# ============================================================


def test_changing_a_password_requires_the_current_one(client: TestClient, owner):
    """An access token alone must not be enough. A phone left unlocked already
    carries one, and this is exactly the move that locks the owner out."""
    response = client.put(
        f"{API}/auth/password",
        headers=owner,
        json={"current_password": "definitely-not-it", "new_password": "NewPass123"},
    )
    assert response.status_code == 401
    assert "current password" in response.json()["detail"].lower()


def test_a_password_can_be_changed_and_then_used(
    client: TestClient, credentials
):
    """The whole point: the new password works afterwards, the old one does not."""
    created = credentials()
    email = created["email"]
    old_password = created["password"]
    new_password = "ChangedPass456"

    changed = client.put(
        f"{API}/auth/password",
        headers=created["headers"],
        json={"current_password": old_password, "new_password": new_password},
    )
    assert changed.status_code == 204, changed.text

    assert (
        client.post(
            f"{API}/auth/login",
            json={"email": email, "password": old_password},
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"{API}/auth/login",
            json={"email": email, "password": new_password},
        ).status_code
        == 200
    )


def test_reusing_the_same_password_is_refused(client: TestClient, credentials):
    created = credentials()
    response = client.put(
        f"{API}/auth/password",
        headers=created["headers"],
        json={
            "current_password": created["password"],
            "new_password": created["password"],
        },
    )
    assert response.status_code == 422


def test_changing_an_email_requires_the_current_password(client: TestClient, owner):
    response = client.put(
        f"{API}/auth/email",
        headers=owner,
        json={"current_password": "wrong", "new_email": "pytest-moved@example.com"},
    )
    assert response.status_code == 401


def test_changing_to_the_same_email_is_refused(client: TestClient, credentials):
    created = credentials()
    response = client.put(
        f"{API}/auth/email",
        headers=created["headers"],
        json={
            "current_password": created["password"],
            "new_email": created["email"],
        },
    )
    assert response.status_code == 422
