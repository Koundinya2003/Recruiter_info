"""Health, profile and anything else that is neither a job nor an application."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_session
from app.config import settings
from app.models.application import Application
from app.models.job import Job
from app.models.user import User, UserProfile
from app.providers.registry import provider_statuses
from app.schemas.settings import HealthOut, ProfileOut, ProfileUpdate

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(session: Session = Depends(db_session)) -> HealthOut:
    database = "up"
    jobs = applications = 0
    try:
        session.execute(text("SELECT 1"))
        jobs = int(session.scalar(select(func.count(Job.id))) or 0)
        applications = int(session.scalar(select(func.count(Application.id))) or 0)
    except Exception:  # noqa: BLE001 - health must report, not raise
        database = "down"

    statuses = provider_statuses()
    return HealthOut(
        status="ok" if database == "up" else "degraded",
        app_env=settings.app_env,
        database=database,
        ai_configured=settings.ai_configured,
        auth_enabled=settings.auth_enabled,
        sources_usable=sum(1 for s in statuses if s.usable),
        sources_total=len(statuses),
        jobs=jobs,
        applications=applications,
    )


def _profile(session: Session, user: User) -> UserProfile:
    if user.profile is None:
        profile = UserProfile(user_id=user.id)
        session.add(profile)
        session.flush()
        return profile
    return user.profile


@router.get("/profile", response_model=ProfileOut)
def get_profile(
    session: Session = Depends(db_session), user: User = Depends(current_user)
) -> ProfileOut:
    return ProfileOut.model_validate(_profile(session, user))


@router.put("/profile", response_model=ProfileOut)
def update_profile(
    request: ProfileUpdate,
    session: Session = Depends(db_session),
    user: User = Depends(current_user),
) -> ProfileOut:
    profile = _profile(session, user)
    profile.full_name = request.full_name
    profile.headline = request.headline
    profile.years_experience = request.years_experience
    profile.default_titles = [t.strip() for t in request.default_titles if t.strip()][:10]
    profile.default_locations = [t.strip() for t in request.default_locations if t.strip()][:10]
    profile.skills = [t.strip() for t in request.skills if t.strip()][:30]
    profile.linkedin_url = request.linkedin_url
    session.commit()
    return ProfileOut.model_validate(profile)
