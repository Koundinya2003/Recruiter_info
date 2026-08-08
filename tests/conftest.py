"""Shared test fixtures.

The suite is fully offline: no test touches a real website. External sites are
stood in for by the local mock server in ``tests/fixtures/mock_server.py``, and
the AI and verification providers have in-process fakes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")

from app.config import settings  # noqa: E402

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg2://roi:roi@localhost:5432/roi_test"
)
settings.database_url = TEST_DATABASE_URL
settings.api_key = ""
settings.openrouter_api_key = ""
# Politeness delays would make the suite crawl; the limiter itself is unit-tested.
settings.crawler_domain_delay_seconds = 0.0
# Stays False so SSRF tests exercise the real production policy. Tests that need
# the local mock server opt in per-client with allow_private=True.
settings.crawler_allow_private_networks = False

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import get_engine, get_session_factory, reset_engine  # noqa: E402
from app.models import (  # noqa: E402,F401  (registers metadata)
    Company,
    Job,
    Recruiter,
    User,
)
from app.models.enums import CompanyPriority, EmailConfidence, SourceType  # noqa: E402
from app.security.rate_limit import get_domain_rate_limiter  # noqa: E402
from app.services.scoring.context import seed_default_taxonomy  # noqa: E402
from app.services.scoring.weights import ensure_scoring_config  # noqa: E402
from tests.fixtures.mock_server import MockSite  # noqa: E402


def _database_available() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


DB_AVAILABLE = _database_available()
requires_db = pytest.mark.skipif(
    not DB_AVAILABLE, reason=f"PostgreSQL not reachable at {TEST_DATABASE_URL}"
)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Create the schema once for the whole session."""
    if not DB_AVAILABLE:
        yield
        return
    Base.metadata.drop_all(bind=get_engine())
    Base.metadata.create_all(bind=get_engine())
    yield
    Base.metadata.drop_all(bind=get_engine())
    reset_engine()


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    limiter = get_domain_rate_limiter()
    limiter.delay_seconds = 0.0
    limiter.reset()
    yield


@pytest.fixture
def session() -> Iterator[Session]:
    """A clean database for each test."""
    if not DB_AVAILABLE:
        pytest.skip("PostgreSQL not available")
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE users, companies, jobs, recruiters, contacts, "
                "job_recruiter_relationships, hiring_signals, outreach_leads, "
                "outreach_events, email_verifications, user_profile, crawl_runs, "
                "source_records, scoring_configs, taxonomy_terms RESTART IDENTITY CASCADE"
            )
        )
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def user(session: Session) -> User:
    from app.models.user import UserProfile

    record = User(email="tester@example.com", display_name="Tester")
    session.add(record)
    session.flush()
    session.add(
        UserProfile(
            user_id=record.id,
            full_name="Test Candidate",
            headline="product analyst with 2 years in analytics",
            years_experience=2.0,
            experience="Owned the retention dashboard; ran experiments on the checkout funnel.",
            skills=["SQL", "Product Analytics", "A/B Testing"],
            target_roles=["Product Analyst", "Associate Product Manager"],
            target_industries=["Fintech", "SaaS"],
            preferred_locations=["Remote", "Bangalore"],
        )
    )
    ensure_scoring_config(session, record.id)
    seed_default_taxonomy(session, record.id)
    session.flush()
    return record


@pytest.fixture
def company(session: Session, user: User) -> Company:
    record = Company(
        user_id=user.id,
        company_name="Testly Payments",
        normalized_name="testly",
        company_domain="testly.example",
        career_page_url="https://testly.example/careers",
        industry="Fintech",
        priority=CompanyPriority.HIGH,
        active=True,
    )
    session.add(record)
    session.flush()
    return record


@pytest.fixture
def recruiter(session: Session, company: Company) -> Recruiter:
    record = Recruiter(
        company_id=company.id,
        name="Casey Talent",
        normalized_name="casey talent",
        company_name=company.company_name,
        title="Talent Acquisition Partner, Product",
        public_professional_email="casey.talent@testly.example",
        email_source_url="https://testly.example/careers",
        email_source_type=SourceType.COMPANY_TEAM_PAGE,
        email_confidence=EmailConfidence.HIGH,
        email_confidence_score=100.0,
        role_relevance=90.0,
        relevance_score=88.0,
    )
    session.add(record)
    session.flush()
    return record


@pytest.fixture(scope="session")
def mock_site() -> Iterator[MockSite]:
    """A local stand-in for a real career site / ATS API."""
    site = MockSite().start()
    try:
        yield site
    finally:
        site.stop()


@pytest.fixture
def api_client():
    """FastAPI TestClient against the real application."""
    if not DB_AVAILABLE:
        pytest.skip("PostgreSQL not available")
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
