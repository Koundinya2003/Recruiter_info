"""The application tracker.

An application snapshots the job and contact details at the moment it is
created. The snapshot is what the tracker shows, so a row keeps working after
the underlying posting is taken down, re-crawled, or deleted from the library —
which is exactly when a tracker is most needed.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.enums import ApplicationStatus, OutreachStatus

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.job import Job
    from app.models.user import User


class Application(Base, TimestampMixin):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_application_user_job"),
        Index("ix_application_status", "user_id", "status"),
        Index("ix_application_follow_up", "follow_up_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, not CASCADE: losing the posting must not lose the application.
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )

    # --- Snapshot of the job ------------------------------------------------
    job_title: Mapped[str] = mapped_column(String(400), nullable=False)
    company_name: Mapped[str] = mapped_column(String(300), nullable=False)
    location: Mapped[str | None] = mapped_column(String(300))
    job_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    source: Mapped[str | None] = mapped_column(String(40))

    # --- Snapshot of the contact -------------------------------------------
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_title: Mapped[str | None] = mapped_column(String(250))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_profile_url: Mapped[str | None] = mapped_column(String(1000))

    # --- Tracker state ------------------------------------------------------
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, native_enum=False, length=30),
        default=ApplicationStatus.SAVED,
        nullable=False,
    )
    outreach_status: Mapped[OutreachStatus] = mapped_column(
        Enum(OutreachStatus, native_enum=False, length=30),
        default=OutreachStatus.NOT_STARTED,
        nullable=False,
    )

    date_found: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    date_saved: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    date_applied: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outreach_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_change_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    follow_up_date: Mapped[date | None] = mapped_column(Date, index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[Job | None] = relationship()
    contact: Mapped[Contact | None] = relationship()
    events: Mapped[list[ApplicationEvent]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ApplicationEvent.created_at",
    )

    @property
    def is_applied(self) -> bool:
        return self.status.counts_as_applied

    @property
    def follow_up_due(self) -> bool:
        if self.follow_up_date is None or not self.status.is_open:
            return False
        return self.follow_up_date <= utcnow().date()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Application {self.id} {self.job_title!r} {self.status}>"


class ApplicationEvent(Base):
    """Append-only history of what the user did with an application."""

    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    application: Mapped[Application] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ApplicationEvent {self.id} {self.event_type}>"
