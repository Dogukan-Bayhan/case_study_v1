"""Application settings loaded from environment."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized runtime configuration shared across API, ETL, and storage clients."""
    app_name: str = "MultiTenant Analytics"
    env: str = "dev"
    log_level: str = "INFO"
    secret_key: str = "secret"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    database_url: str = "postgresql+psycopg2://analytics:analytics@postgres:5432/analytics"
    clickhouse_host: str = "clickhouse"
    clickhouse_port: int = 9000
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_database: str = "analytics"
    clickhouse_enabled: bool = True
    etl_batch_size: int = 50000
    etl_insert_batch_size: int = 20000
    seed_data: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    """Load and cache application settings for backend services.

    Business purpose:
        Provide a single, consistent configuration source for APIs, ETL, and DB clients.
    Why it exists:
        Parsing environment variables is expensive and should happen once per process.
    Where used:
        Injected via dependencies and during app startup initialization.
    Inputs:
        None; values are read from the environment and defaults.
    Returns:
        Settings instance with resolved runtime configuration.
    """
    # lru_cache ensures settings are constructed once per process.
    return Settings()
