"""Job schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import JobStatus, SourceType
from app.schemas.common import ORMModel, ScoreBreakdownOut


class JobOut(ORMModel):
    id: int
    company_id: int
    title: str
    location: str | None
    employment_type: str | None
    job_url: str
    source: SourceType
    status: JobStatus
    posted_at: datetime | None
    discovered_at: datetime
    last_seen_at: datetime
    relevance_score: float
    is_demo: bool


class JobDetail(JobOut):
    description: str | None
    normalized_title: str
    canonical_url: str
    content_hash: str
    source_job_id: str | None
    company_name: str | None = None
    relevance: ScoreBreakdownOut = Field(default_factory=ScoreBreakdownOut)
    age_hours: float | None = None
    linked_recruiters: list[dict] = Field(default_factory=list)


class JobFilters(BaseModel):
    q: str | None = None
    company_id: int | None = None
    status: JobStatus | None = None
    source: SourceType | None = None
    min_relevance: float | None = Field(default=None, ge=0, le=100)
    max_age_hours: float | None = Field(default=None, ge=0)
    location: str | None = None
    include_demo: bool = True
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    sort: str = Field(default="relevance", pattern="^(relevance|posted|discovered|title)$")
