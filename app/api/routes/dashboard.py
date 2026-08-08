"""Dashboard overview and cross-entity search."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.db.base import utcnow
from app.models.company import Company
from app.models.enums import EmailConfidence, JobStatus, OutreachStatus, VerificationStatus
from app.models.job import Job
from app.models.outreach import OutreachLead
from app.models.recruiter import Recruiter
from app.models.signal import HiringSignal
from app.models.user import User
from app.schemas.outreach import DashboardOut, DashboardStats, OpportunityOut
from app.services.scoring.outreach_priority import ContactTodayFilters, contact_today
from app.services.scoring.weights import load_weights

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    limit: int = Query(default=15, ge=1, le=50),
    include_demo: bool = True,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> DashboardOut:
    weights = load_weights(session, user.id)
    threshold = float(weights.option("relevance_threshold"))
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    company_filter = [Company.user_id == user.id]
    if not include_demo:
        company_filter.append(Company.is_demo.is_(False))

    def scalar(query: Any) -> int:
        return session.scalar(query) or 0

    company_ids = select(Company.id).where(*company_filter)

    stats = DashboardStats(
        hiring_signals_today=scalar(
            select(func.count(HiringSignal.id)).where(
                HiringSignal.company_id.in_(company_ids), HiringSignal.detected_at >= day_ago
            )
        ),
        relevant_jobs=scalar(
            select(func.count(Job.id)).where(
                Job.company_id.in_(company_ids),
                Job.status == JobStatus.OPEN,
                Job.relevance_score >= threshold,
            )
        ),
        recruiters_found=scalar(
            select(func.count(Recruiter.id)).where(Recruiter.company_id.in_(company_ids))
        ),
        verified_emails=scalar(
            select(func.count(Recruiter.id)).where(
                Recruiter.company_id.in_(company_ids),
                Recruiter.email_verified.is_(True),
                Recruiter.email_verification_status == VerificationStatus.VALID,
            )
        ),
        companies_tracked=scalar(select(func.count(Company.id)).where(*company_filter)),
        jobs_tracked=scalar(select(func.count(Job.id)).where(Job.company_id.in_(company_ids))),
        leads_awaiting_approval=scalar(
            select(func.count(OutreachLead.id)).where(
                OutreachLead.user_id == user.id,
                OutreachLead.status.in_([OutreachStatus.NEW, OutreachStatus.REVIEWED]),
            )
        ),
        contacted_this_week=scalar(
            select(func.count(OutreachLead.id)).where(
                OutreachLead.user_id == user.id, OutreachLead.last_contact_at >= week_ago
            )
        ),
        inferred_emails=scalar(
            select(func.count(Recruiter.id)).where(
                Recruiter.company_id.in_(company_ids),
                Recruiter.email_confidence == EmailConfidence.LOW,
            )
        ),
        demo_mode=scalar(select(func.count(Company.id)).where(Company.user_id == user.id, Company.is_demo.is_(True))) > 0,
    )

    opportunities = contact_today(
        session,
        user.id,
        ContactTodayFilters(limit=limit, include_demo=include_demo),
        weights=weights,
    )
    stats.high_priority_leads = sum(1 for o in opportunities if o.priority >= 85)

    return DashboardOut(
        stats=stats,
        opportunities=[OpportunityOut(**o.to_dict()) for o in opportunities],
        generated_at=now,
    )


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=10, ge=1, le=50),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> dict[str, list[dict[str, Any]]]:
    """Search across companies, jobs and recruiters at once."""
    pattern = f"%{q.strip()}%"

    companies = session.scalars(
        select(Company)
        .where(
            Company.user_id == user.id,
            or_(
                Company.company_name.ilike(pattern),
                Company.industry.ilike(pattern),
                Company.company_domain.ilike(pattern),
            ),
        )
        .limit(limit)
    ).all()

    jobs = session.scalars(
        select(Job)
        .join(Company)
        .where(
            Company.user_id == user.id,
            or_(Job.title.ilike(pattern), Job.location.ilike(pattern)),
        )
        .order_by(Job.relevance_score.desc())
        .limit(limit)
    ).all()

    recruiters = session.scalars(
        select(Recruiter)
        .join(Company)
        .where(
            Company.user_id == user.id,
            or_(
                Recruiter.name.ilike(pattern),
                Recruiter.title.ilike(pattern),
                Recruiter.public_professional_email.ilike(pattern),
            ),
        )
        .order_by(Recruiter.relevance_score.desc())
        .limit(limit)
    ).all()

    return {
        "companies": [
            {
                "id": c.id,
                "name": c.company_name,
                "industry": c.industry,
                "hiring_activity_score": c.hiring_activity_score,
                "is_demo": c.is_demo,
            }
            for c in companies
        ],
        "jobs": [
            {
                "id": j.id,
                "title": j.title,
                "company_id": j.company_id,
                "location": j.location,
                "relevance_score": j.relevance_score,
                "status": j.status.value,
                "is_demo": j.is_demo,
            }
            for j in jobs
        ],
        "recruiters": [
            {
                "id": r.id,
                "name": r.name,
                "title": r.title,
                "company_id": r.company_id,
                "relevance_score": r.relevance_score,
                "email": r.public_professional_email,
                "email_is_inferred": r.email_is_inferred,
                "do_not_contact": r.do_not_contact,
                "is_demo": r.is_demo,
            }
            for r in recruiters
        ],
    }
