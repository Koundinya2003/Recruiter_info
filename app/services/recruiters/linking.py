"""Linking recruiters to the jobs they plausibly own.

We never claim a relationship we cannot justify: each link stores the reason it
was created and a confidence, and the UI shows both.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import JobStatus, RecruiterRelation
from app.models.job import Job
from app.models.recruiter import JobRecruiterLink, Recruiter
from app.utils.text import contains_term

# Recruiter title keyword -> job-title keywords it plausibly covers.
FUNCTION_COVERAGE: dict[str, tuple[str, ...]] = {
    "product": ("product", "program"),
    "technical": ("engineer", "developer", "data", "analyst", "product", "machine learning"),
    "tech": ("engineer", "developer", "data", "analyst"),
    "engineering": ("engineer", "developer", "architect"),
    "data": ("data", "analyst", "analytics", "scientist"),
    "analytics": ("analyst", "analytics", "data"),
    "business": ("business", "operations", "strategy", "analyst"),
    "growth": ("growth", "marketing", "analyst"),
    "gtm": ("marketing", "sales", "growth"),
}


def link_recruiters_to_jobs(
    session: Session, company_id: int, *, min_job_relevance: float = 0.0
) -> int:
    """Create missing job/recruiter links for a company. Returns links created."""
    jobs = list(
        session.scalars(
            select(Job).where(
                Job.company_id == company_id,
                Job.status == JobStatus.OPEN,
                Job.relevance_score >= min_job_relevance,
            )
        ).all()
    )
    recruiters = list(
        session.scalars(select(Recruiter).where(Recruiter.company_id == company_id)).all()
    )
    if not jobs or not recruiters:
        return 0

    existing = {
        (link.job_id, link.recruiter_id)
        for link in session.scalars(
            select(JobRecruiterLink).where(
                JobRecruiterLink.job_id.in_([j.id for j in jobs])
            )
        ).all()
    }

    created = 0
    for job in jobs:
        for recruiter in recruiters:
            if (job.id, recruiter.id) in existing:
                continue
            relation, confidence, rationale = classify_link(job, recruiter)
            if relation is None:
                continue
            session.add(
                JobRecruiterLink(
                    job_id=job.id,
                    recruiter_id=recruiter.id,
                    relation=relation,
                    confidence=confidence,
                    rationale=rationale,
                )
            )
            existing.add((job.id, recruiter.id))
            created += 1

    session.flush()
    return created


def classify_link(
    job: Job, recruiter: Recruiter
) -> tuple[RecruiterRelation | None, float, str | None]:
    """Decide whether and why a recruiter is connected to a job."""
    title = recruiter.title or ""

    # A contact named directly on the posting is the strongest link we can have.
    payload_contact = (job.relevance_breakdown or {}).get("posting_contact")
    if payload_contact and recruiter.public_professional_email == payload_contact:
        return (
            RecruiterRelation.POSTED_BY,
            0.95,
            "Listed as the contact on this job posting",
        )

    for keyword, job_terms in FUNCTION_COVERAGE.items():
        if contains_term(title, keyword) and any(contains_term(job.title, t) for t in job_terms):
            return (
                RecruiterRelation.FUNCTION_MATCH,
                0.75,
                f"Recruits for {keyword}, which covers '{job.title}'",
            )

    if recruiter.role_relevance >= 50:
        return (
            RecruiterRelation.COMPANY_TALENT_TEAM,
            0.45,
            f"On {recruiter.company_name}'s talent team; no explicit link to this specific role",
        )

    return None, 0.0, None
