"""The job library: everything discovery has found and validated."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.api.serializers import job_out
from app.collectors.http_client import SafeHTTPClient
from app.db.base import utcnow
from app.models.enums import ValidationStatus
from app.models.job import Job
from app.models.user import User
from app.schemas.common import Message
from app.schemas.job import JobActionRequest, JobDetail, JobOut, ValidationOut
from app.services.validation import validate_posting

router = APIRouter(prefix="/jobs", tags=["jobs"])

DISPLAYABLE = [s for s in ValidationStatus if s.is_displayable]


def _get_job(session: Session, user: User, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("", response_model=list[JobOut])
def list_jobs(
    q: str | None = Query(default=None, max_length=200),
    saved_only: bool = False,
    confirmed_only: bool = False,
    include_dismissed: bool = False,
    company: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[JobOut]:
    statement = select(Job).where(
        Job.user_id == user.id, Job.validation_status.in_(DISPLAYABLE)
    )
    if not include_dismissed:
        statement = statement.where(Job.is_dismissed.is_(False))
    if saved_only:
        statement = statement.where(Job.is_saved.is_(True))
    if confirmed_only:
        statement = statement.where(
            Job.validation_status.in_([s for s in ValidationStatus if s.is_confirmed])
        )
    if company:
        statement = statement.where(Job.company_name.ilike(f"%{company.strip()}%"))
    if q:
        term = f"%{q.strip()}%"
        statement = statement.where(
            or_(Job.title.ilike(term), Job.company_name.ilike(term), Job.location.ilike(term))
        )
    statement = statement.order_by(Job.relevance_score.desc(), Job.discovered_at.desc())
    jobs = session.scalars(statement.limit(limit).offset(offset)).all()
    return [job_out(session, user, job) for job in jobs]


@router.get("/count")
def count_jobs(
    session: Session = Depends(db_session), user: User = Depends(current_user)
) -> dict[str, int]:
    total = session.scalar(
        select(func.count(Job.id)).where(
            Job.user_id == user.id,
            Job.is_dismissed.is_(False),
            Job.validation_status.in_(DISPLAYABLE),
        )
    )
    confirmed = session.scalar(
        select(func.count(Job.id)).where(
            Job.user_id == user.id,
            Job.is_dismissed.is_(False),
            Job.validation_status.in_([s for s in ValidationStatus if s.is_confirmed]),
        )
    )
    return {"total": int(total or 0), "confirmed": int(confirmed or 0)}


@router.get("/{job_id}", response_model=JobDetail)
def get_job(
    job_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> JobDetail:
    job = _get_job(session, user, job_id)
    return job_out(session, user, job, detail=True)  # type: ignore[return-value]


@router.patch("/{job_id}", response_model=JobOut)
def update_job(
    job_id: int,
    request: JobActionRequest,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> JobOut:
    job = _get_job(session, user, job_id)
    if request.dismissed is not None:
        job.is_dismissed = request.dismissed
    if request.saved is not None:
        job.is_saved = request.saved
    session.commit()
    return job_out(session, user, job)


@router.post("/{job_id}/revalidate", response_model=ValidationOut)
def revalidate(
    job_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ValidationOut:
    """Re-check a posting on demand — useful before applying to an older find."""
    job = _get_job(session, user, job_id)
    with SafeHTTPClient(max_pages=4) as client:
        outcome = validate_posting(
            client,
            url=job.job_url,
            title=job.title,
            company_name=job.company_name,
            source=job.source,
            board_token=str(job.payload.get("board") or "") or None,
        )
    job.validation_status = outcome.status
    job.validation_checks = outcome.checks
    job.validation_reason = outcome.reason
    job.validated_at = utcnow()
    job.http_status = outcome.http_status
    job.final_url = outcome.final_url or job.final_url
    session.commit()
    return ValidationOut(
        status=outcome.status.value,
        label=outcome.status.label,
        reason=outcome.reason,
        confirmed=outcome.status.is_confirmed,
        checks=outcome.checks,
        checked_at=job.validated_at,
        http_status=outcome.http_status,
    )


@router.delete("/{job_id}", response_model=Message)
def delete_job(
    job_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Message:
    job = _get_job(session, user, job_id)
    session.delete(job)
    session.commit()
    return Message(detail="Job removed from your library.")
