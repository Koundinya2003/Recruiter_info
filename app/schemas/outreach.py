"""Outreach queue, draft generation and dashboard schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import OutreachStatus, ResponseStatus
from app.schemas.common import ORMModel, ScoreBreakdownOut


class OpportunityOut(BaseModel):
    """One row of the 'Who should I contact today?' table."""

    company_id: int
    company_name: str
    industry: str | None = None
    job_id: int | None = None
    job_title: str | None = None
    job_url: str | None = None
    job_relevance: float | None = None
    posted_at: str | None = None
    job_age_hours: float | None = None
    recruiter_id: int
    recruiter_name: str
    recruiter_title: str | None = None
    recruiter_score: float
    email: str | None = None
    email_confidence: str
    email_verified: bool
    email_status: str
    email_is_inferred: bool
    outreach_priority: float
    band: str
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    breakdown: dict[str, Any] = Field(default_factory=dict)
    existing_lead_id: int | None = None
    existing_lead_status: str | None = None
    is_demo: bool = False


class LeadCreate(BaseModel):
    recruiter_id: int
    job_id: int | None = None
    note: str | None = Field(default=None, max_length=2000)


class LeadOut(ORMModel):
    id: int
    company_id: int
    recruiter_id: int
    job_id: int | None
    status: OutreachStatus
    outreach_priority: float
    priority_band: str | None
    draft_subject: str | None
    draft_body: str | None
    draft_provider: str | None
    draft_model: str | None
    draft_generated_at: datetime | None
    draft_edited_at: datetime | None
    draft_approved: bool
    draft_approved_at: datetime | None
    contacted_at: datetime | None
    last_contact_at: datetime | None
    contact_count: int
    response_status: ResponseStatus
    responded_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class OutreachEventOut(ORMModel):
    id: int
    event_type: str
    from_status: OutreachStatus | None
    to_status: OutreachStatus | None
    note: str | None
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str
    created_at: datetime


class LeadDetail(LeadOut):
    company_name: str | None = None
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    recruiter_email_is_inferred: bool = False
    recruiter_email_verified: bool = False
    recruiter_do_not_contact: bool = False
    job_title: str | None = None
    job_url: str | None = None
    priority: ScoreBreakdownOut = Field(default_factory=ScoreBreakdownOut)
    events: list[OutreachEventOut] = Field(default_factory=list)
    allowed_transitions: list[str] = Field(default_factory=list)
    is_demo: bool = False


class StatusChangeRequest(BaseModel):
    status: OutreachStatus
    note: str | None = Field(default=None, max_length=2000)


class DraftRequest(BaseModel):
    reason: str | None = Field(
        default=None,
        max_length=1000,
        description="Optional: why you are reaching out, in your own words.",
    )
    force_offline: bool = Field(
        default=False, description="Use the offline template drafter instead of the LLM."
    )


class DraftOut(BaseModel):
    subject: str
    body: str
    provider: str
    model: str
    is_fallback: bool
    warnings: list[str] = Field(default_factory=list)
    approved: bool = False


class DraftEdit(BaseModel):
    subject: str | None = Field(default=None, max_length=400)
    body: str | None = Field(default=None, max_length=20000)


class RecordOutreachRequest(BaseModel):
    channel: str = Field(default="email", max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class RecordResponseRequest(BaseModel):
    response: ResponseStatus
    note: str | None = Field(default=None, max_length=2000)


class NoteRequest(BaseModel):
    """A note that must actually contain something."""

    note: str = Field(min_length=1, max_length=2000)


class OptionalNoteRequest(BaseModel):
    """For actions where a note is a nicety, not a requirement."""

    note: str | None = Field(default=None, max_length=2000)


class LeadFilters(BaseModel):
    status: OutreachStatus | None = None
    company_id: int | None = None
    q: str | None = None
    include_demo: bool = True
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class DashboardStats(BaseModel):
    hiring_signals_today: int = 0
    relevant_jobs: int = 0
    recruiters_found: int = 0
    verified_emails: int = 0
    high_priority_leads: int = 0
    companies_tracked: int = 0
    jobs_tracked: int = 0
    leads_awaiting_approval: int = 0
    contacted_this_week: int = 0
    inferred_emails: int = 0
    demo_mode: bool = False


class DashboardOut(BaseModel):
    stats: DashboardStats
    opportunities: list[OpportunityOut] = Field(default_factory=list)
    generated_at: datetime
