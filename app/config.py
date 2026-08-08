"""Application settings.

Everything is sourced from the environment (or a local `.env`). No secret is
ever read from a committed file. Every third-party integration is optional:
when a key is absent the corresponding service degrades to an offline
implementation rather than failing at startup.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

EmailVerificationProvider = Literal["dns", "null", "http"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---------------------------------------------------------------
    app_env: str = "development"
    app_name: str = "Recruiter Outreach Intelligence"
    log_level: str = "INFO"
    log_json: bool = False

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg2://roi:roi@localhost:5432/roi"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- API auth -----------------------------------------------------------
    api_key: str = ""
    api_rate_limit_per_minute: int = 240

    # Identifies the single local user who owns the data.
    owner_email: str = "owner@localhost"

    # --- AI -----------------------------------------------------------------
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-3.5-sonnet"
    ai_request_timeout: float = 45.0
    ai_max_tokens: int = 700

    # --- Email verification -------------------------------------------------
    email_verification_provider: EmailVerificationProvider = "dns"
    email_verification_api_key: str = ""
    email_verification_api_url: str = ""
    email_verification_timeout: float = 15.0

    # --- Crawling -----------------------------------------------------------
    crawler_user_agent: str = (
        "RecruiterOutreachIntelligence/0.1 (+https://example.com/bot; research tool)"
    )
    crawler_domain_delay_seconds: float = 2.0
    crawler_request_timeout: float = 20.0
    crawler_max_retries: int = 3
    crawler_max_pages_per_run: int = 25
    crawler_max_response_bytes: int = 3_000_000
    crawler_respect_robots: bool = True
    crawler_allow_private_networks: bool = False

    # --- Scheduler ----------------------------------------------------------
    scheduler_enabled: bool = False
    scheduler_interval_minutes: int = Field(default=180, ge=5)

    # --- Frontend -----------------------------------------------------------
    api_base_url: str = "http://localhost:8000"

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    # --- Derived helpers ----------------------------------------------------
    @property
    def ai_configured(self) -> bool:
        return bool(self.openrouter_api_key.strip())

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key.strip())

    @property
    def is_test(self) -> bool:
        return self.app_env.lower() in {"test", "testing"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
