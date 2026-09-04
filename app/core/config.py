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