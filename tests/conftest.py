"""Shared test fixtures.

The suite is fully offline: no test touches a real website. External sources
are stood in for by the local mock server in ``tests/fixtures/mock_server.py``,
and the AI and verification providers have in-process fakes.
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
    Application,
    Company,
    Contact,
    Job,
    JobSearch,
    User,
    UserProfile,
)
from app.models.enums import ContactRole, EmailStatus, SourceType, ValidationStatus  # noqa: E402
from app.security.rate_limit import get_domain_rate_limiter  # noqa: E402
from tests.fixtures.mock_server import MockSite  # noqa: E402

TABLES = (
    "application_events",
    "applications",
    "job_contacts",
    "contacts",
    "jobs",
    "search_runs",
    "job_searches",
    "companies",
    "user_profile",
    "users",
)


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
    with get_engine().begin() as connection:
        connection.execute(
            text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
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
    record = User(email="tester@example.com", display_name="Tester")
    session.add(record)
    session.flush()
    session.add(
        UserProfile(
            user_id=record.id,
            full_name="Test Candidate",
            headline="Product analyst with 2 years in analytics",
            years_experience=2.0,
            default_titles=["Associate Product Manager"],
            default_locations=["Bangalore"],
            skills=["SQL", "Product Analytics"],
        )
    )
    session.flush()
    return record


@pytest.fixture
def company(session: Session) -> Company:
    record = Company(
        name="Demo Fintech",
        normalized_name="demo fintech",
        domain="demo-fintech.example",
        careers_url="https://demo-fintech.example/careers",
    )
    session.add(record)
    session.flush()
    return record


@pytest.fixture
def job(session: Session, user: User, company: Company) -> Job:
    record = Job(
        user_id=user.id,
        company_id=company.id,
        title="Associate Product Manager",
        normalized_title="associate product manager",
        company_name=company.name,
        location="Bangalore, India",
        job_url="https://demo-fintech.example/jobs/1",
        canonical_url="https://demo-fintech.example/jobs/1",
        source=SourceType.GREENHOUSE,
        fingerprint="fingerprint-1",
        validation_status=ValidationStatus.VALID,
        validation_reason="Page still publishes a live JobPosting record.",
        relevance_score=92.0,
        min_years=0.0,
        max_years=2.0,
        experience_text="0-2 years",
    )
    session.add(record)
    session.flush()
    return record


@pytest.fixture
def contact(session: Session, company: Company) -> Contact:
    record = Contact(
        company_id=company.id,
        name="Casey Talent",
        dedupe_key="email:casey.talent@demo-fintech.example",
        title="Talent Acquisition Partner, Product",
        company_name=company.name,
        role=ContactRole.TALENT_ACQUISITION,
        email="casey.talent@demo-fintech.example",
        email_status=EmailStatus.PUBLISHED_ATTRIBUTED,
        source=SourceType.COMPANY_TEAM_PAGE,
        source_url="https://demo-fintech.example/careers",
        confidence=0.9,
    )
    session.add(record)
    session.flush()
    return record


@pytest.fixture(scope="session")
def mock_site() -> Iterator[MockSite]:
    """A local stand-in for a real job source / career site."""
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

    with get_engine().begin() as connection:
        connection.execute(
            text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        )
    with TestClient(create_app()) as client:
        yield client
