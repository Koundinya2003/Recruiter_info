"""The application tracker.

Every state change is the user's: nothing here advances a status on its own.
The service's job is to keep the record consistent — stamping the date that
goes with a status, snapshotting the job and contact so the row survives the
posting being taken down, and writing an event so the history is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.application import Application, ApplicationEvent
from app.models.contact import Contact
from app.models.enums import ApplicationStatus, OutreachStatus, ValidationStatus
from app.models.job import Job
from app.models.user import User

log = get_logger(__name__)


class TrackerError(Exception):
    """A tracker operation the caller asked for that cannot be done."""


@dataclass
class StatusChange:
    application: Application
    previous: ApplicationStatus
    current: ApplicationStatus


def _record(application: Application, event_type: str, detail: str | None = None) -> None:
    application.events.append(ApplicationEvent(event_type=event_type, detail=detail))


def snapshot_from_job(job: Job, contact: Contact | None) -> dict[str, Any]:
    """The job and contact details frozen into the application row."""
    return {
        "job_title": job.title,
        "company_name": job.company_name,
        "location": job.location,
        "job_url": job.final_url or job.job_url,
        "source": job.source.value,
        "contact_name": contact.display_name if contact else None,
        "contact_title": contact.title if contact else None,
        "contact_email": contact.email if contact else None,
        "contact_profile_url": (contact.profile_url or contact.search_url) if contact else None,
        "date_found": job.discovered_at,
    }


def save_job(
    session: Session,
    user: User,
    job: Job,
    *,
    contact: Contact | None = None,
    status: ApplicationStatus = ApplicationStatus.SAVED,
    notes: str | None = None,
) -> Application:
    """Put a job into the tracker, or return the row that is already there."""
    existing = session.scalar(
        select(Application).where(Application.user_id == user.id, Application.job_id == job.id)
    )
    if existing is not None:
        if contact is not None and existing.contact_id != contact.id:
            attach_contact(session, existing, contact)
        return existing

    snapshot = snapshot_from_job(job, contact)
    application = Application(
        user_id=user.id,
        job_id=job.id,
        contact_id=contact.id if contact else None,
        status=status,
        notes=notes,
        **snapshot,
    )
    if status.counts_as_applied:
        application.date_applied = utcnow()
    session.add(application)
    job.is_saved = True
    session.flush()
    _record(application, "CREATED", f"Saved with status {status.label}.")
    session.flush()
    return application


def attach_contact(session: Session, application: Application, contact: Contact) -> Application:
    application.contact_id = contact.id
    application.contact_name = contact.display_name
    application.contact_title = contact.title
    application.contact_email = contact.email
    application.contact_profile_url = contact.profile_url or contact.search_url
    _record(application, "CONTACT_SET", f"Contact set to {contact.display_name}.")
    session.flush()
    return application


def set_status(
    session: Session,
    application: Application,
    status: ApplicationStatus,
    *,
    when: datetime | None = None,
    note: str | None = None,
) -> StatusChange:
    """Move an application to ``status``, stamping the matching date."""
    previous = application.status
    application.status = status
    application.last_status_change_at = when or utcnow()

    if status.counts_as_applied and application.date_applied is None:
        application.date_applied = when or utcnow()
    if status is ApplicationStatus.OUTREACH_SENT:
        if application.outreach_status is OutreachStatus.NOT_STARTED:
            application.outreach_status = OutreachStatus.EMAIL_SENT
        if application.outreach_sent_at is None:
            application.outreach_sent_at = when or utcnow()

    detail = f"{previous.label} → {status.label}"
    if note:
        detail = f"{detail}. {note}"
    _record(application, "STATUS_CHANGED", detail)
    session.flush()
    log.info("application.status", id=application.id, to=status.value)
    return StatusChange(application=application, previous=previous, current=status)


def set_outreach_status(
    session: Session,
    application: Application,
    status: OutreachStatus,
    *,
    when: datetime | None = None,
) -> Application:
    previous = application.outreach_status
    application.outreach_status = status
    if status.is_sent and application.outreach_sent_at is None:
        application.outreach_sent_at = when or utcnow()
    if not status.is_sent:
        application.outreach_sent_at = None
    _record(application, "OUTREACH_CHANGED", f"{previous.label} → {status.label}")
    session.flush()
    return application


def update_fields(
    session: Session,
    application: Application,
    *,
    notes: str | None = None,
    follow_up_date: date | None = None,
    clear_follow_up: bool = False,
    contact_email: str | None = None,
    date_applied: datetime | None = None,
) -> Application:
    changed: list[str] = []
    if notes is not None and notes != application.notes:
        application.notes = notes or None
        changed.append("notes")
    if clear_follow_up:
        application.follow_up_date = None
        changed.append("follow-up cleared")
    elif follow_up_date is not None and follow_up_date != application.follow_up_date:
        application.follow_up_date = follow_up_date
        changed.append(f"follow-up {follow_up_date:%d %b %Y}")
    if contact_email is not None:
        application.contact_email = contact_email.strip().lower() or None
        changed.append("contact email")
    if date_applied is not None:
        application.date_applied = date_applied
        changed.append("date applied")
    if changed:
        _record(application, "UPDATED", ", ".join(changed))
        session.flush()
    return application


def delete_application(session: Session, application: Application) -> None:
    if application.job is not None:
        application.job.is_saved = False
    session.delete(application)
    session.flush()


# --- Reporting ----------------------------------------------------------------


def dashboard_counts(session: Session, user: User) -> dict[str, int]:
    """The dashboard's headline numbers, each a single counted query."""

    def count(statement: Any) -> int:
        return int(session.scalar(statement) or 0)

    job_base = select(func.count(Job.id)).where(Job.user_id == user.id, Job.is_dismissed.is_(False))
    app_base = select(func.count(Application.id)).where(Application.user_id == user.id)

    displayable = [s for s in ValidationStatus if s.is_displayable]
    confirmed = [s for s in ValidationStatus if s.is_confirmed]

    return {
        "jobs_found": count(job_base.where(Job.validation_status.in_(displayable))),
        "valid_jobs": count(job_base.where(Job.validation_status.in_(confirmed))),
        "saved_jobs": count(job_base.where(Job.is_saved.is_(True))),
        "applications_submitted": count(
            app_base.where(
                Application.status.in_([s for s in ApplicationStatus if s.counts_as_applied])
            )
        ),
        "outreach_sent": count(
            app_base.where(
                Application.outreach_status.in_([s for s in OutreachStatus if s.is_sent])
            )
        ),
        "interviews": count(app_base.where(Application.status == ApplicationStatus.INTERVIEW)),
        "offers": count(app_base.where(Application.status == ApplicationStatus.OFFER)),
        "follow_ups_due": count(
            app_base.where(
                Application.follow_up_date.is_not(None),
                Application.follow_up_date <= utcnow().date(),
                Application.status.in_([s for s in ApplicationStatus if s.is_open]),
            )
        ),
        "in_pipeline": count(
            app_base.where(Application.status.in_([s for s in ApplicationStatus if s.is_open]))
        ),
    }


def pipeline_breakdown(session: Session, user: User) -> dict[str, int]:
    rows = session.execute(
        select(Application.status, func.count(Application.id))
        .where(Application.user_id == user.id)
        .group_by(Application.status)
    ).all()
    counts = {status.value: 0 for status in ApplicationStatus}
    for status, total in rows:
        counts[status.value if hasattr(status, "value") else str(status)] = int(total)
    return counts


def follow_ups_due(session: Session, user: User, *, limit: int = 20) -> list[Application]:
    return list(
        session.scalars(
            select(Application)
            .where(
                Application.user_id == user.id,
                Application.follow_up_date.is_not(None),
                Application.follow_up_date <= utcnow().date(),
                Application.status.in_([s for s in ApplicationStatus if s.is_open]),
            )
            .order_by(Application.follow_up_date)
            .limit(limit)
        ).all()
    )
