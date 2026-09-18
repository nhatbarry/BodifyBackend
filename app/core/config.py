from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Bodify API"
    app_version: str = "0.1.0"
    debug: bool = False

    database_url: str

    supabase_url: str
    supabase_publishable_key: str
    supabase_secret_key: str

    storage_endpoint: str
    storage_access_key: str
    storage_secret_key: str
    storage_bucket: str

    # A second R2 credential, this one allowed to write and delete.
    #
    # Kept apart from the pair above on purpose: everything that serves the
    # exercise catalog only ever reads, and giving that path write access
    # would mean a bug in a read endpoint could damage 1400 media files. Only
    # avatar upload needs these.
    #
    # Optional. Left blank, avatar upload answers 503 with a clear reason
    # rather than failing later as an opaque AccessDenied from R2.
    storage_write_access_key: str | None = None
    storage_write_secret_key: str | None = None

    # User uploads live in their own bucket, away from the exercise catalog.
    # The write credential is scoped to this bucket alone - it cannot even list
    # the catalog - so the blast radius of a mistake in the upload path stops
    # at content users put there themselves.
    storage_write_bucket: str | None = None

    # The AI coach's model key. Server-side only, and that is the point: the
    # Kotlin prototype shipped this key inside the app, where anyone who
    # unzips the APK can read it and spend it.
    #
    # Optional, like the write credentials above. Left blank, the chat
    # endpoints answer 503 with a reason rather than failing inside the
    # request as an opaque error from Google.
    gemini_api_key: str | None = None

    # Which model answers. Flash-Lite rather than Flash or Pro, because of
    # what this job actually is: the coach is handed the user's numbers and
    # writes four sentences about them. That is phrasing, not reasoning -
    # which is also why thinking is switched off in services/gemini.py. Paying
    # for a stronger model to do work we have deliberately stopped it thinking
    # about buys nothing.
    #
    # 3.1 rather than 3.5: same family, lower price ($0.25/$1.50 against
    # $0.30/$2.50 per MTok). The 3.8 Flash on the price list is cheaper still
    # today, but it is built for long-horizon coding and its introductory rate
    # doubles in January 2027.
    gemini_model: str = "gemini-3.1-flash-lite"

    # Signing in with Google, the native way.
    #
    # This is the **Web** OAuth client ID from Google Cloud, not the Android
    # one. Android's account picker signs its ID token for whichever client is
    # named as the audience, and Supabase only accepts a token whose audience
    # is a client ID configured on its Google provider - so both ends have to
    # name this same one. The app is told it by GET /auth/oauth/config rather
    # than compiling it in, so changing the Google project does not mean
    # shipping a new APK.
    #
    # Not a secret: a client ID travels in every OAuth request. The client
    # *secret* lives in the Supabase dashboard and never comes near this app.
    #
    # Optional. Left blank, the config endpoint reports Google as unavailable
    # and the app hides the button rather than offering one that cannot work.
    google_oauth_client_id: str | None = None

    # Where Supabase sends the browser back to after a social sign-in.
    #
    # A custom scheme, because the destination is an app and not a web page.
    # It has to be on the project's redirect allowlist in the Supabase
    # dashboard, or the handshake ends on an error page instead of in the app.
    oauth_redirect_url: str = "bodify://auth"

    # Whether Facebook's button is offered. There is nothing for this server
    # to hold - the app id and secret live in the Supabase dashboard - so this
    # is simply "has that been set up yet", and it exists so the app can hide
    # a button that would otherwise open a browser onto a Supabase error.
    facebook_oauth_enabled: bool = False

    @property
    def can_use_ai_coach(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def can_use_google_oauth(self) -> bool:
        return bool(self.google_oauth_client_id)

    @property
    def can_write_storage(self) -> bool:
        return bool(
            self.storage_write_access_key
            and self.storage_write_secret_key
            and self.storage_write_bucket
        )

    @property
    def upload_bucket(self) -> str:
        if not self.storage_write_bucket:
            raise RuntimeError("STORAGE_WRITE_BUCKET is not configured")
        return self.storage_write_bucket


settings = Settings()