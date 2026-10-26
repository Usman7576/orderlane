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
    # Dashboard sessions. cookie_secure must be true in production (HTTPS only).
    cookie_secure: bool = False
    session_idle_hours: int = 12
    session_max_days: int = 7
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    storage_dir: Path = Path("storage")

    # WhatsApp Cloud API
    wa_graph_version: str = "v23.0"
    wa_phone_number_id: str = ""
    wa_access_token: SecretStr = SecretStr("")
    wa_app_secret: SecretStr = SecretStr("")
    wa_verify_token: SecretStr = SecretStr("")
    wa_mark_as_read: bool = True

    # Azure OpenAI
    llm_provider: str = "azure"  # azure | rules (no LLM: rules pipeline only)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: SecretStr = SecretStr("")
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment_small: str = ""
    azure_openai_deployment_large: str = ""
    # Rough USD per 1M tokens, for cost estimates in agent_runs (set to your model's price).
    llm_price_input_per_m: float = 0.15
    llm_price_output_per_m: float = 0.60

    # Azure AI Speech
    azure_speech_key: SecretStr = SecretStr("")
    azure_speech_region: str = ""
    speech_languages: str = "ur-PK,en-US"

    # Agent thresholds
    match_confidence_min: float = 0.85
    fuzzy_accept: float = 0.85
    review_above_total: float = 100_000
    # Wait this long after a customer's last message before replying, to answer a burst once.
    confirm_debounce_seconds: int = 10

    @property
    def llm_enabled(self) -> bool:
        return (
            self.llm_provider == "azure"
            and bool(self.azure_openai_endpoint)
            and bool(self.azure_openai_api_key.get_secret_value())
            and bool(self.azure_openai_deployment_small)
        )

    @property
    def speech_enabled(self) -> bool:
        return bool(self.azure_speech_key.get_secret_value() and self.azure_speech_region)

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
