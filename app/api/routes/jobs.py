"""Job listing, filtering and detail routes."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import UnaryExpression

from app.api.deps import current_user, db_session
from app.db.base import utcnow
from app.models.company import Company
from app.models.enums import JobStatus, SourceType
from app.models.job import Job
from app.models.user import User
from app.schemas.common import ScoreBreakdownOut
from app.schemas.job import JobDetail, JobOut
from app.services.jobs.ingest import rescore_company_jobs
from app.services.scoring.context import build_context
from app.services.scoring.weights import load_weights

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(
    q: str | None = None,
    company_id: int | None = None,
    status: JobStatus | None = None,
    source: SourceType | None = None,
    min_relevance: float | None = Query(default=None, ge=0, le=100),
    max_age_hours: float | None = Query(default=None, ge=0),
    location: str | None = None,
    include_demo: bool = True,
    sort: str = Query(default="relevance", pattern="^(relevance|posted|discovered|title)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[Job]:
    query = select(Job).join(Company).where(Company.user_id == user.id)

    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(Job.title.ilike(pattern), Job.description.ilike(pattern), Company.company_name.ilike(pattern))
        )
    if company_id is not None:
        query = query.where(Job.company_id == company_id)
    if status is not None:
        query = query.where(Job.status == status)
    if source is not None:
        query = query.where(Job.source == source)
    if min_relevance is not None:
        query = query.where(Job.relevance_score >= min_relevance)
    if location:
        query = query.where(Job.location.ilike(f"%{location.strip()}%"))
    if not include_demo:
        query = query.where(Job.is_demo.is_(False))
    if max_age_hours is not None:
        cutoff = utcnow() - timedelta(hours=max_age_hours)
        query = query.where(
            or_(Job.posted_at >= cutoff, Job.posted_at.is_(None) & (Job.discovered_at >= cutoff))
        )

    order: dict[str, list[UnaryExpression]] = {
        "relevance": [Job.relevance_score.desc(), Job.discovered_at.desc()],
        "posted": [Job.posted_at.desc().nullslast(), Job.discovered_at.desc()],
        "discovered": [Job.discovered_at.desc()],
        "title": [Job.title.asc()],
    }
    return list(
        session.scalars(query.order_by(*order[sort]).limit(limit).offset(offset)).all()
    )


@router.get("/{job_id}", response_model=JobDetail)
def get_job(
    job_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> JobDetail:
    job = session.scalar(
        select(Job)
        .join(Company)
        .where(Job.id == job_id, Company.user_id == user.id)
        .options(selectinload(Job.recruiter_links), selectinload(Job.company))
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    detail = JobDetail.model_validate(job)
    detail.company_name = job.company.company_name if job.company else None
    detail.relevance = ScoreBreakdownOut.from_json(job.relevance_breakdown)
    detail.age_hours = job.age_hours
    detail.linked_recruiters = [
        {
            "recruiter_id": link.recruiter_id,
            "name": link.recruiter.name,
            "title": link.recruiter.title,
            "relation": link.relation.value,
            "confidence": link.confidence,
            "rationale": link.rationale,
            "email": link.recruiter.public_professional_email,
            "email_is_inferred": link.recruiter.email_is_inferred,
            "email_verified": link.recruiter.email_verified,
            "recruiter_score": link.recruiter.relevance_score,
            "do_not_contact": link.recruiter.do_not_contact,
        }
        for link in job.recruiter_links
    ]
    return detail


@router.post("/rescore", response_model=dict)
def rescore(
    company_id: int | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> dict:
    """Re-run relevance scoring after changing the taxonomy or weights."""
    context = build_context(session, user.id)
    weights = load_weights(session, user.id)
    query = select(Company).where(Company.user_id == user.id)
    if company_id is not None:
        query = query.where(Company.id == company_id)

    total = 0
    for company in session.scalars(query).all():
        total += rescore_company_jobs(session, company, context, weights)
    return {"jobs_rescored": total}
