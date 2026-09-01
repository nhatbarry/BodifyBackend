from functools import lru_cache

from supabase import Client, ClientOptions, create_client

from app.core.config import settings

# persist_session/auto_refresh_token are off: this backend is stateless and
# every call passes its token explicitly, so the client must never keep a
# session in memory (a shared client would otherwise leak one request's
# session into the next).
_OPTIONS = ClientOptions(persist_session=False, auto_refresh_token=False)


@lru_cache
def get_supabase() -> Client:
    """Client with the publishable (anon) key - for signup/login/refresh."""
    return create_client(
        settings.supabase_url,
        settings.supabase_publishable_key,
        options=_OPTIONS,
    )


@lru_cache
def get_supabase_admin() -> Client:
    """Client with the secret (service_role) key - bypasses RLS.

    Only for privileged server-side work (e.g. deleting an auth user when
    profile creation fails). Never expose this key to clients.
    """
    return create_client(
        settings.supabase_url,
        settings.supabase_secret_key,
        options=_OPTIONS,
    )
