"""Checking a Supabase access token without asking Supabase.

The project signs tokens with an asymmetric key (ES256) and publishes the
public half at /auth/v1/.well-known/jwks.json, so the signature, expiry,
audience and issuer can all be checked here. That replaces a round trip to
GoTrue on every authenticated request - measured at the single largest fixed
cost of a request once the database was close by.

A valid signature is not the whole answer: it cannot know the session was
ended. `get_current_user` pairs this with a look at auth.sessions, which keeps
logout and password changes taking effect immediately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt

from app.core.config import settings

# Tokens this project issues. Listing them pins the check: a token claiming
# "none" or an HMAC algorithm is refused rather than trusted.
_ALGORITHMS = ["ES256", "RS256"]
_AUDIENCE = "authenticated"


class TokenInvalid(Exception):
    """The token is not one this project issued, or it has expired."""


class KeysUnavailable(Exception):
    """The signing keys could not be fetched, so nothing can be decided."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID


def _auth_base() -> str:
    return f"{settings.supabase_url.rstrip('/')}/auth/v1"


@lru_cache(maxsize=1)
def _jwks_client() -> jwt.PyJWKClient:
    # Keys are cached for an hour. An unknown `kid` makes PyJWKClient refetch
    # once, so a key rotation is picked up without waiting for the hour.
    return jwt.PyJWKClient(
        f"{_auth_base()}/.well-known/jwks.json",
        cache_keys=True,
        lifespan=3600,
        timeout=10,
    )


def verify(token: str) -> TokenClaims:
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
    except jwt.PyJWKClientConnectionError as exc:
        raise KeysUnavailable(str(exc)) from exc
    except (jwt.PyJWKClientError, jwt.PyJWTError) as exc:
        raise TokenInvalid(str(exc)) from exc

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=_ALGORITHMS,
            audience=_AUDIENCE,
            issuer=_auth_base(),
            options={"require": ["exp", "sub", "session_id"]},
        )
        return TokenClaims(
            user_id=uuid.UUID(claims["sub"]),
            session_id=uuid.UUID(claims["session_id"]),
        )
    except (jwt.PyJWTError, ValueError) as exc:
        raise TokenInvalid(str(exc)) from exc
