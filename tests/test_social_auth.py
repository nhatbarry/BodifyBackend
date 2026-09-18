"""Signing in with Google or Facebook.

The provider handshake itself cannot be tested here - it needs a real Google
account and a real browser - so these cover the parts that are ours: what the
app is told it may offer, what happens when a provider is not configured, and
the profile row that has to exist by the time a social user reaches the app.

That last one is the bug this file exists to prevent. A social account is
created by Supabase during the handshake, not by `POST /auth/register`, so
nothing in this codebase runs at the moment the account appears - and a user
with no `user_profiles` row is nameless on every screen that reads one.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.user_profiles import UserProfile
from app.services import social_auth
from tests.conftest import API


class _FakeUser:
    """Stands in for the Supabase user object the provider flow returns."""

    def __init__(self, metadata: dict | None = None, email: str | None = None):
        self.id = uuid.uuid4()
        self.email = email
        self.user_metadata = metadata or {}


# ============================================================
# What the app is allowed to offer
# ============================================================


def test_config_reports_what_the_server_can_actually_do(client: TestClient):
    """The app draws its buttons from this, so it must never claim a provider
    that would fail the moment somebody tapped it."""
    response = client.get(f"{API}/auth/oauth/config")
    assert response.status_code == 200

    body = response.json()
    assert body["google"] is settings.can_use_google_oauth
    assert body["facebook"] is settings.facebook_oauth_enabled


def test_config_needs_no_token(client: TestClient):
    """It is read on the login screen, where nobody is signed in yet."""
    assert client.get(f"{API}/auth/oauth/config").status_code == 200


def test_the_google_client_id_is_null_when_google_is_off(client: TestClient):
    """A client id with google=false would send the app looking for an
    audience Supabase is not configured to accept."""
    body = client.get(f"{API}/auth/oauth/config").json()
    if not body["google"]:
        assert body["google_client_id"] is None


# ============================================================
# A provider that is not set up
# ============================================================


def test_an_unknown_provider_is_404(client: TestClient):
    assert client.get(f"{API}/auth/oauth/tiktok/url").status_code == 404


def test_google_sign_in_says_so_when_unconfigured(
    client: TestClient, monkeypatch
):
    """503 rather than a confusing 401: nothing is wrong with the request, and
    the user cannot fix it by trying a different account."""
    monkeypatch.setattr(settings, "google_oauth_client_id", None)

    response = client.post(
        f"{API}/auth/oauth/google", json={"id_token": "whatever"}
    )
    assert response.status_code == 503


def test_facebook_url_says_so_when_unconfigured(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "facebook_oauth_enabled", False)
    assert client.get(f"{API}/auth/oauth/facebook/url").status_code == 503


def test_a_configured_provider_returns_supabase_authorize_url(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "facebook_oauth_enabled", True)

    url = client.get(f"{API}/auth/oauth/facebook/url").json()["url"]

    assert url.startswith(f"{settings.supabase_url}/auth/v1/authorize")
    assert "provider=facebook" in url
    # The redirect target comes from this server's configuration, never from
    # the request - otherwise anyone could point the tokens somewhere else.
    assert "redirect_to=" in url


def test_the_redirect_target_cannot_be_chosen_by_the_caller(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "facebook_oauth_enabled", True)

    url = client.get(
        f"{API}/auth/oauth/facebook/url?redirect_to=https://evil.example.com"
    ).json()["url"]

    assert "evil.example.com" not in url


# ============================================================
# The profile row
# ============================================================


def test_a_social_account_gets_a_profile_on_first_sign_in(db_session):
    user = _FakeUser({"full_name": "Nguyễn Văn A"}, email="a@example.com")

    profile = social_auth.ensure_profile(db_session, user)

    assert profile.name == "Nguyễn Văn A"
    assert profile.user_id == user.id

    db_session.delete(profile)
    db_session.commit()


def test_signing_in_again_reuses_the_same_profile(db_session):
    """It runs on every social sign-in, not only the first, so a second visit
    must not raise on the unique constraint or make a duplicate."""
    user = _FakeUser({"name": "Repeat Visitor"})

    first = social_auth.ensure_profile(db_session, user)
    second = social_auth.ensure_profile(db_session, user)

    assert first.profile_id == second.profile_id

    rows = (
        db_session.query(UserProfile)
        .filter(UserProfile.user_id == user.id)
        .count()
    )
    assert rows == 1

    db_session.delete(first)
    db_session.commit()


def test_a_renamed_google_account_does_not_rewrite_the_profile(db_session):
    """The user may have edited their name in Bodify. Google is where the
    account came from, not the authority on what it is called afterwards."""
    user = _FakeUser({"full_name": "Original Name"})
    profile = social_auth.ensure_profile(db_session, user)

    profile.name = "What The User Chose"
    db_session.commit()

    user.user_metadata["full_name"] = "Renamed In Google"
    again = social_auth.ensure_profile(db_session, user)

    assert again.name == "What The User Chose"

    db_session.delete(profile)
    db_session.commit()


def test_the_providers_avatar_is_not_copied(db_session):
    """`avatar_url` holds a storage key in this schema, not a URL. Copying
    Google's would make every viewer of the profile fetch from a server we do
    not control - the same reason the free-text avatar field was removed."""
    user = _FakeUser(
        {"full_name": "Has A Picture", "avatar_url": "https://lh3.google.com/x"}
    )

    profile = social_auth.ensure_profile(db_session, user)

    assert profile.avatar_url is None

    db_session.delete(profile)
    db_session.commit()


# ============================================================
# Naming an account that arrived without one
# ============================================================


@pytest.mark.parametrize(
    "metadata, email, expected",
    [
        ({"full_name": "Full Name"}, None, "Full Name"),
        ({"name": "Just Name"}, None, "Just Name"),
        ({"preferred_username": "handle"}, None, "handle"),
        # No usable metadata: the email's local part beats a placeholder.
        ({}, "someone@example.com", "someone"),
        # Nothing at all still has to produce a name - the column is NOT NULL.
        ({}, None, social_auth.FALLBACK_NAME),
        # Whitespace is not a name.
        ({"full_name": "   "}, "fallback@example.com", "fallback"),
    ],
)
def test_display_name_prefers_what_the_provider_sent(metadata, email, expected):
    assert social_auth.display_name(_FakeUser(metadata, email)) == expected


def test_a_very_long_name_is_cut_to_fit_the_column(db_session):
    """`user_profiles.name` is VARCHAR(150); a longer one would raise on
    insert, turning somebody's first sign-in into a 500."""
    user = _FakeUser({"full_name": "x" * 400})

    profile = social_auth.ensure_profile(db_session, user)

    assert len(profile.name) == 150

    db_session.delete(profile)
    db_session.commit()


# ============================================================
# Completing a browser sign-in
# ============================================================


def test_complete_needs_a_token(client: TestClient):
    """It creates a profile for whoever the token says - so without one there
    is nobody to create it for."""
    assert client.post(f"{API}/auth/oauth/complete").status_code == 401


def test_complete_rejects_a_bad_token(client: TestClient):
    response = client.post(
        f"{API}/auth/oauth/complete",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_complete_is_idempotent_for_an_existing_user(
    client: TestClient, owner
):
    """An email user already has a profile; calling this must return it rather
    than fail on the unique constraint."""
    first = client.post(f"{API}/auth/oauth/complete", headers=owner)
    second = client.post(f"{API}/auth/oauth/complete", headers=owner)

    assert first.status_code == 200
    assert second.status_code == 200
    assert (
        first.json()["profile"]["profile_id"]
        == second.json()["profile"]["profile_id"]
    )
