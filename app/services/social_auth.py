"""Signing in with Google or Facebook.

Two providers, two different shapes of flow, and the difference is not a
preference - it is what each platform actually hands back:

**Google is native.** The Android account picker returns a signed OIDC ID
token, which Supabase verifies itself. The app sends that token here, this
server exchanges it for a Supabase session, and the app never learns the
project's Supabase URL - the same rule the rest of the app follows.

**Facebook goes through the browser.** Its Android SDK returns an *access*
token, not an OIDC ID token, so `sign_in_with_id_token` cannot take it
(Limited Login, which does return one, is iOS-only). The browser flow is
Supabase's own `/authorize` endpoint, and the app gets its session back on the
deep link Supabase redirects to. The app still asks *this* server for that
URL rather than building it, so the Supabase URL stays a server-side detail
and the redirect target cannot be set by whoever is holding the phone.

The account itself is Supabase's either way: this module never creates an
`auth.users` row, it only makes sure the matching `user_profiles` row exists
once Supabase says who the user is.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user_profiles import UserProfile

# Providers this server will build an authorize URL for. Not every provider
# Supabase supports - only the ones the app has a button for, because the
# redirect URL and the app's own handling have to exist too.
BROWSER_PROVIDERS = frozenset({"facebook", "google"})

# Last resort for a profile name. An account with a blank name renders as an
# empty row everywhere in the app, and `user_profiles.name` is NOT NULL.
FALLBACK_NAME = "Bodify user"


def authorize_url(provider: str, redirect_to: str) -> str:
    """Supabase's hosted OAuth entry point for [provider].

    Built here rather than through `sign_in_with_oauth`: that method clears
    the session on the client object, and ours are `lru_cache` singletons
    shared by every request. A URL is three query parameters; mutating shared
    state to get one is not worth it.
    """
    query = urlencode({"provider": provider, "redirect_to": redirect_to})
    return f"{settings.supabase_url}/auth/v1/authorize?{query}"


def display_name(user) -> str:
    """The name to put on a profile created from a social account.

    Google and Facebook both send something usable in `user_metadata`, but
    which key holds it differs by provider and has changed over time, so each
    is tried before falling back to the email's local part.
    """
    metadata = getattr(user, "user_metadata", None) or {}

    for key in ("full_name", "name", "preferred_username", "user_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:150]

    email = getattr(user, "email", None)
    if email and "@" in email:
        return email.split("@", 1)[0][:150]

    return FALLBACK_NAME


def ensure_profile(db: Session, user) -> UserProfile:
    """Finds this user's profile row, creating it on first social sign-in.

    Email signup creates the profile in the same request that creates the
    account (`POST /auth/register`). A social account is created by Supabase
    during the provider handshake, so there is no such moment - the first time
    we see the user is when they arrive holding a valid session, and the row
    has to be made then.

    Idempotent on purpose: it runs on every social sign-in, not just the first.

    The provider's avatar is deliberately **not** copied. `avatar_url` holds a
    storage key in this schema, not a URL, and pointing it at a remote address
    would hand every viewer of the profile out to a server we do not control -
    the same reason the free-text avatar URL field was removed.
    """
    user_id = uuid.UUID(str(user.id))

    profile = (
        db.query(UserProfile).filter(UserProfile.user_id == user_id).one_or_none()
    )
    if profile is not None:
        return profile

    profile = UserProfile(user_id=user_id, name=display_name(user))
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile
