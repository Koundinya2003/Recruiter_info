"""Verification service: run a check, persist it, update the recruiter.

The rule the brief is emphatic about is enforced here, in one place:

    An inferred address is never marked verified.

A pattern-inferred address can still be *checked* — the result is informative —
but ``recruiter.email_verified`` stays False, because verifying that a domain
accepts mail says nothing about whether we guessed the right person's mailbox.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.enums import EmailConfidence, VerificationStatus
from app.models.recruiter import Recruiter
from app.models.verification import EmailVerification
from app.services.email.verifier import EmailVerifier, VerificationResult, get_verifier

log = get_logger(__name__)


def verify_email(email: str, verifier: EmailVerifier | None = None) -> VerificationResult:
    """Check a bare address. No database involvement."""
    verifier = verifier or get_verifier()
    return verifier.verify(email)


def verify_recruiter_email(
    session: Session,
    recruiter: Recruiter,
    verifier: EmailVerifier | None = None,
) -> VerificationResult | None:
    """Verify a recruiter's address and persist the outcome.

    Returns None when the recruiter has no address to check.
    """
    if not recruiter.public_professional_email:
        return None

    verifier = verifier or get_verifier()
    result = verifier.verify(recruiter.public_professional_email)

    session.add(
        EmailVerification(
            recruiter_id=recruiter.id,
            email=result.email,
            status=result.status,
            confidence=result.confidence,
            provider=result.provider,
            reason=result.reason,
            raw_response=result.raw,
        )
    )

    recruiter.email_verification_status = result.status
    recruiter.email_verified_at = utcnow()

    if recruiter.email_confidence is EmailConfidence.LOW:
        # Inferred address: record the check, but never call it verified.
        recruiter.email_verified = False
        if result.status is VerificationStatus.INVALID:
            # An inferred address that does not even resolve is worthless.
            recruiter.email_confidence_score = 0.0
    else:
        recruiter.email_verified = result.status is VerificationStatus.VALID
        recruiter.email_confidence_score = round(
            recruiter.email_confidence.score * (0.5 + 0.5 * result.confidence), 2
        )

    session.flush()
    log.info(
        "verify.recorded",
        recruiter_id=recruiter.id,
        status=result.status.value,
        provider=result.provider,
        verified=recruiter.email_verified,
    )
    return result


def verify_company_recruiters(
    session: Session,
    company_id: int,
    *,
    only_unverified: bool = True,
    limit: int = 50,
    verifier: EmailVerifier | None = None,
) -> list[VerificationResult]:
    """Verify every discovered address at a company."""
    verifier = verifier or get_verifier()
    query = select(Recruiter).where(
        Recruiter.company_id == company_id,
        Recruiter.public_professional_email.is_not(None),
    )
    if only_unverified:
        query = query.where(
            Recruiter.email_verification_status == VerificationStatus.NOT_CHECKED
        )
    results: list[VerificationResult] = []
    for recruiter in session.scalars(query.limit(limit)).all():
        result = verify_recruiter_email(session, recruiter, verifier)
        if result is not None:
            results.append(result)
    return results


def verification_history(session: Session, email: str, limit: int = 20) -> list[EmailVerification]:
    return list(
        session.scalars(
            select(EmailVerification)
            .where(EmailVerification.email == email)
            .order_by(EmailVerification.checked_at.desc())
            .limit(limit)
        ).all()
    )
