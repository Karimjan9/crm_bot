from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: SecretStr
    bot_mode: str = Field(default="polling", pattern="^(polling|webhook)$")
    public_base_url: str = ""
    telegram_webhook_secret: SecretStr | None = None

    crm_base_url: str
    crm_bot_api_key: SecretStr
    crm_webhook_secret: SecretStr
    crm_timeout_seconds: float = Field(default=15, ge=1, le=60)
    redis_url: str = "redis://localhost:6379/0"

    workday_start: str = "09:00"
    workday_end: str = "18:00"
    default_timezone: str = "Asia/Tashkent"
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1_000_000, le=50 * 1024 * 1024)
    redis_state_ttl_hours: int = Field(default=24, ge=1, le=168)
    outbox_max_attempts: int = Field(default=5, ge=1, le=20)
    dead_letter_retention_days: int = Field(default=30, ge=1, le=365)


@lru_cache
def get_settings() -> Settings:
    return Settings()
