"""Profile, taxonomy, scoring-config and admin schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import CrawlStatus, SourceType, TaxonomyKind
from app.schemas.common import ORMModel, validate_public_url


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    headline: str | None = Field(default=None, max_length=300)
    education: str | None = None
    experience: str | None = None
    years_experience: float | None = Field(default=None, ge=0, le=60)
    skills: list[str] = Field(default_factory=list, max_length=100)
    target_roles: list[str] = Field(default_factory=list, max_length=50)
    target_industries: list[str] = Field(default_factory=list, max_length=50)
    preferred_locations: list[str] = Field(default_factory=list, max_length=50)
    portfolio_url: str | None = Field(default=None, max_length=500)
    github_url: str | None = Field(default=None, max_length=500)
    linkedin_url: str | None = Field(default=None, max_length=500)
    resume_filename: str | None = Field(default=None, max_length=300)
    resume_text: str | None = Field(default=None, max_length=60000)

    @field_validator("portfolio_url", "github_url", "linkedin_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return validate_public_url(value)

    @field_validator("skills", "target_roles", "target_industries", "preferred_locations")
    @classmethod
    def _clean_list(cls, values: list[str]) -> list[str]:
        seen: list[str] = []
        for value in values:
            cleaned = str(value).strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned[:120])
        return seen


class ProfileOut(ProfileIn):
    id: int | None = None
    user_email: str | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaxonomyTermIn(BaseModel):
    kind: TaxonomyKind
    term: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=25)
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    is_primary: bool = True
    active: bool = True

    @field_validator("term")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("aliases")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        return [str(v).strip()[:120] for v in values if str(v).strip()]


class TaxonomyTermOut(ORMModel):
    id: int
    kind: TaxonomyKind
    term: str
    aliases: list[str]
    weight: float
    is_primary: bool
    active: bool


class ScoringConfigIn(BaseModel):
    job_weights: dict[str, float] = Field(default_factory=dict)
    hiring_weights: dict[str, float] = Field(default_factory=dict)
    recruiter_weights: dict[str, float] = Field(default_factory=dict)
    outreach_weights: dict[str, float] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("job_weights", "hiring_weights", "recruiter_weights", "outreach_weights")
    @classmethod
    def _check_weights(cls, values: dict[str, float]) -> dict[str, float]:
        for key, value in values.items():
            if not isinstance(value, (int, float)) or value < 0 or value > 100:
                raise ValueError(f"Weight '{key}' must be a number between 0 and 100")
        return {k: float(v) for k, v in values.items()}


class ScoringConfigOut(BaseModel):
    job: dict[str, float]
    hiring: dict[str, float]
    recruiter: dict[str, float]
    outreach: dict[str, float]
    options: dict[str, Any]


class CrawlRunOut(ORMModel):
    id: int
    company_id: int | None
    collector: str
    source: SourceType
    target_url: str | None
    trigger: str
    started_at: datetime
    completed_at: datetime | None
    status: CrawlStatus
    records_found: int
    records_added: int
    records_updated: int
    records_rejected: int
    pages_fetched: int
    bytes_downloaded: int
    rate_limit_waits: int
    rate_limit_seconds: float
    error_count: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None
    duration_seconds: float | None = None


class SourceRecordOut(ORMModel):
    id: int
    crawl_run_id: int
    record_type: str
    source_type: SourceType
    source_url: str | None
    external_id: str | None
    accepted: bool
    reject_reason: str | None
    fetched_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class RateLimitEventOut(BaseModel):
    domain: str
    waited_seconds: float
    at: float


class AdminOverview(BaseModel):
    last_crawl: CrawlRunOut | None = None
    recent_crawls: list[CrawlRunOut] = Field(default_factory=list)
    sources_checked: dict[str, int] = Field(default_factory=dict)
    jobs_discovered_7d: int = 0
    recruiters_discovered_7d: int = 0
    emails_discovered: int = 0
    inferred_emails: int = 0
    verification_results: dict[str, int] = Field(default_factory=dict)
    recent_errors: list[dict[str, Any]] = Field(default_factory=list)
    rate_limit_events: list[RateLimitEventOut] = Field(default_factory=list)
    crawl_status_counts: dict[str, int] = Field(default_factory=dict)


class HealthOut(BaseModel):
    status: str
    app: str
    environment: str
    database: str
    ai_provider: str
    ai_configured: bool
    email_verification_provider: str
    auth_enabled: bool
    scheduler_enabled: bool
    demo_records: int = 0
