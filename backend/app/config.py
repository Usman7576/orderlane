from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, read from environment variables (and .env in development)."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = "postgresql+asyncpg://orderlane:orderlane@localhost:5432/orderlane"
    redis_url: str = "redis://localhost:6379/0"
    session_secret: SecretStr = SecretStr("change-me")
    storage_dir: Path = Path("storage")

    # WhatsApp Cloud API
    wa_graph_version: str = "v23.0"
    wa_phone_number_id: str = ""
    wa_access_token: SecretStr = SecretStr("")
    wa_app_secret: SecretStr = SecretStr("")
    wa_verify_token: SecretStr = SecretStr("")
    wa_mark_as_read: bool = True

    # Agent thresholds (used from Day 2 onwards)
    match_confidence_min: float = 0.85
    fuzzy_accept: float = 0.92
    review_above_total: float = 100_000
    confirm_debounce_seconds: int = 20

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
