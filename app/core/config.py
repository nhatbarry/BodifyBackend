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


settings = Settings()