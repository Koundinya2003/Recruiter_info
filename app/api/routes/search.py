"""Searching for jobs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.api.serializers import job_out
from app.config import settings
from app.logging_config import get_logger
from app.models.job import Job
from app.models.search import JobSearch, SearchRun
from app.models.user import User
from app.providers.registry import provider_statuses
from app.schemas.common import Message
from app.schemas.job import JobOut
from app.schemas.search import (
    ParsedQueryOut,
    ParseRequest,
    ProviderStatusOut,
    SavedSearchOut,
    SearchRequest,
    SearchResultOut,
    SearchRunOut,
)
from app.search.parser import parse_query
from app.search.query import JobQuery
from app.services.discovery import run_search

log = get_logger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


def _apply_overrides(query: JobQuery, request: SearchRequest) -> JobQuery:
    """Let the user correct what the parser understood, field by field."""
    for name in ("titles", "locations", "companies", "industries", "keywords", "exclusions"):
        value = getattr(request, name)
        if value is not None:
            setattr(query, name, value)
    if request.min_years is not None:
        query.min_years = request.min_years
    if request.max_years is not None:
        query.max_years = request.max_years
    if request.remote_only is not None:
        query.remote_only = request.remote_only
    if request.max_age_days is not None:
        query.max_age_days = request.max_age_days
    query.limit = request.limit or settings.search_result_limit
    return query


@router.post("/parse", response_model=ParsedQueryOut)
def parse(request: ParseRequest) -> ParsedQueryOut:
    """Show what a search request is understood to mean, without running it."""
    query = parse_query(request.query, use_llm=request.use_llm)
    return ParsedQueryOut(**query.to_dict())


@router.post("", response_model=SearchResultOut)
def search(
    request: SearchRequest,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> SearchResultOut:
    """Run a search: find postings, validate them, and find contacts."""
    if not any(s.usable for s in provider_statuses()):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No job source is usable. Configure at least one under Sources — several "
                "need no credentials at all."
            ),
        )

    query = _apply_overrides(parse_query(request.query, use_llm=request.use_llm), request)
    if query.is_empty:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not work out a role to search for. Try naming the job title.",
        )

    outcome = run_search(
        session,
        user,
        query,
        find_contacts=request.find_contacts,
        only_providers=request.providers,
    )
    session.commit()
    return SearchResultOut(**outcome.to_dict())


@router.get("/{search_id}/jobs", response_model=list[JobOut])
def search_jobs(
    search_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[JobOut]:
    """The postings a given search produced, best match first."""
    search_row = session.get(JobSearch, search_id)
    if search_row is None or search_row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Search not found")
    jobs = session.scalars(
        select(Job)
        .where(Job.search_id == search_id, Job.is_dismissed.is_(False))
        .order_by(Job.relevance_score.desc())
    ).all()
    return [job_out(session, user, job) for job in jobs]


@router.get("/history", response_model=list[SavedSearchOut])
def history(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[SavedSearchOut]:
    rows = session.scalars(
        select(JobSearch)
        .where(JobSearch.user_id == user.id)
        .order_by(JobSearch.created_at.desc())
        .limit(limit)
    ).all()
    return [SavedSearchOut.model_validate(row) for row in rows]


@router.get("/runs/{run_id}", response_model=SearchRunOut)
def get_run(
    run_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> SearchRunOut:
    run = session.get(SearchRun, run_id)
    if run is None or run.search.user_id != user.id:
        raise HTTPException(status_code=404, detail="Search run not found")
    return SearchRunOut.model_validate(run)


@router.delete("/{search_id}", response_model=Message)
def delete_search(
    search_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Message:
    """Forget a saved search. The jobs it found stay in the library."""
    search_row = session.get(JobSearch, search_id)
    if search_row is None or search_row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Search not found")
    session.delete(search_row)
    session.commit()
    return Message(detail="Search deleted. The jobs it found are still in your library.")


@router.get("/sources", response_model=list[ProviderStatusOut])
def sources() -> list[ProviderStatusOut]:
    """Every job source, and whether it can run right now."""
    return [ProviderStatusOut(**s.to_dict()) for s in provider_statuses()]
