"""People (and published inboxes) worth contacting about a role.

Every row carries the URL it was read from. A contact with no citable source
cannot be created by discovery — only by the user, and it is labelled as such.

There is no pattern-inference path here by design: the product never turns
"Priya Sharma" plus "acme.com" into an address.
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
    ContactRole,
    EmailStatus,
    SourceType,
    VerificationStatus,
)

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.job import Job


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("company_id", "dedupe_key", name="uq_contact_company_key"),
        Index("ix_contact_role", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # NULL for a team inbox or a directory search link — those are not people,
    # and inventing a name for them would be exactly the wrong thing to do.
    name: Mapped[str | None] = mapped_column(String(200))
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    title: Mapped[str | None] = mapped_column(String(250))
    company_name: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[ContactRole] = mapped_column(
        Enum(ContactRole, native_enum=False, length=30), nullable=False
    )

    profile_url: Mapped[str | None] = mapped_column(String(1000))
    # For SEARCH_LINK rows: a directory query the user can run themselves.
    search_url: Mapped[str | None] = mapped_column(String(1000))

    email: Mapped[str | None] = mapped_column(String(320), index=True)
    email_status: Mapped[EmailStatus] = mapped_column(
        Enum(EmailStatus, native_enum=False, length=30),
        default=EmailStatus.NONE,
        nullable=False,
    )
    email_verification: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, native_enum=False, length=20),
        default=VerificationStatus.NOT_CHECKED,
        nullable=False,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Provenance ---------------------------------------------------------
    source: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, length=40), nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(1000))
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)

    notes: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    company: Mapped[Company] = relationship(back_populates="contacts")
    job_links: Mapped[list[JobContact]] = relationship(
        back_populates="contact", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def has_email(self) -> bool:
        return bool(self.email)

    @property
    def is_person(self) -> bool:
        return self.role.is_person and bool(self.name)

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.role is ContactRole.TALENT_ALIAS:
            return f"{self.company_name} talent inbox"
        return f"Find recruiters at {self.company_name}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Contact {self.id} {self.display_name!r} ({self.role})>"


class JobContact(Base, TimestampMixin):
    """Which contacts were surfaced for which job, and why."""

    __tablename__ = "job_contacts"
    __table_args__ = (UniqueConstraint("job_id", "contact_id", name="uq_job_contact"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 0 is the best contact for this job.
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    relevance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="contact_links")
    contact: Mapped[Contact] = relationship(back_populates="job_links")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<JobContact job={self.job_id} contact={self.contact_id} rank={self.rank}>"
