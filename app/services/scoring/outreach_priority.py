"""Outreach priority and the "Who should I contact today?" engine.

    Job relevance        30
    Hiring freshness     20
    Recruiter relevance  20
    Company priority     15
    Email confidence     10
    Recency               5

The engine pairs each sufficiently-relevant open job with the recruiters at
that company, scores every pair, and returns the ranked shortlist. Recruiters
marked DO_NOT_CONTACT are filtered out at the query level, not merely ranked
low, and pairs that already have a contacted lead are excluded so the list
never suggests contacting someone twice about the same role.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.base import utcnow
from app.models.company import Company
from app.models.enums import (
    EmailConfidence,
    JobStatus,
    OutreachStatus,
    VerificationStatus,
    priority_band,
)
from app.models.job import Job
from app.models.outreach import OutreachLead
from app.models.recruiter import Recruiter
from app.services.scoring.base import ScoreResult, clamp, decay, humanize_age
from app.services.scoring.context import ProfileContext, build_context
from app.services.scoring.weights import Weights, default_weights, load_weights


@dataclass
class Opportunity:
    """One (job, recruiter) pair with its priority score and explanation."""

    company: Company
    job: Job | None
    recruiter: Recruiter
    score: ScoreResult
    existing_lead: OutreachLead | None = None
    link_rationale: str | None = None

    @property
    def priority(self) -> float:
        return self.score.total

    @property
    def band(self) -> str:
        return priority_band(self.priority)

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company.id,
            "company_name": self.company.company_name,
            "industry": self.company.industry,
            "job_id": self.job.id if self.job else None,
            "job_title": self.job.title if self.job else None,
            "job_url": self.job.job_url if self.job else None,
            "job_relevance": self.job.relevance_score if self.job else None,
            "posted_at": self.job.posted_at.isoformat()
            if self.job and self.job.posted_at
            else None,
            "job_age_hours": self.job.age_hours if self.job else None,
            "recruiter_id": self.recruiter.id,
            "recruiter_name": self.recruiter.name,
            "recruiter_title": self.recruiter.title,
            "recruiter_score": self.recruiter.relevance_score,
            "email": self.recruiter.public_professional_email,
            "email_confidence": self.recruiter.email_confidence.value,
            "email_verified": self.recruiter.email_verified,
            "email_status": self.recruiter.email_verification_status.value,
            "email_is_inferred": self.recruiter.email_is_inferred,
            "outreach_priority": self.priority,
            "band": self.band,
            "reasons": self.score.reasons,
            "gaps": self.score.gaps,
            "breakdown": self.score.to_dict(),
            "existing_lead_id": self.existing_lead.id if self.existing_lead else None,
            "existing_lead_status": self.existing_lead.status.value
            if self.existing_lead
            else None,
            "is_demo": self.company.is_demo,
        }


def score_opportunity(
    company: Company,
    job: Job | None,
    recruiter: Recruiter,
    weights: Weights | None = None,
) -> ScoreResult:
    weights = weights or default_weights()
    w = weights.outreach
    result = ScoreResult()

    if recruiter.do_not_contact:
        result.excluded = True
        result.exclusion_reason = "Recruiter is marked DO NOT CONTACT"

    # --- Job relevance (30) --------------------------------------------------
    if job is not None:
        factor = clamp(job.relevance_score / 100.0)
        result.add(
            "job_relevance",
            "Job relevance",
            w["job_relevance"] * factor,
            w["job_relevance"],
            [f"{job.title} scores {job.relevance_score:.0f}/100 against your profile"],
            matched=factor > 0,
        )
    else:
        result.add(
            "job_relevance",
            "Job relevance",
            0,
            w["job_relevance"],
            ["No specific role attached to this contact"],
        )

    # --- Hiring freshness (20) -----------------------------------------------
    age = job.age_hours if job else None
    factor = decay(
        age,
        float(weights.option("fresh_job_window_hours")),
        float(weights.option("freshness_zero_hours")),
    )
    if age is None:
        result.add(
            "hiring_freshness",
            "Hiring freshness",
            0,
            w["hiring_freshness"],
            ["No posting date to judge freshness"],
        )
    else:
        result.add(
            "hiring_freshness",
            "Hiring freshness",
            w["hiring_freshness"] * factor,
            w["hiring_freshness"],
            [f"Role appeared {humanize_age(age)}"],
            matched=factor > 0,
        )

    # --- Recruiter relevance (20) --------------------------------------------
    factor = clamp(recruiter.relevance_score / 100.0)
    result.add(
        "recruiter_relevance",
        "Recruiter relevance",
        w["recruiter_relevance"] * factor,
        w["recruiter_relevance"],
        [
            f"{recruiter.name} ({recruiter.title or 'role unknown'}) scores "
            f"{recruiter.relevance_score:.0f}/100"
        ],
        matched=factor > 0,
    )

    # --- Company priority (15) -----------------------------------------------
    result.add(
        "company_priority",
        "Company priority",
        w["company_priority"] * company.priority.weight,
        w["company_priority"],
        [f"{company.company_name} is a {company.priority.value} priority company for you"],
        matched=True,
    )

    # --- Email confidence (10) -----------------------------------------------
    if not recruiter.public_professional_email:
        result.add(
            "email_confidence",
            "Email confidence",
            0,
            w["email_confidence"],
            ["No contact address available — you cannot email this person yet"],
        )
    else:
        factor = recruiter.email_confidence.score / 100.0
        reasons: list[str] = []
        if recruiter.email_confidence is EmailConfidence.HIGH:
            reasons.append("Public professional email, attributed to this person")
        elif recruiter.email_confidence is EmailConfidence.MEDIUM:
            reasons.append("Public company address, not attributed to this person")
        else:
            reasons.append("INFERRED address — not published anywhere, verify before use")
        if recruiter.email_verified and recruiter.email_verification_status is VerificationStatus.VALID:
            factor = min(1.0, factor + 0.2)
            reasons.append("Email verified")
        elif recruiter.email_verification_status is VerificationStatus.INVALID:
            factor *= 0.1
            reasons.append("Email verification failed")
        result.add(
            "email_confidence",
            "Email confidence",
            w["email_confidence"] * factor,
            w["email_confidence"],
            reasons,
        )

    # --- Recency (5) ---------------------------------------------------------
    age_days = (utcnow() - recruiter.last_seen).total_seconds() / 86400.0
    factor = decay(
        age_days,
        float(weights.option("recruiter_recency_full_days")),
        float(weights.option("recruiter_recency_zero_days")),
    )
    result.add(
        "recency",
        "Recency",
        w["recency"] * factor,
        w["recency"],
        [f"Recruiter details last confirmed {humanize_age(age_days * 24)}"],
        matched=factor > 0,
    )

    return result


@dataclass
class ContactTodayFilters:
    min_job_relevance: float | None = None
    min_priority: float | None = None
    company_ids: list[int] = field(default_factory=list)
    require_email: bool = False
    require_verified_email: bool = False
    include_demo: bool = True
    exclude_existing_leads: bool = False
    limit: int = 25


def contact_today(
    session: Session,
    user_id: int,
    filters: ContactTodayFilters | None = None,
    *,
    weights: Weights | None = None,
    context: ProfileContext | None = None,
) -> list[Opportunity]:
    """Rank today's (job, recruiter) opportunities for ``user_id``."""
    filters = filters or ContactTodayFilters()
    weights = weights or load_weights(session, user_id)
    context = context or build_context(session, user_id)

    min_relevance = (
        filters.min_job_relevance
        if filters.min_job_relevance is not None
        else float(weights.option("contact_today_min_job_relevance"))
    )
    min_priority = (
        filters.min_priority
        if filters.min_priority is not None
        else float(weights.option("contact_today_min_priority"))
    )

    company_query = select(Company).where(
        Company.user_id == user_id, Company.active.is_(True)
    )
    if filters.company_ids:
        company_query = company_query.where(Company.id.in_(filters.company_ids))
    if not filters.include_demo:
        company_query = company_query.where(Company.is_demo.is_(False))
    companies = {c.id: c for c in session.scalars(company_query).all()}
    if not companies:
        return []

    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.company_id.in_(companies.keys()),
                Job.status == JobStatus.OPEN,
                Job.relevance_score >= min_relevance,
            )
            .options(selectinload(Job.recruiter_links))
        ).all()
    )

    # DO_NOT_CONTACT is enforced in the query itself.
    recruiters_by_company: dict[int, list[Recruiter]] = {}
    for recruiter in session.scalars(
        select(Recruiter).where(
            Recruiter.company_id.in_(companies.keys()),
            Recruiter.do_not_contact.is_(False),
        )
    ).all():
        recruiters_by_company.setdefault(recruiter.company_id, []).append(recruiter)

    leads = {
        (lead.recruiter_id, lead.job_id): lead
        for lead in session.scalars(
            select(OutreachLead).where(OutreachLead.user_id == user_id)
        ).all()
    }

    opportunities: list[Opportunity] = []
    for job in jobs:
        company = companies[job.company_id]
        linked_ids = {link.recruiter_id: link for link in job.recruiter_links}
        candidates = recruiters_by_company.get(job.company_id, [])
        for recruiter in candidates:
            if filters.require_email and not recruiter.public_professional_email:
                continue
            if filters.require_verified_email and not recruiter.email_verified:
                continue

            lead = leads.get((recruiter.id, job.id))
            if lead is not None:
                # Never re-suggest someone already contacted or explicitly retired.
                if lead.status in {
                    OutreachStatus.CONTACTED,
                    OutreachStatus.REPLIED,
                    OutreachStatus.ARCHIVED,
                    OutreachStatus.DO_NOT_CONTACT,
                } or lead.already_contacted:
                    continue
                if filters.exclude_existing_leads:
                    continue

            score = score_opportunity(company, job, recruiter, weights)
            if score.excluded or score.total < min_priority:
                continue

            link = linked_ids.get(recruiter.id)
            opportunities.append(
                Opportunity(
                    company=company,
                    job=job,
                    recruiter=recruiter,
                    score=score,
                    existing_lead=lead,
                    link_rationale=link.rationale if link else None,
                )
            )

    opportunities.sort(key=lambda o: (-o.priority, -(o.job.relevance_score if o.job else 0)))

    # One recruiter should not fill the whole list with five roles at the same
    # company; keep their single best opportunity near the top.
    seen: dict[int, int] = {}
    deduped: list[Opportunity] = []
    for opportunity in opportunities:
        count = seen.get(opportunity.recruiter.id, 0)
        if count >= 2:
            continue
        seen[opportunity.recruiter.id] = count + 1
        deduped.append(opportunity)

    return deduped[: filters.limit]
