"""Application settings.

Everything is sourced from the environment (or a local `.env`). No secret is
ever read from a committed file.

Every external integration is optional. Job sources that need credentials
report themselves as unconfigured and are skipped with a visible reason on the
search run, rather than failing the search or — much worse — filling the gap
with invented results.
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
    app_name: str = "Job Search & Outreach Workspace"
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

    # --- Job sources --------------------------------------------------------
    # Adzuna: free credentials at https://developer.adzuna.com/signup
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    # The Muse works unauthenticated; a key raises the rate limit.
    the_muse_api_key: str = ""
    # USAJobs: free credentials at https://developer.usajobs.gov/apirequest/
    usajobs_api_key: str = ""
    usajobs_user_agent: str = ""
    # Sources that need no credentials at all — switch off if unwanted.
    enable_remotive: bool = True
    enable_arbeitnow: bool = True
    enable_jobicy: bool = True
    enable_the_muse: bool = True
    enable_company_boards: bool = True

    # Greenhouse/Lever/Ashby boards searched when a query names no companies.
    company_boards_file: str = ""

    # --- Search behaviour ---------------------------------------------------
    search_result_limit: int = Field(default=40, ge=1, le=120)
    search_max_age_days: int = Field(default=45, ge=1, le=365)
    # Relevance below this (0..100) is dropped as not matching the request.
    search_min_relevance: float = Field(default=35.0, ge=0.0, le=100.0)
    # Hard cap on postings put through live validation in one search.
    validation_max_jobs: int = Field(default=40, ge=1, le=200)
    validation_timeout: float = 15.0
    # Hard cap on companies contact discovery will crawl in one search.
    contacts_max_companies: int = Field(default=10, ge=0, le=50)
    contacts_max_pages_per_company: int = Field(default=4, ge=1, le=20)
    # Skip re-crawling a company's pages if they were checked this recently.
    contacts_cache_hours: int = Field(default=168, ge=1)

    # --- AI (optional) ------------------------------------------------------
    # Used only to refine the parse of a free-text search request. The rules
    # parser runs first and works with no key at all.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-3.5-sonnet"
    ai_request_timeout: float = 45.0
    ai_max_tokens: int = 700

    # --- Email verification (optional) --------------------------------------
    email_verification_provider: EmailVerificationProvider = "dns"
    email_verification_api_key: str = ""
    email_verification_api_url: str = ""
    email_verification_timeout: float = 15.0

    # --- Responsible fetching -----------------------------------------------
    crawler_user_agent: str = (
        "JobSearchWorkspace/1.0 (+https://example.com/bot; personal job-search tool)"
    )
    crawler_domain_delay_seconds: float = 1.0
    crawler_request_timeout: float = 20.0
    crawler_max_retries: int = 2
    crawler_max_pages_per_run: int = 120
    crawler_max_response_bytes: int = 3_000_000
    crawler_respect_robots: bool = True
    crawler_allow_private_networks: bool = False

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
