"""Persisting discovered recruiters and their contact details.

Provenance is preserved on write: every contact detail becomes a `contacts` row
carrying its source URL and source type, and the denormalised fields on the
recruiter always point at the *best* contact found so far. An inferred address
never overwrites a published one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.types import NormalizedRecruiter
from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.company import Company
from app.models.enums import ContactType, EmailConfidence, SourceType
from app.models.recruiter import Contact, Recruiter
from app.utils.text import similarity

log = get_logger(__name__)

NAME_SIMILARITY_THRESHOLD = 0.94

_CONFIDENCE_RANK = {
    EmailConfidence.NONE: 0,
    EmailConfidence.LOW: 1,
    EmailConfidence.MEDIUM: 2,
    EmailConfidence.HIGH: 3,
}


@dataclass
class RecruiterIngestResult:
    added: int = 0
    updated: int = 0
    contacts_added: int = 0
    emails_found: int = 0
    recruiter_ids: list[int] = field(default_factory=list)


def find_existing_recruiter(
    session: Session, company_id: int, candidate: NormalizedRecruiter, cache: list[Recruiter] | None = None
) -> Recruiter | None:
    existing = session.scalar(
        select(Recruiter).where(
            Recruiter.company_id == company_id,
            Recruiter.normalized_name == candidate.normalized_name,
        )
    )
    if existing is not None:
        return existing

    if candidate.email:
        existing = session.scalar(
            select(Recruiter).where(
                Recruiter.company_id == company_id,
                Recruiter.public_professional_email == candidate.email,
            )
        )
        if existing is not None:
            return existing

    pool = cache if cache is not None else list(
        session.scalars(select(Recruiter).where(Recruiter.company_id == company_id)).all()
    )
    for recruiter in pool:
        if similarity(recruiter.normalized_name, candidate.normalized_name) >= NAME_SIMILARITY_THRESHOLD:
            return recruiter
    return None


def _upsert_contact(
    session: Session,
    recruiter: Recruiter,
    *,
    contact_type: ContactType,
    value: str,
    source_type: SourceType,
    source_url: str | None,
    confidence: EmailConfidence,
    is_inferred: bool,
    excerpt: str | None,
) -> bool:
    """Returns True when a new contact row was created."""
    existing = session.scalar(
        select(Contact).where(
            Contact.recruiter_id == recruiter.id,
            Contact.contact_type == contact_type,
            Contact.value == value,
        )
    )
    if existing is not None:
        existing.last_seen = utcnow()
        if _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[existing.confidence]:
            existing.confidence = confidence
            existing.source_type = source_type
            existing.source_url = source_url
            existing.is_inferred = is_inferred
        return False

    session.add(
        Contact(
            recruiter_id=recruiter.id,
            contact_type=contact_type,
            value=value,
            source_type=source_type,
            source_url=source_url,
            source_excerpt=excerpt,
            confidence=confidence,
            is_inferred=is_inferred,
            is_primary=contact_type is ContactType.EMAIL
            and recruiter.public_professional_email == value,
        )
    )
    return True


def _promote_email(
    recruiter: Recruiter,
    candidate: NormalizedRecruiter,
) -> bool:
    """Adopt the candidate's email if it is better than what we already have."""
    if not candidate.email:
        return False
    current_rank = _CONFIDENCE_RANK[recruiter.email_confidence]
    new_rank = _CONFIDENCE_RANK[candidate.confidence]
    if recruiter.public_professional_email and new_rank <= current_rank:
        return False

    recruiter.public_professional_email = candidate.email
    recruiter.email_source_url = candidate.source_url
    recruiter.email_source_type = candidate.source
    recruiter.email_confidence = candidate.confidence
    recruiter.email_confidence_score = candidate.confidence.score
    # A newly adopted address is unverified by definition, and an inferred
    # address can never be marked verified.
    recruiter.email_verified = False
    return True


def ingest_recruiters(
    session: Session,
    company: Company,
    candidates: list[NormalizedRecruiter],
) -> RecruiterIngestResult:
    result = RecruiterIngestResult()
    now = utcnow()
    existing_recruiters = list(
        session.scalars(select(Recruiter).where(Recruiter.company_id == company.id)).all()
    )

    for candidate in candidates:
        recruiter = find_existing_recruiter(
            session, company.id, candidate, cache=existing_recruiters
        )
        if recruiter is None:
            recruiter = Recruiter(
                company_id=company.id,
                name=candidate.name,
                normalized_name=candidate.normalized_name,
                company_name=candidate.company_name or company.company_name,
                title=candidate.title,
                professional_profile_url=candidate.profile_url,
                email_confidence=EmailConfidence.NONE,
                discovered_at=now,
                last_seen=now,
                is_demo=company.is_demo,
            )
            session.add(recruiter)
            session.flush()
            existing_recruiters.append(recruiter)
            result.added += 1
        else:
            result.updated += 1
            if candidate.title and not recruiter.title:
                recruiter.title = candidate.title
            if candidate.profile_url and not recruiter.professional_profile_url:
                recruiter.professional_profile_url = candidate.profile_url
        recruiter.last_seen = now

        if _promote_email(recruiter, candidate):
            result.emails_found += 1

        if candidate.email:
            created = _upsert_contact(
                session,
                recruiter,
                contact_type=ContactType.EMAIL,
                value=candidate.email,
                source_type=candidate.source,
                source_url=candidate.source_url,
                confidence=candidate.confidence,
                is_inferred=candidate.is_inferred,
                excerpt=candidate.source_excerpt,
            )
            result.contacts_added += int(created)

        if candidate.profile_url:
            created = _upsert_contact(
                session,
                recruiter,
                contact_type=ContactType.PROFILE_URL,
                value=candidate.profile_url,
                source_type=candidate.source,
                source_url=candidate.source_url,
                confidence=EmailConfidence.NONE,
                is_inferred=False,
                excerpt=candidate.source_excerpt,
            )
            result.contacts_added += int(created)

        result.recruiter_ids.append(recruiter.id)

    session.flush()
    log.info(
        "recruiters.ingested",
        company=company.company_name,
        added=result.added,
        updated=result.updated,
        emails=result.emails_found,
    )
    return result
