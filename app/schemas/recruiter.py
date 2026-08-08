"""Recruiter, contact and verification schemas.

``email_is_inferred`` is present on every recruiter payload so no client can
render an inferred address as if it were a published one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import (
    ContactType,
    EmailConfidence,
    SourceType,
    VerificationStatus,
)
from app.schemas.common import ORMModel, ScoreBreakdownOut, validate_public_url


class ContactOut(ORMModel):
    id: int
    contact_type: ContactType
    value: str
    source_url: str | None
    source_type: SourceType
    source_excerpt: str | None
    confidence: EmailConfidence
    is_inferred: bool
    is_primary: bool
    first_seen: datetime
    last_seen: datetime


class RecruiterOut(ORMModel):
    id: int
    company_id: int
    name: str
    company_name: str
    title: str | None
    professional_profile_url: str | None
    public_professional_email: str | None
    email_source_url: str | None
    email_source_type: SourceType | None
    email_confidence: EmailConfidence
    email_confidence_score: float
    email_verified: bool
    email_verification_status: VerificationStatus
    email_verified_at: datetime | None
    email_is_inferred: bool
    role_relevance: float
    relevance_score: float
    do_not_contact: bool
    do_not_contact_reason: str | None
    last_seen: datetime
    discovered_at: datetime
    is_demo: bool


class RecruiterDetail(RecruiterOut):
    notes: str | None
    relevance: ScoreBreakdownOut = Field(default_factory=ScoreBreakdownOut)
    contacts: list[ContactOut] = Field(default_factory=list)
    associated_jobs: list[dict[str, Any]] = Field(default_factory=list)
    verification_history: list[dict[str, Any]] = Field(default_factory=list)


class RecruiterCreate(BaseModel):
    """Manual entry of a contact the user found themselves."""

    company_id: int
    name: str = Field(min_length=2, max_length=200)
    title: str | None = Field(default=None, max_length=250)
    public_professional_email: EmailStr | None = None
    professional_profile_url: str | None = Field(default=None, max_length=1000)
    email_source_url: str | None = Field(default=None, max_length=1000)
    email_source_type: SourceType = SourceType.MANUAL_ENTRY
    email_confidence: EmailConfidence = EmailConfidence.MEDIUM
    notes: str | None = None

    @field_validator("professional_profile_url", "email_source_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return validate_public_url(value)


class RecruiterUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=250)
    professional_profile_url: str | None = Field(default=None, max_length=1000)
    notes: str | None = None

    @field_validator("professional_profile_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return validate_public_url(value)


class DoNotContactRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class VerificationOut(BaseModel):
    email: str
    status: VerificationStatus
    confidence: float
    provider: str
    reason: str = ""
    checked_at: datetime | None = None


class RecruiterFilters(BaseModel):
    q: str | None = None
    company_id: int | None = None
    min_score: float | None = Field(default=None, ge=0, le=100)
    email_confidence: EmailConfidence | None = None
    verification_status: VerificationStatus | None = None
    has_email: bool | None = None
    exclude_do_not_contact: bool = True
    include_demo: bool = True
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
