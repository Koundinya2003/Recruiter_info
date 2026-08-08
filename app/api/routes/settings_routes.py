"""Profile, taxonomy and scoring-weight configuration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.models.config import TaxonomyTerm
from app.models.enums import TaxonomyKind
from app.models.user import User, UserProfile
from app.schemas.common import Message
from app.schemas.settings import (
    ProfileIn,
    ProfileOut,
    ScoringConfigIn,
    ScoringConfigOut,
    TaxonomyTermIn,
    TaxonomyTermOut,
)
from app.services.scoring.context import seed_default_taxonomy
from app.services.scoring.weights import ensure_scoring_config, load_weights

router = APIRouter(prefix="/settings", tags=["settings"])


# --- Profile --------------------------------------------------------------------


@router.get("/profile", response_model=ProfileOut)
def get_profile(
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ProfileOut:
    profile = user.profile
    if profile is None:
        profile = UserProfile(user_id=user.id)
        session.add(profile)
        session.flush()
    out = ProfileOut.model_validate(profile)
    out.user_email = user.email
    return out


@router.put("/profile", response_model=ProfileOut)
def update_profile(
    payload: ProfileIn,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ProfileOut:
    """Store résumé context. It is never hardcoded into application logic."""
    profile = user.profile
    if profile is None:
        profile = UserProfile(user_id=user.id)
        session.add(profile)
        session.flush()
    for field, value in payload.model_dump().items():
        setattr(profile, field, value)
    session.flush()
    out = ProfileOut.model_validate(profile)
    out.user_email = user.email
    return out


# --- Taxonomy -------------------------------------------------------------------


@router.get("/taxonomy", response_model=list[TaxonomyTermOut])
def list_taxonomy(
    kind: TaxonomyKind | None = None,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> list[TaxonomyTerm]:
    query = select(TaxonomyTerm).where(TaxonomyTerm.user_id == user.id)
    if kind is not None:
        query = query.where(TaxonomyTerm.kind == kind)
    return list(session.scalars(query.order_by(TaxonomyTerm.kind, TaxonomyTerm.term)).all())


@router.post("/taxonomy", response_model=TaxonomyTermOut, status_code=status.HTTP_201_CREATED)
def add_taxonomy_term(
    payload: TaxonomyTermIn,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> TaxonomyTerm:
    term = TaxonomyTerm(user_id=user.id, **payload.model_dump())
    session.add(term)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409, detail=f"'{payload.term}' already exists in {payload.kind.value}."
        ) from exc
    return term


@router.patch("/taxonomy/{term_id}", response_model=TaxonomyTermOut)
def update_taxonomy_term(
    term_id: int,
    payload: TaxonomyTermIn,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> TaxonomyTerm:
    term = session.scalar(
        select(TaxonomyTerm).where(TaxonomyTerm.id == term_id, TaxonomyTerm.user_id == user.id)
    )
    if term is None:
        raise HTTPException(status_code=404, detail="Taxonomy term not found")
    for field, value in payload.model_dump().items():
        setattr(term, field, value)
    session.flush()
    return term


@router.delete("/taxonomy/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_taxonomy_term(
    term_id: int,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> None:
    term = session.scalar(
        select(TaxonomyTerm).where(TaxonomyTerm.id == term_id, TaxonomyTerm.user_id == user.id)
    )
    if term is None:
        raise HTTPException(status_code=404, detail="Taxonomy term not found")
    session.delete(term)
    session.flush()


@router.post("/taxonomy/reset", response_model=Message)
def reset_taxonomy(
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> Message:
    """Restore the default role/industry/skill taxonomy, keeping custom terms."""
    created = seed_default_taxonomy(session, user.id)
    return Message(detail=f"{created} default term(s) restored.")


# --- Scoring weights ------------------------------------------------------------


@router.get("/scoring", response_model=ScoringConfigOut)
def get_scoring(
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ScoringConfigOut:
    weights = load_weights(session, user.id)
    return ScoringConfigOut(**weights.to_dict())


@router.put("/scoring", response_model=ScoringConfigOut)
def update_scoring(
    payload: ScoringConfigIn,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ScoringConfigOut:
    config = ensure_scoring_config(session, user.id)
    if payload.job_weights:
        config.job_weights = {**(config.job_weights or {}), **payload.job_weights}
    if payload.hiring_weights:
        config.hiring_weights = {**(config.hiring_weights or {}), **payload.hiring_weights}
    if payload.recruiter_weights:
        config.recruiter_weights = {**(config.recruiter_weights or {}), **payload.recruiter_weights}
    if payload.outreach_weights:
        config.outreach_weights = {**(config.outreach_weights or {}), **payload.outreach_weights}
    if payload.options:
        config.options = {**(config.options or {}), **payload.options}
    session.flush()
    return ScoringConfigOut(**load_weights(session, user.id).to_dict())


@router.post("/scoring/reset", response_model=ScoringConfigOut)
def reset_scoring(
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ScoringConfigOut:
    from app.services.scoring.weights import (
        DEFAULT_HIRING_WEIGHTS,
        DEFAULT_JOB_WEIGHTS,
        DEFAULT_OPTIONS,
        DEFAULT_OUTREACH_WEIGHTS,
        DEFAULT_RECRUITER_WEIGHTS,
    )

    config = ensure_scoring_config(session, user.id)
    config.job_weights = dict(DEFAULT_JOB_WEIGHTS)
    config.hiring_weights = dict(DEFAULT_HIRING_WEIGHTS)
    config.recruiter_weights = dict(DEFAULT_RECRUITER_WEIGHTS)
    config.outreach_weights = dict(DEFAULT_OUTREACH_WEIGHTS)
    config.options = dict(DEFAULT_OPTIONS)
    session.flush()
    return ScoringConfigOut(**load_weights(session, user.id).to_dict())
