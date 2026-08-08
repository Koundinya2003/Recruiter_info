"""Observability: what ran, what it found, and what went wrong.

Scraping systems fail quietly. This endpoint exists so they cannot.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.config import settings
from app.db.base import utcnow
from app.models.company import Company
from app.models.crawl import CrawlRun, SourceRecord
from app.models.enums import EmailConfidence, VerificationStatus
from app.models.job import Job
from app.models.recruiter import Recruiter
from app.models.user import User
from app.models.verification import EmailVerification
from app.schemas.settings import (
    AdminOverview,
    CrawlRunOut,
    HealthOut,
    RateLimitEventOut,
    SourceRecordOut,
)
from app.security.rate_limit import get_domain_rate_limiter

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview", response_model=AdminOverview)
def overview(
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> AdminOverview:
    week_ago = utcnow() - timedelta(days=7)
    company_ids = select(Company.id).where(Company.user_id == user.id)

    recent_runs = list(
        session.scalars(
            select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(25)
        ).all()
    )

    status_counts: dict[str, int] = {
        row[0].value: row[1]
        for row in session.execute(
            select(CrawlRun.status, func.count(CrawlRun.id)).group_by(CrawlRun.status)
        ).all()
    }
    source_counts: dict[str, int] = {
        row[0].value: row[1]
        for row in session.execute(
            select(CrawlRun.source, func.count(CrawlRun.id)).group_by(CrawlRun.source)
        ).all()
    }
    verification_counts: dict[str, int] = {
        row[0].value: row[1]
        for row in session.execute(
            select(EmailVerification.status, func.count(EmailVerification.id)).group_by(
                EmailVerification.status
            )
        ).all()
    }

    recent_errors = []
    for run in recent_runs:
        for error in run.errors or []:
            recent_errors.append(
                {
                    "crawl_id": run.id,
                    "collector": run.collector,
                    "started_at": run.started_at.isoformat(),
                    **error,
                }
            )

    limiter = get_domain_rate_limiter()

    return AdminOverview(
        last_crawl=CrawlRunOut.model_validate(recent_runs[0]) if recent_runs else None,
        recent_crawls=[CrawlRunOut.model_validate(r) for r in recent_runs],
        sources_checked=source_counts,
        crawl_status_counts=status_counts,
        jobs_discovered_7d=session.scalar(
            select(func.count(Job.id)).where(
                Job.company_id.in_(company_ids), Job.discovered_at >= week_ago
            )
        )
        or 0,
        recruiters_discovered_7d=session.scalar(
            select(func.count(Recruiter.id)).where(
                Recruiter.company_id.in_(company_ids), Recruiter.discovered_at >= week_ago
            )
        )
        or 0,
        emails_discovered=session.scalar(
            select(func.count(Recruiter.id)).where(
                Recruiter.company_id.in_(company_ids),
                Recruiter.public_professional_email.is_not(None),
            )
        )
        or 0,
        inferred_emails=session.scalar(
            select(func.count(Recruiter.id)).where(
                Recruiter.company_id.in_(company_ids),
                Recruiter.email_confidence == EmailConfidence.LOW,
            )
        )
        or 0,
        verification_results=verification_counts,
        recent_errors=recent_errors[:40],
        rate_limit_events=[
            RateLimitEventOut(domain=e.domain, waited_seconds=round(e.waited_seconds, 3), at=e.at)
            for e in limiter.recent_events(30)
        ],
    )


@router.get("/crawls", response_model=list[CrawlRunOut])
def list_crawls(
    company_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[CrawlRun]:
    query = select(CrawlRun)
    if company_id is not None:
        query = query.where(CrawlRun.company_id == company_id)
    return list(
        session.scalars(
            query.order_by(CrawlRun.started_at.desc()).limit(limit).offset(offset)
        ).all()
    )


@router.get("/crawls/{crawl_id}/records", response_model=list[SourceRecordOut])
def crawl_records(
    crawl_id: int,
    accepted: bool | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[SourceRecord]:
    run = session.get(CrawlRun, crawl_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Crawl run not found")
    query = select(SourceRecord).where(SourceRecord.crawl_run_id == crawl_id)
    if accepted is not None:
        query = query.where(SourceRecord.accepted.is_(accepted))
    return list(session.scalars(query.order_by(SourceRecord.id).limit(limit)).all())


@router.get("/health", response_model=HealthOut)
def health(session: Session = Depends(db_session)) -> HealthOut:
    """Liveness plus a summary of which integrations are actually configured."""
    from sqlalchemy import text

    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001
        database = "unavailable"

    demo_records = 0
    if database == "ok":
        demo_records = (
            session.scalar(select(func.count(Company.id)).where(Company.is_demo.is_(True))) or 0
        )

    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        app=settings.app_name,
        environment=settings.app_env,
        database=database,
        ai_provider="openai_compatible" if settings.ai_configured else "offline_template",
        ai_configured=settings.ai_configured,
        email_verification_provider=settings.email_verification_provider,
        auth_enabled=settings.auth_enabled,
        scheduler_enabled=settings.scheduler_enabled,
        demo_records=demo_records,
    )


@router.get("/verifications", response_model=list[dict])
def recent_verifications(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[dict]:
    rows = session.scalars(
        select(EmailVerification).order_by(EmailVerification.checked_at.desc()).limit(limit)
    ).all()
    return [
        {
            "email": v.email,
            "status": v.status.value,
            "confidence": v.confidence,
            "provider": v.provider,
            "reason": v.reason,
            "recruiter_id": v.recruiter_id,
            "checked_at": v.checked_at.isoformat(),
            "valid": v.status is VerificationStatus.VALID,
        }
        for v in rows
    ]
