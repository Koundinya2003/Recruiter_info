"""Contacts discovered for the companies behind your jobs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.api.serializers import contact_out
from app.collectors.http_client import SafeHTTPClient
from app.db.base import utcnow
from app.models.company import Company
from app.models.contact import Contact, JobContact
from app.models.enums import VerificationStatus
from app.models.job import Job
from app.models.user import User
from app.schemas.common import Message
from app.schemas.job import ContactOut
from app.services.contacts import discover_contacts
from app.services.discovery import link_contacts, persist_contact
from app.services.email.verifier import get_verifier

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _owned_contact(session: Session, user: User, contact_id: int) -> Contact:
    """A contact reachable from one of this user's jobs."""
    contact = session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    owns = session.scalar(
        select(Job.id)
        .join(JobContact, JobContact.job_id == Job.id)
        .where(JobContact.contact_id == contact.id, Job.user_id == user.id)
        .limit(1)
    )
    if owns is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


@router.get("", response_model=list[ContactOut])
def list_contacts(
    company: str | None = Query(default=None, max_length=200),
    with_email_only: bool = False,
    people_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[ContactOut]:
    statement = (
        select(Contact)
        .join(JobContact, JobContact.contact_id == Contact.id)
        .join(Job, Job.id == JobContact.job_id)
        .where(Job.user_id == user.id, Contact.is_archived.is_(False))
        .distinct()
    )
    if company:
        statement = statement.where(Contact.company_name.ilike(f"%{company.strip()}%"))
    if with_email_only:
        statement = statement.where(Contact.email.is_not(None))
    if people_only:
        statement = statement.where(Contact.name.is_not(None))
    contacts = session.scalars(statement.limit(limit)).all()
    return [contact_out(contact) for contact in contacts]


@router.get("/job/{job_id}", response_model=list[ContactOut])
def contacts_for_job(
    job_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[ContactOut]:
    job = session.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    rows = session.execute(
        select(JobContact, Contact)
        .join(Contact, Contact.id == JobContact.contact_id)
        .where(JobContact.job_id == job.id, Contact.is_archived.is_(False))
        .order_by(JobContact.rank)
    ).all()
    return [contact_out(contact, link) for link, contact in rows]


@router.post("/job/{job_id}/discover", response_model=list[ContactOut])
def discover_for_job(
    job_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[ContactOut]:
    """Look again for contacts at this job's company."""
    job = session.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    company = session.get(Company, job.company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    with SafeHTTPClient(max_pages=8) as client:
        result = discover_contacts(
            client,
            company_name=company.name,
            company_domain=company.domain,
            careers_url=company.careers_url,
            posting_url=job.final_url or job.job_url,
            posting_description=job.description,
            role_hint=f"recruiter talent acquisition {job.title}",
        )
    company.contacts_checked_at = utcnow()
    contacts = [persist_contact(session, company, c) for c in result.contacts]
    session.flush()
    link_contacts(session, job, [(c, None) for c in contacts[:6]])
    session.commit()
    return contacts_for_job(job_id, session, user)


@router.post("/{contact_id}/verify-email", response_model=ContactOut)
def verify_email(
    contact_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ContactOut:
    """Check that a published address is deliverable. Never sends anything."""
    contact = _owned_contact(session, user, contact_id)
    if not contact.email:
        raise HTTPException(status_code=400, detail="This contact has no address to check.")
    result = get_verifier().verify(contact.email)
    contact.email_verification = VerificationStatus(result.status)
    contact.email_verified_at = utcnow()
    session.commit()
    return contact_out(contact)


@router.delete("/{contact_id}", response_model=Message)
def archive_contact(
    contact_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Message:
    contact = _owned_contact(session, user, contact_id)
    contact.is_archived = True
    session.commit()
    return Message(detail="Contact hidden from your job list.")
