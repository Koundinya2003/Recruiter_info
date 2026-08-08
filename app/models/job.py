"""Discovered job postings."""

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
from app.models.enums import JobStatus, SourceType

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.recruiter import JobRecruiterLink


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        # Two independent dedupe keys: the canonical URL, and a content hash of
        # (normalized company, normalized title, location).
        UniqueConstraint("company_id", "canonical_url", name="uq_job_company_canonical_url"),
        Index("ix_job_content_hash", "company_id", "content_hash"),
        Index("ix_job_discovered_at", "discovered_at"),
        Index("ix_job_relevance", "relevance_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(400), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(300))
    normalized_location: Mapped[str | None] = mapped_column(String(300))
    employment_type: Mapped[str | None] = mapped_column(String(80))

    job_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    source: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, length=40), nullable=False
    )
    source_job_id: Mapped[str | None] = mapped_column(String(200))

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), default=JobStatus.OPEN, nullable=False
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    relevance_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    relevance_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="jobs")
    recruiter_links: Mapped[list[JobRecruiterLink]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def age_hours(self) -> float | None:
        reference = self.posted_at or self.discovered_at
        if reference is None:
            return None
        return (utcnow() - reference).total_seconds() / 3600.0

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job {self.id} {self.title!r}>"
