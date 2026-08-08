"""Persisting discovered jobs, with duplicate detection against the database.

Within-run duplicates are dropped by the collector. This layer handles the
cross-run and cross-source case:

1. Same canonical URL for the company  -> same job.
2. Same content fingerprint (company + normalised title + normalised location)
   -> same job, even when the URL differs because it came from another source.
3. Same source + external id           -> same job.
4. Very similar normalised title with the same location -> treated as the same
   job when similarity is above the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.types import NormalizedJob
from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.company import Company
from app.models.enums import JobStatus
from app.models.job import Job
from app.services.scoring.context import ProfileContext
from app.services.scoring.job_relevance import apply_job_score
from app.services.scoring.weights import Weights
from app.utils.text import similarity

log = get_logger(__name__)

TITLE_SIMILARITY_THRESHOLD = 0.92


@dataclass
class IngestResult:
    added: int = 0
    updated: int = 0
    duplicates: int = 0
    closed: int = 0
    new_job_ids: list[int] = field(default_factory=list)


def find_existing_job(
    session: Session, company_id: int, candidate: NormalizedJob, cache: list[Job] | None = None
) -> Job | None:
    """Locate an already-stored job matching ``candidate``."""
    existing = session.scalar(
        select(Job).where(
            Job.company_id == company_id, Job.canonical_url == candidate.canonical_url
        )
    )
    if existing is not None:
        return existing

    existing = session.scalar(
        select(Job).where(Job.company_id == company_id, Job.content_hash == candidate.content_hash)
    )
    if existing is not None:
        return existing

    if candidate.external_id:
        existing = session.scalar(
            select(Job).where(
                Job.company_id == company_id,
                Job.source == candidate.source,
                Job.source_job_id == candidate.external_id,
            )
        )
        if existing is not None:
            return existing

    # Fuzzy fallback: near-identical title at the same location.
    pool = cache if cache is not None else list(
        session.scalars(select(Job).where(Job.company_id == company_id)).all()
    )
    for job in pool:
        if (job.normalized_location or "") != (candidate.normalized_location or ""):
            continue
        if similarity(job.normalized_title, candidate.normalized_title) >= TITLE_SIMILARITY_THRESHOLD:
            return job
    return None


def ingest_jobs(
    session: Session,
    company: Company,
    candidates: list[NormalizedJob],
    context: ProfileContext,
    weights: Weights | None = None,
    *,
    authoritative: bool = False,
) -> IngestResult:
    """Insert or refresh jobs for ``company`` and score each one.

    :param authoritative: when True the source is known to list *all* current
        openings (an ATS API), so previously seen jobs that are now absent are
        marked CLOSED. Never set this for partial sources like a career page
        scrape, or live roles would be closed by accident.
    """
    result = IngestResult()
    now = utcnow()
    existing_jobs = list(session.scalars(select(Job).where(Job.company_id == company.id)).all())
    seen_ids: set[int] = set()

    for candidate in candidates:
        job = find_existing_job(session, company.id, candidate, cache=existing_jobs)
        if job is None:
            job = Job(
                company_id=company.id,
                title=candidate.title,
                normalized_title=candidate.normalized_title,
                description=candidate.description,
                location=candidate.location,
                normalized_location=candidate.normalized_location,
                employment_type=candidate.employment_type,
                job_url=candidate.url,
                canonical_url=candidate.canonical_url,
                content_hash=candidate.content_hash,
                source=candidate.source,
                source_job_id=candidate.external_id,
                posted_at=candidate.posted_at,
                discovered_at=now,
                last_seen_at=now,
                status=JobStatus.OPEN,
                is_demo=company.is_demo,
            )
            session.add(job)
            session.flush()
            existing_jobs.append(job)
            result.added += 1
            result.new_job_ids.append(job.id)
        else:
            result.duplicates += 1
            changed = False
            if job.status != JobStatus.OPEN:
                job.status = JobStatus.OPEN
                changed = True
            if candidate.description and candidate.description != job.description:
                job.description = candidate.description
                changed = True
            if candidate.location and candidate.location != job.location:
                job.location = candidate.location
                job.normalized_location = candidate.normalized_location
                changed = True
            if candidate.posted_at and job.posted_at is None:
                job.posted_at = candidate.posted_at
                changed = True
            if candidate.employment_type and not job.employment_type:
                job.employment_type = candidate.employment_type
                changed = True
            job.last_seen_at = now
            if changed:
                result.updated += 1

        seen_ids.add(job.id)
        apply_job_score(job, context, weights)

    if authoritative and candidates:
        for job in existing_jobs:
            if job.id in seen_ids or job.status != JobStatus.OPEN:
                continue
            job.status = JobStatus.CLOSED
            result.closed += 1

    session.flush()
    log.info(
        "jobs.ingested",
        company=company.company_name,
        added=result.added,
        updated=result.updated,
        duplicates=result.duplicates,
        closed=result.closed,
    )
    return result


def rescore_company_jobs(
    session: Session, company: Company, context: ProfileContext, weights: Weights | None = None
) -> int:
    """Re-run relevance scoring for every job at a company."""
    jobs = list(session.scalars(select(Job).where(Job.company_id == company.id)).all())
    for job in jobs:
        apply_job_score(job, context, weights)
    session.flush()
    return len(jobs)
