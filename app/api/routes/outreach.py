"""Outreach workspace: the contact-today ranking, the queue, drafts, approval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user, db_session
from app.models.company import Company
from app.models.enums import OutreachStatus
from app.models.job import Job
from app.models.outreach import OutreachLead
from app.models.recruiter import Recruiter
from app.models.user import User
from app.schemas.common import Message
from app.schemas.outreach import (
    DraftEdit,
    DraftOut,
    DraftRequest,
    LeadCreate,
    LeadDetail,
    LeadOut,
    NoteRequest,
    OpportunityOut,
    OptionalNoteRequest,
    OutreachEventOut,
    RecordOutreachRequest,
    RecordResponseRequest,
    StatusChangeRequest,
)
from app.services.ai.email_generator import attach_draft_to_lead, generate_draft
from app.services.ai.provider import get_provider
from app.services.outreach.service import (
    ALLOWED_TRANSITIONS,
    OutreachError,
    add_note,
    approve_draft,
    create_lead,
    edit_draft,
    recompute_priority,
    record_draft_generated,
    record_follow_up,
    record_outreach,
    record_response,
    transition,
)
from app.services.scoring.context import build_context
from app.services.scoring.outreach_priority import ContactTodayFilters, contact_today

router = APIRouter(prefix="/outreach", tags=["outreach"])


def _get_lead(session: Session, user: User, lead_id: int) -> OutreachLead:
    lead = session.scalar(
        select(OutreachLead)
        .where(OutreachLead.id == lead_id, OutreachLead.user_id == user.id)
        .options(
            selectinload(OutreachLead.recruiter),
            selectinload(OutreachLead.company),
            selectinload(OutreachLead.job),
            selectinload(OutreachLead.events),
        )
    )
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _detail(lead: OutreachLead) -> LeadDetail:
    from app.schemas.common import ScoreBreakdownOut

    detail = LeadDetail.model_validate(lead)
    detail.company_name = lead.company.company_name if lead.company else None
    detail.recruiter_name = lead.recruiter.name if lead.recruiter else None
    detail.recruiter_email = lead.recruiter.public_professional_email if lead.recruiter else None
    detail.recruiter_email_is_inferred = (
        lead.recruiter.email_is_inferred if lead.recruiter else False
    )
    detail.recruiter_email_verified = lead.recruiter.email_verified if lead.recruiter else False
    detail.recruiter_do_not_contact = lead.recruiter.do_not_contact if lead.recruiter else False
    detail.job_title = lead.job.title if lead.job else None
    detail.job_url = lead.job.job_url if lead.job else None
    detail.priority = ScoreBreakdownOut.from_json(lead.priority_breakdown)
    detail.events = [OutreachEventOut.model_validate(e) for e in lead.events]
    detail.allowed_transitions = sorted(s.value for s in ALLOWED_TRANSITIONS.get(lead.status, set()))
    detail.is_demo = lead.company.is_demo if lead.company else False
    return detail


# --- Who should I contact today? ------------------------------------------------


@router.get("/contact-today", response_model=list[OpportunityOut])
def who_should_i_contact_today(
    limit: int = Query(default=25, ge=1, le=100),
    min_job_relevance: float | None = Query(default=None, ge=0, le=100),
    min_priority: float | None = Query(default=None, ge=0, le=100),
    require_email: bool = False,
    require_verified_email: bool = False,
    include_demo: bool = True,
    company_id: int | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[OpportunityOut]:
    """The ranked shortlist. Recruiters marked DO NOT CONTACT never appear."""
    filters = ContactTodayFilters(
        min_job_relevance=min_job_relevance,
        min_priority=min_priority,
        require_email=require_email,
        require_verified_email=require_verified_email,
        include_demo=include_demo,
        company_ids=[company_id] if company_id else [],
        limit=limit,
    )
    opportunities = contact_today(session, user.id, filters)
    return [OpportunityOut(**o.to_dict()) for o in opportunities]


# --- Queue ----------------------------------------------------------------------


@router.get("/leads", response_model=list[LeadOut])
def list_leads(
    status_filter: OutreachStatus | None = Query(default=None, alias="status"),
    company_id: int | None = None,
    q: str | None = None,
    include_demo: bool = True,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[OutreachLead]:
    query = select(OutreachLead).where(OutreachLead.user_id == user.id)
    if status_filter is not None:
        query = query.where(OutreachLead.status == status_filter)
    if company_id is not None:
        query = query.where(OutreachLead.company_id == company_id)
    if q:
        pattern = f"%{q.strip()}%"
        query = (
            query.join(Recruiter, Recruiter.id == OutreachLead.recruiter_id)
            .join(Company, Company.id == OutreachLead.company_id)
            .where(or_(Recruiter.name.ilike(pattern), Company.company_name.ilike(pattern)))
        )
    if not include_demo:
        query = query.join(Company, Company.id == OutreachLead.company_id, isouter=True).where(
            Company.is_demo.is_(False)
        )
    query = query.order_by(OutreachLead.outreach_priority.desc(), OutreachLead.created_at.desc())
    return list(session.scalars(query.limit(limit).offset(offset)).all())


@router.post("/leads", response_model=LeadDetail, status_code=status.HTTP_201_CREATED)
def add_lead(
    payload: LeadCreate,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    recruiter = session.scalar(
        select(Recruiter)
        .join(Company)
        .where(Recruiter.id == payload.recruiter_id, Company.user_id == user.id)
    )
    if recruiter is None:
        raise HTTPException(status_code=404, detail="Recruiter not found")

    company = session.get(Company, recruiter.company_id)
    if company is None:  # pragma: no cover - FK guarantees this
        raise HTTPException(status_code=404, detail="Company not found")

    job = None
    if payload.job_id is not None:
        job = session.scalar(
            select(Job).where(Job.id == payload.job_id, Job.company_id == company.id)
        )
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found for this company")

    try:
        lead = create_lead(session, user.id, recruiter, job, company, note=payload.note)
    except OutreachError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="This recruiter is already queued for this role."
        ) from exc

    session.refresh(lead)
    return _detail(lead)


@router.get("/leads/{lead_id}", response_model=LeadDetail)
def get_lead(
    lead_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    return _detail(_get_lead(session, user, lead_id))


@router.post("/leads/{lead_id}/status", response_model=LeadDetail)
def change_status(
    lead_id: int,
    payload: StatusChangeRequest,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    lead = _get_lead(session, user, lead_id)
    try:
        transition(session, lead, payload.status, note=payload.note)
    except OutreachError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(lead)


# --- Drafting -------------------------------------------------------------------


@router.post("/leads/{lead_id}/draft", response_model=DraftOut)
def generate_email_draft(
    lead_id: int,
    payload: DraftRequest | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> DraftOut:
    """Generate a draft. It is stored unapproved — approval is a separate step."""
    lead = _get_lead(session, user, lead_id)
    payload = payload or DraftRequest()

    if lead.recruiter.do_not_contact:
        raise HTTPException(
            status_code=409,
            detail=f"{lead.recruiter.name} is marked DO NOT CONTACT; no draft will be generated.",
        )

    profile = user.profile
    matching = build_context(session, user.id)
    draft = generate_draft(
        lead.company,
        lead.job,
        lead.recruiter,
        profile,
        matching,
        provider=get_provider(force_offline=payload.force_offline),
        reason=payload.reason,
    )
    attach_draft_to_lead(session, lead, draft)
    record_draft_generated(
        session,
        lead,
        provider=draft.provider,
        model=draft.model,
        fallback=draft.is_fallback,
    )
    return DraftOut(**draft.to_dict())


@router.patch("/leads/{lead_id}/draft", response_model=LeadDetail)
def edit_email_draft(
    lead_id: int,
    payload: DraftEdit,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    """Edit the draft. Editing always revokes approval."""
    lead = _get_lead(session, user, lead_id)
    edit_draft(session, lead, subject=payload.subject, body=payload.body)
    return _detail(lead)


@router.post("/leads/{lead_id}/approve", response_model=LeadDetail)
def approve(
    lead_id: int,
    payload: OptionalNoteRequest | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    """Explicit human approval — required before outreach can be recorded."""
    lead = _get_lead(session, user, lead_id)
    try:
        approve_draft(session, lead, note=payload.note if payload else None)
    except OutreachError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(lead)


# --- Recording ------------------------------------------------------------------


@router.post("/leads/{lead_id}/record-outreach", response_model=LeadDetail)
def record(
    lead_id: int,
    payload: RecordOutreachRequest | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    """Record that you sent the email from your own mail client."""
    lead = _get_lead(session, user, lead_id)
    payload = payload or RecordOutreachRequest()
    try:
        record_outreach(session, lead, channel=payload.channel, note=payload.note)
    except OutreachError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(lead)


@router.post("/leads/{lead_id}/follow-up", response_model=LeadDetail)
def follow_up(
    lead_id: int,
    payload: OptionalNoteRequest | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    lead = _get_lead(session, user, lead_id)
    try:
        record_follow_up(session, lead, note=payload.note if payload else None)
    except OutreachError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(lead)


@router.post("/leads/{lead_id}/response", response_model=LeadDetail)
def record_reply(
    lead_id: int,
    payload: RecordResponseRequest,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    lead = _get_lead(session, user, lead_id)
    try:
        record_response(session, lead, payload.response, note=payload.note)
    except OutreachError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(lead)


@router.post("/leads/{lead_id}/notes", response_model=LeadDetail)
def add_lead_note(
    lead_id: int,
    payload: NoteRequest,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    lead = _get_lead(session, user, lead_id)
    try:
        add_note(session, lead, payload.note)
    except OutreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _detail(lead)


@router.post("/leads/{lead_id}/recompute", response_model=LeadDetail)
def recompute(
    lead_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> LeadDetail:
    lead = _get_lead(session, user, lead_id)
    recompute_priority(session, lead)
    return _detail(lead)


@router.get("/leads/{lead_id}/history", response_model=list[OutreachEventOut])
def lead_history(
    lead_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[OutreachEventOut]:
    lead = _get_lead(session, user, lead_id)
    return [OutreachEventOut.model_validate(e) for e in lead.events]


@router.delete("/leads/{lead_id}", response_model=Message)
def archive_lead(
    lead_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Message:
    """Archive rather than delete — the outreach history is worth keeping."""
    lead = _get_lead(session, user, lead_id)
    try:
        transition(session, lead, OutreachStatus.ARCHIVED, note="Archived by user")
    except OutreachError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Message(detail="Lead archived. Its history is preserved.")
