"""The outreach queue: state machine, approval gate and duplicate prevention.

Three invariants are enforced here and nowhere else, so they cannot be
bypassed by a route or a script:

1. **Human approval.** A lead reaches CONTACTED only from APPROVED, and only
   when the draft itself has been approved. Generation never approves.
2. **No duplicate outreach.** ``(recruiter, job)`` is unique, and a lead that
   already has ``contacted_at`` refuses a second contact record.
3. **DO_NOT_CONTACT is absolute.** Marking a recruiter blocks new leads, moves
   existing ones to DO_NOT_CONTACT, and the recommendation engine filters them
   out at the query level.

Every change appends to `outreach_events`, which is never mutated or deleted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.company import Company
from app.models.enums import (
    OutreachEventType,
    OutreachStatus,
    ResponseStatus,
    priority_band,
)
from app.models.job import Job
from app.models.outreach import OutreachEvent, OutreachLead
from app.models.recruiter import Recruiter
from app.services.scoring.outreach_priority import score_opportunity
from app.services.scoring.weights import Weights, load_weights

log = get_logger(__name__)

S = OutreachStatus

ALLOWED_TRANSITIONS: dict[OutreachStatus, set[OutreachStatus]] = {
    S.NEW: {S.REVIEWED, S.APPROVED, S.ARCHIVED, S.DO_NOT_CONTACT},
    S.REVIEWED: {S.APPROVED, S.NEW, S.ARCHIVED, S.DO_NOT_CONTACT},
    S.APPROVED: {S.CONTACTED, S.REVIEWED, S.ARCHIVED, S.DO_NOT_CONTACT},
    S.CONTACTED: {S.REPLIED, S.FOLLOW_UP, S.ARCHIVED, S.DO_NOT_CONTACT},
    S.REPLIED: {S.FOLLOW_UP, S.ARCHIVED, S.DO_NOT_CONTACT},
    S.FOLLOW_UP: {S.CONTACTED, S.REPLIED, S.ARCHIVED, S.DO_NOT_CONTACT},
    S.ARCHIVED: {S.NEW, S.DO_NOT_CONTACT},
    # Terminal: lifted only by clearing the flag on the recruiter itself.
    S.DO_NOT_CONTACT: set(),
}


class OutreachError(Exception):
    """A refused outreach operation, with a message safe to show the user."""


class DuplicateLeadError(OutreachError):
    pass


class DoNotContactError(OutreachError):
    pass


class InvalidTransitionError(OutreachError):
    pass


def _log_event(
    session: Session,
    lead: OutreachLead,
    event_type: OutreachEventType,
    *,
    from_status: OutreachStatus | None = None,
    to_status: OutreachStatus | None = None,
    note: str | None = None,
    payload: dict[str, Any] | None = None,
    actor: str = "user",
) -> OutreachEvent:
    event = OutreachEvent(
        lead_id=lead.id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        note=note,
        payload=payload or {},
        actor=actor,
    )
    session.add(event)
    session.flush()
    return event


def find_lead(
    session: Session, user_id: int, recruiter_id: int, job_id: int | None
) -> OutreachLead | None:
    return session.scalar(
        select(OutreachLead).where(
            OutreachLead.user_id == user_id,
            OutreachLead.recruiter_id == recruiter_id,
            OutreachLead.job_id.is_(None) if job_id is None else OutreachLead.job_id == job_id,
        )
    )


def create_lead(
    session: Session,
    user_id: int,
    recruiter: Recruiter,
    job: Job | None,
    company: Company,
    *,
    weights: Weights | None = None,
    note: str | None = None,
) -> OutreachLead:
    """Add a (recruiter, job) pair to the outreach queue."""
    if recruiter.do_not_contact:
        raise DoNotContactError(
            f"{recruiter.name} is marked DO NOT CONTACT and cannot be added to the queue."
        )

    existing = find_lead(session, user_id, recruiter.id, job.id if job else None)
    if existing is not None:
        raise DuplicateLeadError(
            f"{recruiter.name} is already in the outreach queue for this role "
            f"(status: {existing.status.value})."
        )

    weights = weights or load_weights(session, user_id)
    score = score_opportunity(company, job, recruiter, weights)

    lead = OutreachLead(
        user_id=user_id,
        company_id=company.id,
        recruiter_id=recruiter.id,
        job_id=job.id if job else None,
        status=S.NEW,
        outreach_priority=score.total,
        priority_band=priority_band(score.total),
        priority_breakdown=score.to_dict(),
        priority_computed_at=utcnow(),
    )
    session.add(lead)
    session.flush()

    _log_event(
        session,
        lead,
        OutreachEventType.LEAD_CREATED,
        to_status=S.NEW,
        note=note,
        payload={
            "recruiter": recruiter.name,
            "job": job.title if job else None,
            "priority": score.total,
        },
    )
    log.info("outreach.lead_created", lead_id=lead.id, recruiter_id=recruiter.id)
    return lead


def transition(
    session: Session,
    lead: OutreachLead,
    new_status: OutreachStatus,
    *,
    note: str | None = None,
    actor: str = "user",
) -> OutreachLead:
    """Move a lead to ``new_status``, refusing illegal transitions."""
    current = lead.status
    if new_status == current:
        return lead

    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if new_status not in allowed:
        allowed_names = ", ".join(sorted(s.value for s in allowed)) or "nothing (terminal state)"
        raise InvalidTransitionError(
            f"Cannot move a lead from {current.value} to {new_status.value}. "
            f"Allowed from {current.value}: {allowed_names}."
        )

    if new_status is S.APPROVED and not lead.draft_approved:
        raise OutreachError(
            "Approve the email draft before approving the lead for outreach."
        )
    if new_status is S.CONTACTED:
        raise OutreachError(
            "Use 'record outreach' to mark a lead as contacted, so the contact is logged."
        )

    lead.status = new_status
    session.flush()
    _log_event(
        session,
        lead,
        OutreachEventType.STATUS_CHANGED,
        from_status=current,
        to_status=new_status,
        note=note,
        actor=actor,
    )
    log.info("outreach.transition", lead_id=lead.id, **{"from": current.value, "to": new_status.value})
    return lead


def approve_draft(session: Session, lead: OutreachLead, *, note: str | None = None) -> OutreachLead:
    """Explicit human approval of the drafted email."""
    if not lead.draft_body or not lead.draft_body.strip():
        raise OutreachError("There is no draft to approve. Generate or write one first.")
    if lead.recruiter.do_not_contact:
        raise DoNotContactError(
            f"{lead.recruiter.name} is marked DO NOT CONTACT; this draft cannot be approved."
        )

    lead.draft_approved = True
    lead.draft_approved_at = utcnow()
    session.flush()
    _log_event(
        session,
        lead,
        OutreachEventType.DRAFT_APPROVED,
        note=note,
        payload={"subject": lead.draft_subject},
    )

    if lead.status in {S.NEW, S.REVIEWED}:
        previous = lead.status
        lead.status = S.APPROVED
        session.flush()
        _log_event(
            session,
            lead,
            OutreachEventType.STATUS_CHANGED,
            from_status=previous,
            to_status=S.APPROVED,
            note="Automatically moved to APPROVED when the draft was approved",
        )
    log.info("outreach.draft_approved", lead_id=lead.id)
    return lead


def record_draft_generated(
    session: Session,
    lead: OutreachLead,
    *,
    provider: str,
    model: str,
    fallback: bool,
) -> OutreachEvent:
    """Log that a draft was generated. The draft itself stays unapproved."""
    return _log_event(
        session,
        lead,
        OutreachEventType.DRAFT_GENERATED,
        payload={"provider": provider, "model": model, "fallback": fallback},
        actor="ai",
    )


def edit_draft(
    session: Session,
    lead: OutreachLead,
    *,
    subject: str | None = None,
    body: str | None = None,
) -> OutreachLead:
    """Record a user edit. Any edit revokes approval — you approve what you send."""
    changed = False
    if subject is not None and subject != lead.draft_subject:
        lead.draft_subject = subject
        changed = True
    if body is not None and body != lead.draft_body:
        lead.draft_body = body
        changed = True
    if not changed:
        return lead

    lead.draft_edited_at = utcnow()
    if lead.draft_approved:
        lead.draft_approved = False
        lead.draft_approved_at = None
        if lead.status is S.APPROVED:
            lead.status = S.REVIEWED
    session.flush()
    _log_event(
        session,
        lead,
        OutreachEventType.DRAFT_EDITED,
        note="Draft edited; approval reset so the sent version is the approved version",
    )
    return lead


def record_outreach(
    session: Session,
    lead: OutreachLead,
    *,
    channel: str = "email",
    note: str | None = None,
    allow_repeat: bool = False,
) -> OutreachLead:
    """Record that the user actually sent the outreach.

    This application never sends mail itself; the user sends it from their own
    client and records it here.
    """
    if lead.recruiter.do_not_contact:
        raise DoNotContactError(
            f"{lead.recruiter.name} is marked DO NOT CONTACT; outreach cannot be recorded."
        )
    # Duplicate detection comes first: "you already contacted them" is the
    # accurate reason, and it must not be masked by the approval check.
    if lead.already_contacted and not allow_repeat:
        raise DuplicateLeadError(
            f"{lead.recruiter.name} was already contacted about this role on "
            f"{lead.contacted_at:%d %b %Y}. Use a follow-up instead of contacting again."
        )
    if lead.status is not S.APPROVED and not allow_repeat:
        raise OutreachError(
            f"Lead is {lead.status.value}. Approve the draft before recording outreach."
        )
    if not lead.draft_approved and not allow_repeat:
        raise OutreachError("The draft has not been approved yet.")

    now = utcnow()
    previous = lead.status
    if lead.contacted_at is None:
        lead.contacted_at = now
    lead.last_contact_at = now
    lead.contact_count += 1
    lead.status = S.CONTACTED
    lead.response_status = ResponseStatus.AWAITING
    session.flush()

    _log_event(
        session,
        lead,
        OutreachEventType.CONTACT_RECORDED,
        from_status=previous,
        to_status=S.CONTACTED,
        note=note,
        payload={
            "channel": channel,
            "subject": lead.draft_subject,
            "contact_count": lead.contact_count,
            "to": lead.recruiter.public_professional_email,
        },
    )
    log.info("outreach.recorded", lead_id=lead.id, count=lead.contact_count)
    return lead


def record_follow_up(
    session: Session, lead: OutreachLead, *, note: str | None = None
) -> OutreachLead:
    """Record an additional touch on an already-contacted lead."""
    if not lead.already_contacted:
        raise OutreachError("This lead has not been contacted yet, so there is nothing to follow up.")
    return record_outreach(session, lead, note=note, channel="email-follow-up", allow_repeat=True)


def record_response(
    session: Session,
    lead: OutreachLead,
    response: ResponseStatus,
    *,
    note: str | None = None,
) -> OutreachLead:
    """Record how the recruiter replied."""
    if not lead.already_contacted:
        raise OutreachError("Record the outreach before recording a response to it.")

    previous = lead.status
    lead.response_status = response
    lead.responded_at = utcnow()
    if response in {ResponseStatus.POSITIVE, ResponseStatus.NEGATIVE} and lead.status in {
        S.CONTACTED,
        S.FOLLOW_UP,
    }:
        lead.status = S.REPLIED
    session.flush()

    _log_event(
        session,
        lead,
        OutreachEventType.RESPONSE_RECORDED,
        from_status=previous,
        to_status=lead.status,
        note=note,
        payload={"response": response.value},
    )
    return lead


def add_note(session: Session, lead: OutreachLead, note: str) -> OutreachLead:
    text = (note or "").strip()
    if not text:
        raise OutreachError("Note is empty.")
    stamp = utcnow().strftime("%Y-%m-%d %H:%M")
    lead.notes = f"{lead.notes}\n\n[{stamp}] {text}" if lead.notes else f"[{stamp}] {text}"
    session.flush()
    _log_event(session, lead, OutreachEventType.NOTE_ADDED, note=text)
    return lead


def set_do_not_contact(
    session: Session, recruiter: Recruiter, *, reason: str | None = None
) -> int:
    """Mark a recruiter DO NOT CONTACT and retire every lead for them."""
    recruiter.do_not_contact = True
    recruiter.do_not_contact_reason = reason
    session.flush()

    leads = list(
        session.scalars(
            select(OutreachLead).where(OutreachLead.recruiter_id == recruiter.id)
        ).all()
    )
    for lead in leads:
        previous = lead.status
        if previous is S.DO_NOT_CONTACT:
            continue
        lead.status = S.DO_NOT_CONTACT
        session.flush()
        _log_event(
            session,
            lead,
            OutreachEventType.DO_NOT_CONTACT_SET,
            from_status=previous,
            to_status=S.DO_NOT_CONTACT,
            note=reason or "Recruiter marked DO NOT CONTACT",
        )
    log.info("outreach.do_not_contact", recruiter_id=recruiter.id, leads_retired=len(leads))
    return len(leads)


def clear_do_not_contact(session: Session, recruiter: Recruiter) -> None:
    """Lift a DO NOT CONTACT flag. Leads stay retired; create new ones deliberately."""
    recruiter.do_not_contact = False
    recruiter.do_not_contact_reason = None
    session.flush()
    log.info("outreach.do_not_contact_cleared", recruiter_id=recruiter.id)


def recompute_priority(
    session: Session, lead: OutreachLead, weights: Weights | None = None
) -> OutreachLead:
    weights = weights or load_weights(session, lead.user_id)
    score = score_opportunity(lead.company, lead.job, lead.recruiter, weights)
    lead.outreach_priority = score.total
    lead.priority_band = priority_band(score.total)
    lead.priority_breakdown = score.to_dict()
    lead.priority_computed_at = utcnow()
    session.flush()
    return lead
