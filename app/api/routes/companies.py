"""Company tracking routes."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.db.base import utcnow
from app.models.company import Company
from app.models.enums import CrawlTrigger, JobStatus, VerificationStatus
from app.models.job import Job
from app.models.recruiter import Recruiter
from app.models.signal import HiringSignal
from app.models.user import User
from app.schemas.company import (
    CompanyCreate,
    CompanyDetail,
    CompanyOut,
    CompanyStats,
    CompanyUpdate,
    HiringSignalOut,
    ScanRequest,
    ScanResult,
)
from app.services.scan import scan_company
from app.services.scoring.hiring_activity import timeline
from app.services.scoring.weights import load_weights
from app.utils.text import normalize_company_name

router = APIRouter(prefix="/companies", tags=["companies"])


def _get_company(session: Session, user: User, company_id: int) -> Company:
    company = session.scalar(
        select(Company).where(Company.id == company_id, Company.user_id == user.id)
    )
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("", response_model=list[CompanyOut])
def list_companies(
    q: str | None = None,
    active: bool | None = None,
    industry: str | None = None,
    include_demo: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[Company]:
    query = select(Company).where(Company.user_id == user.id)
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Company.company_name.ilike(pattern),
                Company.company_domain.ilike(pattern),
                Company.industry.ilike(pattern),
            )
        )
    if active is not None:
        query = query.where(Company.active.is_(active))
    if industry:
        query = query.where(Company.industry.ilike(f"%{industry}%"))
    if not include_demo:
        query = query.where(Company.is_demo.is_(False))

    query = query.order_by(Company.hiring_activity_score.desc(), Company.company_name)
    return list(session.scalars(query.limit(limit).offset(offset)).all())


@router.post("", response_model=CompanyOut, status_code=status.HTTP_201_CREATED)
def create_company(
    payload: CompanyCreate,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Company:
    company = Company(
        user_id=user.id,
        company_name=payload.company_name,
        normalized_name=normalize_company_name(payload.company_name),
        company_domain=payload.company_domain,
        career_page_url=payload.career_page_url,
        industry=payload.industry,
        priority=payload.priority,
        active=payload.active,
        notes=payload.notes,
    )
    session.add(company)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail=f"'{payload.company_name}' is already being tracked."
        ) from exc
    return company


@router.get("/{company_id}", response_model=CompanyDetail)
def get_company(
    company_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> CompanyDetail:
    company = _get_company(session, user, company_id)
    weights = load_weights(session, user.id)
    threshold = float(weights.option("relevance_threshold"))

    open_jobs = session.scalar(
        select(func.count(Job.id)).where(
            Job.company_id == company.id, Job.status == JobStatus.OPEN
        )
    )
    relevant_jobs = session.scalar(
        select(func.count(Job.id)).where(
            Job.company_id == company.id,
            Job.status == JobStatus.OPEN,
            Job.relevance_score >= threshold,
        )
    )
    recruiters = session.scalar(
        select(func.count(Recruiter.id)).where(Recruiter.company_id == company.id)
    )
    contactable = session.scalar(
        select(func.count(Recruiter.id)).where(
            Recruiter.company_id == company.id,
            Recruiter.public_professional_email.is_not(None),
            Recruiter.do_not_contact.is_(False),
        )
    )
    verified = session.scalar(
        select(func.count(Recruiter.id)).where(
            Recruiter.company_id == company.id,
            Recruiter.email_verified.is_(True),
            Recruiter.email_verification_status == VerificationStatus.VALID,
        )
    )
    signals_7d = session.scalar(
        select(func.count(HiringSignal.id)).where(
            HiringSignal.company_id == company.id,
            HiringSignal.detected_at >= utcnow() - timedelta(days=7),
        )
    )

    recent_signals = list(
        session.scalars(
            select(HiringSignal)
            .where(HiringSignal.company_id == company.id)
            .order_by(HiringSignal.detected_at.desc())
            .limit(15)
        ).all()
    )

    detail = CompanyDetail.model_validate(company)
    detail.stats = CompanyStats(
        open_jobs=open_jobs or 0,
        relevant_jobs=relevant_jobs or 0,
        recruiters=recruiters or 0,
        contactable_recruiters=contactable or 0,
        verified_emails=verified or 0,
        signals_7d=signals_7d or 0,
    )
    detail.recent_signals = [HiringSignalOut.model_validate(s) for s in recent_signals]
    detail.timeline = timeline(session, company.id, days=45)
    detail.hiring_activity_breakdown = _hiring_breakdown(session, company)
    return detail


def _hiring_breakdown(session: Session, company: Company) -> dict:
    from app.services.scoring.hiring_activity import gather_activity, score_hiring_activity

    weights = load_weights(session, company.user_id)
    data = gather_activity(session, company, weights)
    result, _ = score_hiring_activity(data, weights)
    return result.to_dict()


@router.patch("/{company_id}", response_model=CompanyOut)
def update_company(
    company_id: int,
    payload: CompanyUpdate,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Company:
    company = _get_company(session, user, company_id)
    data = payload.model_dump(exclude_unset=True)
    if "company_name" in data and data["company_name"]:
        company.normalized_name = normalize_company_name(data["company_name"])
    for field, value in data.items():
        setattr(company, field, value)
    session.flush()
    return company


@router.post("/{company_id}/pause", response_model=CompanyOut)
def pause_company(
    company_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Company:
    company = _get_company(session, user, company_id)
    company.active = False
    session.flush()
    return company


@router.post("/{company_id}/resume", response_model=CompanyOut)
def resume_company(
    company_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Company:
    company = _get_company(session, user, company_id)
    company.active = True
    session.flush()
    return company


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(
    company_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> None:
    company = _get_company(session, user, company_id)
    session.delete(company)
    session.flush()


@router.post("/{company_id}/scan", response_model=ScanResult)
def scan(
    company_id: int,
    payload: ScanRequest | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ScanResult:
    """Run a discovery pass for one company, right now."""
    company = _get_company(session, user, company_id)
    payload = payload or ScanRequest()
    summary = scan_company(
        session,
        company,
        trigger=CrawlTrigger.MANUAL,
        discover_recruiters=payload.discover_recruiters,
        infer_emails=payload.infer_emails,
        extra_recruiter_urls=payload.extra_recruiter_urls,
    )
    return ScanResult(**summary.to_dict())


@router.get("/{company_id}/history", response_model=list[HiringSignalOut])
def company_history(
    company_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[HiringSignal]:
    company = _get_company(session, user, company_id)
    return list(
        session.scalars(
            select(HiringSignal)
            .where(HiringSignal.company_id == company.id)
            .order_by(HiringSignal.detected_at.desc())
            .limit(limit)
        ).all()
    )
