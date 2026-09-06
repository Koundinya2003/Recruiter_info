"""Job and contact response shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ValidationOut(BaseModel):
    status: str
    label: str
    reason: str | None = None
    confirmed: bool = False
    checks: dict[str, str] = Field(default_factory=dict)
    checked_at: datetime | None = None
    http_status: int | None = None


class ContactOut(ORMModel):
    id: int
    name: str | None
    display_name: str
    title: str | None
    company_name: str
    role: str
    role_label: str
    is_person: bool
    profile_url: str | None
    search_url: str | None
    email: str | None
    email_status: str
    email_status_label: str
    email_verification: str
    source: str
    source_label: str
    source_url: str | None
    source_excerpt: str | None
    confidence: float
    rationale: str | None = None
    rank: int | None = None


class JobOut(ORMModel):
    id: int
    title: str
    company_name: str
    location: str | None
    is_remote: bool
    experience_label: str
    min_years: float | None
    max_years: float | None
    employment_type: str | None
    department: str | None
    salary_text: str | None
    summary: str | None
    job_url: str
    apply_url: str | None
    final_url: str | None
    source: str
    source_label: str
    posted_at: datetime | None
    discovered_at: datetime
    age_days: float | None
    validation: ValidationOut
    relevance_score: float
    match_reasons: list[str]
    is_saved: bool
    is_dismissed: bool
    contacts: list[ContactOut] = Field(default_factory=list)
    application_id: int | None = None
    application_status: str | None = None


class JobDetail(JobOut):
    description: str | None = None
    relevance_breakdown: dict[str, Any] = Field(default_factory=dict)
    search_id: int | None = None
    company_id: int


class JobActionRequest(BaseModel):
    dismissed: bool | None = None
    saved: bool | None = None
