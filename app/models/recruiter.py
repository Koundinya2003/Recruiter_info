"""Recruiters, their contact details, and their links to jobs.

Contact details are deliberately kept in their own table rather than as columns
on the recruiter: every single one carries the URL it came from and the kind of
source it was, so the UI can always answer "where did this come from?".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.enums import (
    ContactType,
    EmailConfidence,
    RecruiterRelation,
    SourceType,
    VerificationStatus,
)

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.job import Job


class Recruiter(Base, TimestampMixin):
    __tablename__ = "recruiters"
    __table_args__ = (
        UniqueConstraint("company_id", "normalized_name", name="uq_recruiter_company_name"),
        Index("ix_recruiter_relevance", "relevance_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(250))
    professional_profile_url: Mapped[str | None] = mapped_column(String(1000))

    # The primary public professional email, denormalised from `contacts` for
    # convenient querying. The authoritative provenance lives in `contacts`.
    public_professional_email: Mapped[str | None] = mapped_column(String(320), index=True)
    email_source_url: Mapped[str | None] = mapped_column(String(1000))
    email_source_type: Mapped[SourceType | None] = mapped_column(
        Enum(SourceType, native_enum=False, length=40)
    )
    email_confidence: Mapped[EmailConfidence] = mapped_column(
        Enum(EmailConfidence, native_enum=False, length=20),
        default=EmailConfidence.NONE,
        nullable=False,
    )
    email_confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, native_enum=False, length=20),
        default=VerificationStatus.NOT_CHECKED,
        nullable=False,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role_relevance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    relevance_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    relevance_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    do_not_contact: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    do_not_contact_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    company: Mapped[Company] = relationship(back_populates="recruiters")
    contacts: Mapped[list[Contact]] = relationship(
        back_populates="recruiter", cascade="all, delete-orphan", passive_deletes=True
    )
    job_links: Mapped[list[JobRecruiterLink]] = relationship(
        back_populates="recruiter", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def email_is_inferred(self) -> bool:
        """True when the address was guessed from a pattern, not published."""
        return self.email_confidence is EmailConfidence.LOW

    @property
    def contactable(self) -> bool:
        return bool(self.public_professional_email) and not self.do_not_contact

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Recruiter {self.id} {self.name!r} @ {self.company_name}>"


class Contact(Base, TimestampMixin):
    """One discovered contact detail, with full provenance."""

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("recruiter_id", "contact_type", "value", name="uq_contact_unique_value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recruiter_id: Mapped[int] = mapped_column(
        ForeignKey("recruiters.id", ondelete="CASCADE"), nullable=False, index=True
    )

    contact_type: Mapped[ContactType] = mapped_column(
        Enum(ContactType, native_enum=False, length=30), nullable=False
    )
    value: Mapped[str] = mapped_column(String(1000), nullable=False)

    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, length=40), nullable=False
    )
    source_excerpt: Mapped[str | None] = mapped_column(Text)

    confidence: Mapped[EmailConfidence] = mapped_column(
        Enum(EmailConfidence, native_enum=False, length=20),
        default=EmailConfidence.MEDIUM,
        nullable=False,
    )
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    recruiter: Mapped[Recruiter] = relationship(back_populates="contacts")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Contact {self.id} {self.contact_type} {self.value!r}>"


class JobRecruiterLink(Base, TimestampMixin):
    """Association between a job and a recruiter, with why they are linked."""

    __tablename__ = "job_recruiter_relationships"
    __table_args__ = (
        UniqueConstraint("job_id", "recruiter_id", name="uq_job_recruiter"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recruiter_id: Mapped[int] = mapped_column(
        ForeignKey("recruiters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[RecruiterRelation] = mapped_column(
        Enum(RecruiterRelation, native_enum=False, length=40),
        default=RecruiterRelation.FUNCTION_MATCH,
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="recruiter_links")
    recruiter: Mapped[Recruiter] = relationship(back_populates="job_links")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<JobRecruiterLink job={self.job_id} recruiter={self.recruiter_id}>"
