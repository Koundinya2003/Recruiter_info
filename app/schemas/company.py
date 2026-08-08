"""Company request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.enums import CompanyPriority
from app.schemas.common import ORMModel, validate_public_url


class CompanyCreate(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    company_domain: str | None = Field(default=None, max_length=255)
    career_page_url: str | None = Field(default=None, max_length=1000)
    industry: str | None = Field(default=None, max_length=120)
    priority: CompanyPriority = CompanyPriority.MEDIUM
    active: bool = True
    notes: str | None = None

    @field_validator("career_page_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return validate_public_url(value)

    @field_validator("company_domain")
    @classmethod
    def _clean_domain(cls, value: str | None) -> str | None:
        if not value:
            return None
        from app.security.url_guard import extract_domain

        domain = extract_domain(value)
        if not domain or "." not in domain:
            raise ValueError("Enter a valid domain, for example 'example.com'")
        return domain

    @field_validator("company_name", "industry", "notes")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class CompanyUpdate(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    company_domain: str | None = Field(default=None, max_length=255)
    career_page_url: str | None = Field(default=None, max_length=1000)
    industry: str | None = Field(default=None, max_length=120)
    priority: CompanyPriority | None = None
    active: bool | None = None
    notes: str | None = None

    @field_validator("career_page_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return validate_public_url(value)


class CompanyOut(ORMModel):
    id: int
    company_name: str
    company_domain: str | None
    career_page_url: str | None
    industry: str | None
    priority: CompanyPriority
    active: bool
    is_demo: bool
    notes: str | None
    hiring_activity_score: float
    hiring_activity_computed_at: datetime | None
    last_checked_at: datetime | None
    last_scan_status: str | None
    created_at: datetime
    updated_at: datetime


class CompanyStats(BaseModel):
    open_jobs: int = 0
    relevant_jobs: int = 0
    recruiters: int = 0
    contactable_recruiters: int = 0
    verified_emails: int = 0
    signals_7d: int = 0


class HiringSignalOut(ORMModel):
    id: int
    signal_type: str
    points: float
    description: str
    detected_at: datetime
    job_id: int | None
    details: dict[str, Any] = Field(default_factory=dict)


class CompanyDetail(CompanyOut):
    stats: CompanyStats = Field(default_factory=CompanyStats)
    hiring_activity_breakdown: dict[str, Any] = Field(default_factory=dict)
    recent_signals: list[HiringSignalOut] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)


class ScanRequest(BaseModel):
    discover_recruiters: bool = True
    infer_emails: bool = Field(
        default=False,
        description=(
            "Generate low-confidence pattern-based addresses for recruiters with no "
            "published address. These are guesses and are never marked verified."
        ),
    )
    extra_recruiter_urls: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("extra_recruiter_urls")
    @classmethod
    def _check_urls(cls, values: list[str]) -> list[str]:
        return [url for url in (validate_public_url(v) for v in values) if url]


class ScanResult(BaseModel):
    company_id: int
    company_name: str
    jobs_found: int
    jobs_added: int
    jobs_updated: int
    jobs_closed: int
    recruiters_found: int
    recruiters_added: int
    emails_found: int
    inferred_emails: int
    links_created: int
    hiring_activity_score: float
    crawl_run_ids: list[int]
    statuses: list[str]
    errors: list[str]
    notes: list[str]
    ok: bool
