"""The application tracker."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.api.serializers import application_out
from app.models.application import Application
from app.models.contact import Contact
from app.models.enums import ApplicationStatus, OutreachStatus
from app.models.job import Job
from app.models.user import User
from app.schemas.application import (
    ApplicationCreate,
    ApplicationDetail,
    ApplicationEventOut,
    ApplicationOut,
    ApplicationUpdate,
)
from app.schemas.common import Message
from app.services import applications as tracker

router = APIRouter(prefix="/applications", tags=["applications"])


def _get(session: Session, user: User, application_id: int) -> Application:
    application = session.get(Application, application_id)
    if application is None or application.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


def _detail(application: Application) -> ApplicationDetail:
    return ApplicationDetail(
        **application_out(application).model_dump(),
        events=[ApplicationEventOut.model_validate(e) for e in application.events],
    )


@router.get("", response_model=list[ApplicationOut])
def list_applications(
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
    outreach: OutreachStatus | None = None,
    open_only: bool = False,
    due_only: bool = False,
    q: str | None = Query(default=None, max_length=200),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[ApplicationOut]:
    statement = select(Application).where(Application.user_id == user.id)
    if status_filter is not None:
        statement = statement.where(Application.status == status_filter)
    if outreach is not None:
        statement = statement.where(Application.outreach_status == outreach)
    if open_only:
        statement = statement.where(
            Application.status.in_([s for s in ApplicationStatus if s.is_open])
        )
    if q:
        term = f"%{q.strip()}%"
        statement = statement.where(
            Application.job_title.ilike(term) | Application.company_name.ilike(term)
        )
    rows = session.scalars(statement.order_by(Application.updated_at.desc())).all()
    if due_only:
        rows = [row for row in rows if row.follow_up_due]
    return [application_out(row) for row in rows]


@router.post("", response_model=ApplicationDetail, status_code=status.HTTP_201_CREATED)
def create_application(
    request: ApplicationCreate,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ApplicationDetail:
    """Track a job. This records your intent — it does not apply for you."""
    job = session.get(Job, request.job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    contact = session.get(Contact, request.contact_id) if request.contact_id else None
    if request.contact_id and contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    application = tracker.save_job(
        session, user, job, contact=contact, status=request.status, notes=request.notes
    )
    session.commit()
    return _detail(application)


@router.get("/{application_id}", response_model=ApplicationDetail)
def get_application(
    application_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ApplicationDetail:
    return _detail(_get(session, user, application_id))


@router.patch("/{application_id}", response_model=ApplicationDetail)
def update_application(
    application_id: int,
    request: ApplicationUpdate,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ApplicationDetail:
    application = _get(session, user, application_id)

    if request.contact_id is not None:
        contact = session.get(Contact, request.contact_id)
        if contact is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        tracker.attach_contact(session, application, contact)
    if request.status is not None and request.status != application.status:
        tracker.set_status(session, application, request.status)
    if request.outreach_status is not None and request.outreach_status != application.outreach_status:
        tracker.set_outreach_status(session, application, request.outreach_status)

    tracker.update_fields(
        session,
        application,
        notes=request.notes,
        follow_up_date=request.follow_up_date,
        clear_follow_up=request.clear_follow_up,
        contact_email=request.contact_email,
        date_applied=request.date_applied,
    )
    session.commit()
    return _detail(application)


@router.post("/{application_id}/mark-applied", response_model=ApplicationDetail)
def mark_applied(
    application_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ApplicationDetail:
    application = _get(session, user, application_id)
    tracker.set_status(session, application, ApplicationStatus.APPLIED)
    session.commit()
    return _detail(application)


@router.post("/{application_id}/mark-outreach-sent", response_model=ApplicationDetail)
def mark_outreach_sent(
    application_id: int,
    channel: OutreachStatus = Query(default=OutreachStatus.EMAIL_SENT),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ApplicationDetail:
    """Record that you contacted someone. This tool never sends the message."""
    application = _get(session, user, application_id)
    tracker.set_outreach_status(session, application, channel)
    if application.status is ApplicationStatus.SAVED:
        tracker.set_status(session, application, ApplicationStatus.OUTREACH_SENT)
    session.commit()
    return _detail(application)


@router.get("/{application_id}/history", response_model=list[ApplicationEventOut])
def history(
    application_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[ApplicationEventOut]:
    application = _get(session, user, application_id)
    return [ApplicationEventOut.model_validate(e) for e in application.events]


@router.delete("/{application_id}", response_model=Message)
def delete_application(
    application_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Message:
    application = _get(session, user, application_id)
    tracker.delete_application(session, application)
    session.commit()
    return Message(detail="Application removed from your tracker.")
