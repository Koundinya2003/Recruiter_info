"""The outreach queue and its immutable event log.

A lead is uniquely keyed on (recruiter, job) so the same person cannot be
queued twice for the same role. `contacted_at` is written exactly once; the
service layer refuses a second send.
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
from app.models.enums import OutreachEventType, OutreachStatus, ResponseStatus

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.job import Job
    from app.models.recruiter import Recruiter
    from app.models.user import User


class OutreachLead(Base, TimestampMixin):
    __tablename__ = "outreach_leads"
    __table_args__ = (
        UniqueConstraint("recruiter_id", "job_id", name="uq_lead_recruiter_job"),
        Index("ix_lead_status_priority", "status", "outreach_priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recruiter_id: Mapped[int] = mapped_column(
        ForeignKey("recruiters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[OutreachStatus] = mapped_column(
        Enum(OutreachStatus, native_enum=False, length=30),
        default=OutreachStatus.NEW,
        nullable=False,
        index=True,
    )
    outreach_priority: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    priority_band: Mapped[str | None] = mapped_column(String(40))
    priority_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    priority_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Draft ---------------------------------------------------------------
    draft_subject: Mapped[str | None] = mapped_column(String(400))
    draft_body: Mapped[str | None] = mapped_column(Text)
    draft_model: Mapped[str | None] = mapped_column(String(120))
    draft_provider: Mapped[str | None] = mapped_column(String(60))
    draft_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    draft_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    draft_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    draft_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Outreach record -----------------------------------------------------
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    contact_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_status: Mapped[ResponseStatus] = mapped_column(
        Enum(ResponseStatus, native_enum=False, length=30),
        default=ResponseStatus.AWAITING,
        nullable=False,
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship()
    company: Mapped[Company] = relationship()
    recruiter: Mapped[Recruiter] = relationship()
    job: Mapped[Job | None] = relationship()
    events: Mapped[list[OutreachEvent]] = relationship(
        back_populates="lead",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="OutreachEvent.created_at",
    )

    @property
    def already_contacted(self) -> bool:
        return self.contacted_at is not None

    @property
    def ready_to_send(self) -> bool:
        return (
            self.status is OutreachStatus.APPROVED
            and self.draft_approved
            and not self.already_contacted
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OutreachLead {self.id} {self.status} p={self.outreach_priority}>"


class OutreachEvent(Base):
    """Append-only history for a lead. Never updated, never deleted."""

    __tablename__ = "outreach_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lead_id: Mapped[int] = mapped_column(
        ForeignKey("outreach_leads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[OutreachEventType] = mapped_column(
        Enum(OutreachEventType, native_enum=False, length=40), nullable=False
    )
    from_status: Mapped[OutreachStatus | None] = mapped_column(
        Enum(OutreachStatus, native_enum=False, length=30)
    )
    to_status: Mapped[OutreachStatus | None] = mapped_column(
        Enum(OutreachStatus, native_enum=False, length=30)
    )
    note: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    actor: Mapped[str] = mapped_column(String(80), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    lead: Mapped[OutreachLead] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OutreachEvent {self.event_type} lead={self.lead_id}>"
