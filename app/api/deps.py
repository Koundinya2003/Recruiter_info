"""FastAPI dependencies: database session, auth, and the owning user."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.user import User, UserProfile
from app.services.scoring.context import seed_default_taxonomy
from app.services.scoring.weights import ensure_scoring_config


def db_session() -> Iterator[Session]:
    yield from get_db()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Enforce ``X-API-Key`` when ``API_KEY`` is configured.

    With no key configured the application is an unauthenticated local tool,
    which is the intended single-user default. Setting API_KEY switches every
    /api route to requiring it.
    """
    if not settings.auth_enabled:
        return
    expected = settings.api_key
    provided = x_api_key or ""
    # Constant-time comparison; length differences alone must not leak.
    import hmac

    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def get_or_create_user(session: Session) -> User:
    """The single local owner. Created (with defaults) on first use."""
    email = settings.owner_email.strip().lower() or "owner@localhost"
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = session.scalar(select(User).order_by(User.id).limit(1))
    if user is None:
        user = User(email=email, display_name="Owner", is_active=True)
        session.add(user)
        session.flush()
        session.add(UserProfile(user_id=user.id))
        ensure_scoring_config(session, user.id)
        seed_default_taxonomy(session, user.id)
        session.flush()
    return user


def current_user(
    session: Session = Depends(db_session), _: None = Depends(require_api_key)
) -> User:
    return get_or_create_user(session)
