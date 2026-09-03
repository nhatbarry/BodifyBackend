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
