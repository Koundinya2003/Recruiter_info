"""Recruiter routes, including verification and DO NOT CONTACT."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user, db_session
from app.models.company import Company
from app.models.enums import EmailConfidence, VerificationStatus
from app.models.recruiter import Contact, Recruiter
from app.models.user import User
from app.models.verification import EmailVerification
from app.schemas.common import Message, ScoreBreakdownOut
from app.schemas.recruiter import (
    ContactOut,
    DoNotContactRequest,
    RecruiterCreate,
    RecruiterDetail,
    RecruiterOut,
    RecruiterUpdate,
    VerificationOut,
)
from app.services.email.service import verify_recruiter_email
from app.services.outreach.service import clear_do_not_contact, set_do_not_contact
from app.utils.text import basic_normalize

router = APIRouter(prefix="/recruiters", tags=["recruiters"])


def _get_recruiter(session: Session, user: User, recruiter_id: int) -> Recruiter:
    recruiter = session.scalar(
        select(Recruiter)
        .join(Company)
        .where(Recruiter.id == recruiter_id, Company.user_id == user.id)
    )
    if recruiter is None:
        raise HTTPException(status_code=404, detail="Recruiter not found")
    return recruiter


@router.get("", response_model=list[RecruiterOut])
def list_recruiters(
    q: str | None = None,
    company_id: int | None = None,
    min_score: float | None = Query(default=None, ge=0, le=100),
    email_confidence: EmailConfidence | None = None,
    verification_status: VerificationStatus | None = None,
    has_email: bool | None = None,
    exclude_do_not_contact: bool = True,
    include_demo: bool = True,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[Recruiter]:
    query = select(Recruiter).join(Company).where(Company.user_id == user.id)

    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Recruiter.name.ilike(pattern),
                Recruiter.title.ilike(pattern),
                Recruiter.company_name.ilike(pattern),
                Recruiter.public_professional_email.ilike(pattern),
            )
        )
    if company_id is not None:
        query = query.where(Recruiter.company_id == company_id)
    if min_score is not None:
        query = query.where(Recruiter.relevance_score >= min_score)
    if email_confidence is not None:
        query = query.where(Recruiter.email_confidence == email_confidence)
    if verification_status is not None:
        query = query.where(Recruiter.email_verification_status == verification_status)
    if has_email is True:
        query = query.where(Recruiter.public_professional_email.is_not(None))
    elif has_email is False:
        query = query.where(Recruiter.public_professional_email.is_(None))
    if exclude_do_not_contact:
        query = query.where(Recruiter.do_not_contact.is_(False))
    if not include_demo:
        query = query.where(Recruiter.is_demo.is_(False))

    query = query.order_by(Recruiter.relevance_score.desc(), Recruiter.name)
    return list(session.scalars(query.limit(limit).offset(offset)).all())


@router.post("", response_model=RecruiterOut, status_code=status.HTTP_201_CREATED)
def create_recruiter(
    payload: RecruiterCreate,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Recruiter:
    """Manually add a contact you found yourself, with its source."""
    company = session.scalar(
        select(Company).where(Company.id == payload.company_id, Company.user_id == user.id)
    )
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    email = str(payload.public_professional_email) if payload.public_professional_email else None
    if email and not payload.email_source_url:
        raise HTTPException(
            status_code=422,
            detail="Provide the source URL where this address is published, so its provenance is recorded.",
        )

    recruiter = Recruiter(
        company_id=company.id,
        name=payload.name.strip(),
        normalized_name=basic_normalize(payload.name),
        company_name=company.company_name,
        title=payload.title,
        professional_profile_url=payload.professional_profile_url,
        public_professional_email=email,
        email_source_url=payload.email_source_url,
        email_source_type=payload.email_source_type if email else None,
        email_confidence=payload.email_confidence if email else EmailConfidence.NONE,
        email_confidence_score=payload.email_confidence.score if email else 0.0,
        notes=payload.notes,
        is_demo=company.is_demo,
    )
    session.add(recruiter)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail=f"{payload.name} is already recorded for this company."
        ) from exc

    if email:
        from app.models.enums import ContactType

        session.add(
            Contact(
                recruiter_id=recruiter.id,
                contact_type=ContactType.EMAIL,
                value=email,
                source_url=payload.email_source_url,
                source_type=payload.email_source_type,
                confidence=payload.email_confidence,
                is_inferred=payload.email_source_type.value == "PATTERN_INFERENCE",
                is_primary=True,
                source_excerpt="Entered manually by the user",
            )
        )
        session.flush()
    return recruiter


@router.get("/{recruiter_id}", response_model=RecruiterDetail)
def get_recruiter(
    recruiter_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> RecruiterDetail:
    recruiter = session.scalar(
        select(Recruiter)
        .join(Company)
        .where(Recruiter.id == recruiter_id, Company.user_id == user.id)
        .options(selectinload(Recruiter.contacts), selectinload(Recruiter.job_links))
    )
    if recruiter is None:
        raise HTTPException(status_code=404, detail="Recruiter not found")

    detail = RecruiterDetail.model_validate(recruiter)
    detail.relevance = ScoreBreakdownOut.from_json(recruiter.relevance_breakdown)
    detail.contacts = [ContactOut.model_validate(c) for c in recruiter.contacts]
    detail.associated_jobs = [
        {
            "job_id": link.job_id,
            "title": link.job.title,
            "job_url": link.job.job_url,
            "relevance_score": link.job.relevance_score,
            "posted_at": link.job.posted_at.isoformat() if link.job.posted_at else None,
            "status": link.job.status.value,
            "relation": link.relation.value,
            "confidence": link.confidence,
            "rationale": link.rationale,
        }
        for link in recruiter.job_links
    ]
    detail.verification_history = [
        {
            "status": v.status.value,
            "confidence": v.confidence,
            "provider": v.provider,
            "reason": v.reason,
            "checked_at": v.checked_at.isoformat(),
        }
        for v in session.scalars(
            select(EmailVerification)
            .where(EmailVerification.recruiter_id == recruiter.id)
            .order_by(EmailVerification.checked_at.desc())
            .limit(10)
        ).all()
    ]
    return detail


@router.patch("/{recruiter_id}", response_model=RecruiterOut)
def update_recruiter(
    recruiter_id: int,
    payload: RecruiterUpdate,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Recruiter:
    recruiter = _get_recruiter(session, user, recruiter_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(recruiter, field, value)
    session.flush()
    return recruiter


@router.post("/{recruiter_id}/verify-email", response_model=VerificationOut)
def verify_email_route(
    recruiter_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> VerificationOut:
    """Verify this recruiter's address through the configured provider."""
    recruiter = _get_recruiter(session, user, recruiter_id)
    if not recruiter.public_professional_email:
        raise HTTPException(status_code=422, detail="This recruiter has no email address to verify.")

    result = verify_recruiter_email(session, recruiter)
    if result is None:  # pragma: no cover - guarded above
        raise HTTPException(status_code=422, detail="Nothing to verify.")
    return VerificationOut(
        email=result.email,
        status=result.status,
        confidence=result.confidence,
        provider=result.provider,
        reason=result.reason,
        checked_at=recruiter.email_verified_at,
    )


@router.post("/{recruiter_id}/do-not-contact", response_model=Message)
def mark_do_not_contact(
    recruiter_id: int,
    payload: DoNotContactRequest,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Message:
    """Permanently exclude this person from recommendations and outreach."""
    recruiter = _get_recruiter(session, user, recruiter_id)
    retired = set_do_not_contact(session, recruiter, reason=payload.reason)
    return Message(
        detail=(
            f"{recruiter.name} is marked DO NOT CONTACT. {retired} lead(s) were retired and "
            "they will no longer appear in recommendations."
        )
    )


@router.delete("/{recruiter_id}/do-not-contact", response_model=Message)
def unmark_do_not_contact(
    recruiter_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Message:
    recruiter = _get_recruiter(session, user, recruiter_id)
    clear_do_not_contact(session, recruiter)
    return Message(
        detail=(
            f"DO NOT CONTACT lifted for {recruiter.name}. Existing leads stay retired; "
            "add a new lead deliberately if you want to reach out."
        )
    )
