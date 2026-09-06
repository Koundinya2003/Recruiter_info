"""Turning ORM rows into the API's response shapes.

Kept out of the route modules so job serialisation is written once: the same
job shape is returned by search, by the job library and by the dashboard.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.contact import Contact, JobContact
from app.models.job import Job
from app.models.user import User
from app.schemas.application import ApplicationOut
from app.schemas.job import ContactOut, JobDetail, JobOut, ValidationOut


def contact_out(contact: Contact, link: JobContact | None = None) -> ContactOut:
    return ContactOut(
        id=contact.id,
        name=contact.name,
        display_name=contact.display_name,
        title=contact.title,
        company_name=contact.company_name,
        role=contact.role.value,
        role_label=contact.role.label,
        is_person=contact.is_person,
        profile_url=contact.profile_url,
        search_url=contact.search_url,
        email=contact.email,
        email_status=contact.email_status.value,
        email_status_label=contact.email_status.label,
        email_verification=contact.email_verification.value,
        source=contact.source.value,
        source_label=contact.source.label,
        source_url=contact.source_url,
        source_excerpt=contact.source_excerpt,
        confidence=contact.confidence,
        rationale=link.rationale if link else None,
        rank=link.rank if link else None,
    )


def _validation_out(job: Job) -> ValidationOut:
    return ValidationOut(
        status=job.validation_status.value,
        label=job.validation_status.label,
        reason=job.validation_reason,
        confirmed=job.validation_status.is_confirmed,
        checks=job.validation_checks or {},
        checked_at=job.validated_at,
        http_status=job.http_status,
    )


def _job_contacts(session: Session, job: Job) -> list[ContactOut]:
    rows = session.execute(
        select(JobContact, Contact)
        .join(Contact, Contact.id == JobContact.contact_id)
        .where(JobContact.job_id == job.id, Contact.is_archived.is_(False))
        .order_by(JobContact.rank)
    ).all()
    return [contact_out(contact, link) for link, contact in rows]


def _application_for(session: Session, user: User, job: Job) -> Application | None:
    return session.scalar(
        select(Application).where(Application.user_id == user.id, Application.job_id == job.id)
    )


def job_out(session: Session, user: User, job: Job, *, detail: bool = False) -> JobOut:
    application = _application_for(session, user, job)
    common: dict[str, Any] = {
        "id": job.id,
        "title": job.title,
        "company_name": job.company_name,
        "location": job.location,
        "is_remote": job.is_remote,
        "experience_label": job.experience_label,
        "min_years": job.min_years,
        "max_years": job.max_years,
        "employment_type": job.employment_type,
        "department": job.department,
        "salary_text": job.salary_text,
        "summary": job.summary,
        "job_url": job.final_url or job.job_url,
        "apply_url": job.apply_url,
        "final_url": job.final_url,
        "source": job.source.value,
        "source_label": job.source.label,
        "posted_at": job.posted_at,
        "discovered_at": job.discovered_at,
        "age_days": job.age_days,
        "validation": _validation_out(job),
        "relevance_score": job.relevance_score,
        "match_reasons": job.match_reasons or [],
        "is_saved": job.is_saved,
        "is_dismissed": job.is_dismissed,
        "contacts": _job_contacts(session, job),
        "application_id": application.id if application else None,
        "application_status": application.status.value if application else None,
    }
    if not detail:
        return JobOut(**common)
    return JobDetail(
        **common,
        description=job.description,
        relevance_breakdown=job.relevance_breakdown or {},
        search_id=job.search_id,
        company_id=job.company_id,
    )


def application_out(application: Application) -> ApplicationOut:
    return ApplicationOut(
        id=application.id,
        job_id=application.job_id,
        contact_id=application.contact_id,
        job_title=application.job_title,
        company_name=application.company_name,
        location=application.location,
        job_url=application.job_url,
        source=application.source,
        contact_name=application.contact_name,
        contact_title=application.contact_title,
        contact_email=application.contact_email,
        contact_profile_url=application.contact_profile_url,
        status=application.status.value,
        status_label=application.status.label,
        outreach_status=application.outreach_status.value,
        outreach_status_label=application.outreach_status.label,
        date_found=application.date_found,
        date_applied=application.date_applied,
        outreach_sent_at=application.outreach_sent_at,
        follow_up_date=application.follow_up_date,
        follow_up_due=application.follow_up_due,
        notes=application.notes,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )
