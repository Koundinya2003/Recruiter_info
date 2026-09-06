"""Application tracker request and response shapes."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ApplicationStatus, OutreachStatus
from app.schemas.common import ORMModel


class ApplicationCreate(BaseModel):
    job_id: int
    contact_id: int | None = None
    status: ApplicationStatus = ApplicationStatus.SAVED
    notes: str | None = Field(default=None, max_length=5000)


class ApplicationUpdate(BaseModel):
    status: ApplicationStatus | None = None
    outreach_status: OutreachStatus | None = None
    contact_id: int | None = None
    contact_email: str | None = Field(default=None, max_length=320)
    notes: str | None = Field(default=None, max_length=5000)
    follow_up_date: date | None = None
    clear_follow_up: bool = False
    date_applied: datetime | None = None

    @field_validator("contact_email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value and ("@" not in value or " " in value):
            raise ValueError("not a valid email address")
        return value or None


class ApplicationEventOut(ORMModel):
    id: int
    event_type: str
    detail: str | None
    created_at: datetime


class ApplicationOut(ORMModel):
    id: int
    job_id: int | None
    contact_id: int | None
    job_title: str
    company_name: str
    location: str | None
    job_url: str
    source: str | None
    source_label: str | None
    contact_name: str | None
    contact_title: str | None
    contact_email: str | None
    contact_profile_url: str | None
    status: str
    status_label: str
    outreach_status: str
    outreach_status_label: str
    date_found: datetime
    date_applied: datetime | None
    outreach_sent_at: datetime | None
    follow_up_date: date | None
    follow_up_due: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ApplicationDetail(ApplicationOut):
    events: list[ApplicationEventOut] = Field(default_factory=list)


class DashboardOut(BaseModel):
    jobs_found: int
    valid_jobs: int
    saved_jobs: int
    applications_submitted: int
    outreach_sent: int
    interviews: int
    offers: int
    follow_ups_due: int
    in_pipeline: int
    pipeline: dict[str, int]
    recent_jobs: list[dict] = Field(default_factory=list)
    due_follow_ups: list[ApplicationOut] = Field(default_factory=list)
    last_search: dict | None = None
    sources_usable: int = 0
    sources_total: int = 0
