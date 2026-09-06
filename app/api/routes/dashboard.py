"""The dashboard: where everything stands right now."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.api.serializers import application_out, job_out
from app.models.enums import ValidationStatus
from app.models.job import Job
from app.models.search import JobSearch, SearchRun
from app.models.user import User
from app.providers.registry import provider_statuses
from app.schemas.application import DashboardOut
from app.services import applications as tracker

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    limit: int = Query(default=8, ge=1, le=50),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> DashboardOut:
    counts = tracker.dashboard_counts(session, user)
    statuses = provider_statuses()

    recent = session.scalars(
        select(Job)
        .where(
            Job.user_id == user.id,
            Job.is_dismissed.is_(False),
            Job.validation_status.in_([s for s in ValidationStatus if s.is_displayable]),
        )
        .order_by(Job.discovered_at.desc(), Job.relevance_score.desc())
        .limit(limit)
    ).all()

    last_search: dict | None = None
    search_row = session.scalars(
        select(JobSearch)
        .where(JobSearch.user_id == user.id, JobSearch.last_run_at.is_not(None))
        .order_by(JobSearch.last_run_at.desc())
        .limit(1)
    ).first()
    if search_row is not None:
        run = session.scalars(
            select(SearchRun)
            .where(SearchRun.search_id == search_row.id)
            .order_by(SearchRun.started_at.desc())
            .limit(1)
        ).first()
        last_search = {
            "id": search_row.id,
            "query": search_row.raw_query,
            "label": search_row.label,
            "last_run_at": search_row.last_run_at.isoformat() if search_row.last_run_at else None,
            "status": run.status.value if run else None,
            "raw_found": run.raw_found if run else 0,
            "kept": (run.validated + run.unverified) if run else 0,
            "duplicates_dropped": run.duplicates_dropped if run else 0,
            "irrelevant_dropped": run.irrelevant_dropped if run else 0,
            "rejected": run.rejected if run else 0,
        }

    return DashboardOut(
        **counts,
        pipeline=tracker.pipeline_breakdown(session, user),
        recent_jobs=[job_out(session, user, job).model_dump() for job in recent],
        due_follow_ups=[
            application_out(a) for a in tracker.follow_ups_due(session, user, limit=limit)
        ],
        last_search=last_search,
        sources_usable=sum(1 for s in statuses if s.usable),
        sources_total=len(statuses),
    )
