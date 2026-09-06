"""A job posting discovered from an external source.

Every row here came from a real provider response. Nothing on this table is
ever synthesised: if a field is unknown it stays NULL rather than being filled
in with a plausible value.
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
from app.models.enums import SourceType, ValidationStatus

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.contact import JobContact
    from app.models.search import JobSearch


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        # Two independent dedupe keys: the canonical URL, and a fingerprint of
        # (company, title, location) that catches the same role syndicated to
        # several boards under different URLs.
        UniqueConstraint("user_id", "canonical_url", name="uq_job_user_canonical_url"),
        Index("ix_job_fingerprint", "user_id", "fingerprint"),
        Index("ix_job_validation", "validation_status"),
        Index("ix_job_discovered", "discovered_at"),
        Index("ix_job_relevance", "relevance_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The search that first surfaced this posting. Kept even if the search is
    # deleted later, so the job library survives.
    search_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_searches.id", ondelete="SET NULL"), index=True
    )

    # --- What the posting says ---------------------------------------------
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(300), nullable=False)
    location: Mapped[str | None] = mapped_column(String(300))
    normalized_location: Mapped[str | None] = mapped_column(String(300))
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    employment_type: Mapped[str | None] = mapped_column(String(80))
    department: Mapped[str | None] = mapped_column(String(200))
    salary_text: Mapped[str | None] = mapped_column(String(200))

    # Experience: the raw phrase as printed, plus the range parsed from it.
    # Both stay NULL when the posting does not state a requirement.
    experience_text: Mapped[str | None] = mapped_column(String(300))
    min_years: Mapped[float | None] = mapped_column(Float)
    max_years: Mapped[float | None] = mapped_column(Float)

    description: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)

    # --- Where it came from -------------------------------------------------
    job_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    apply_url: Mapped[str | None] = mapped_column(String(1000))
    source: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, length=40), nullable=False
    )
    source_query_url: Mapped[str | None] = mapped_column(String(1000))
    external_id: Mapped[str | None] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # --- Validation ---------------------------------------------------------
    validation_status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, native_enum=False, length=20),
        default=ValidationStatus.PENDING,
        nullable=False,
    )
    validation_checks: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    validation_reason: Mapped[str | None] = mapped_column(Text)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    final_url: Mapped[str | None] = mapped_column(String(1000))

    # --- Relevance to the search that found it ------------------------------
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    relevance_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    match_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    # --- User state ---------------------------------------------------------
    is_saved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    company: Mapped[Company] = relationship(back_populates="jobs")
    search: Mapped[JobSearch | None] = relationship(back_populates="jobs")
    contact_links: Mapped[list[JobContact]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    # -- derived -------------------------------------------------------------
    @property
    def is_displayable(self) -> bool:
        return self.validation_status.is_displayable and not self.is_dismissed

    @property
    def validation_confirmed(self) -> bool:
        return self.validation_status.is_confirmed

    @property
    def experience_label(self) -> str:
        """A human phrase for the experience requirement, honest about gaps."""
        if self.experience_text:
            return self.experience_text
        if self.min_years is None and self.max_years is None:
            return "Not stated"
        if self.max_years is None:
            return f"{self.min_years:g}+ years"
        if self.min_years is None:
            return f"Up to {self.max_years:g} years"
        return f"{self.min_years:g}–{self.max_years:g} years"

    @property
    def age_days(self) -> float | None:
        reference = self.posted_at or self.discovered_at
        if reference is None:
            return None
        return (utcnow() - reference).total_seconds() / 86400.0

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job {self.id} {self.title!r} @ {self.company_name}>"
