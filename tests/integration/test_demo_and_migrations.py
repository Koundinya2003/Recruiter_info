"""Demo-mode safety and Alembic/model parity."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.seed import DEMO_MARKER, DEMO_TLD, clear_demo_data, seed_demo_data
from app.models.company import Company
from app.models.enums import EmailConfidence
from app.models.job import Job
from app.models.recruiter import Contact, Recruiter
from app.models.user import User
from tests.conftest import requires_db

pytestmark = requires_db


def test_demo_seed_creates_a_working_dataset(session: Session, user: User) -> None:
    counts = seed_demo_data(session, user, reset=True)
    assert counts["companies"] >= 3
    assert counts["jobs"] >= 5
    assert counts["recruiters"] >= 3

    companies = list(session.scalars(select(Company).where(Company.user_id == user.id)).all())
    assert companies
    # Scored on seed, so the demo dashboard is immediately meaningful.
    assert any(c.hiring_activity_score > 0 for c in companies)
    jobs = list(session.scalars(select(Job)).all())
    assert any(j.relevance_score >= 80 for j in jobs)


def test_every_demo_record_is_marked_and_unreachable(session: Session, user: User) -> None:
    """The safety property: demo data can never reach a real person."""
    seed_demo_data(session, user, reset=True)

    for company in session.scalars(select(Company).where(Company.user_id == user.id)).all():
        assert company.is_demo is True
        assert DEMO_MARKER in company.company_name

    for job in session.scalars(select(Job)).all():
        assert job.is_demo is True
        assert DEMO_MARKER in job.title

    for recruiter in session.scalars(select(Recruiter)).all():
        assert recruiter.is_demo is True
        assert DEMO_MARKER in recruiter.name
        if recruiter.public_professional_email:
            # RFC 2606 reserved TLD: cannot resolve, cannot deliver.
            assert recruiter.public_professional_email.endswith(DEMO_TLD)

    for contact in session.scalars(select(Contact)).all():
        assert contact.value.endswith(DEMO_TLD) or contact.value.startswith("http")


def test_demo_inferred_addresses_are_never_verified(session: Session, user: User) -> None:
    seed_demo_data(session, user, reset=True)
    inferred = list(
        session.scalars(
            select(Recruiter).where(Recruiter.email_confidence == EmailConfidence.LOW)
        ).all()
    )
    assert inferred, "the demo set should include an inferred address to show the distinction"
    for recruiter in inferred:
        assert recruiter.email_verified is False
        assert recruiter.email_source_url is None


def test_seeding_twice_is_idempotent(session: Session, user: User) -> None:
    first = seed_demo_data(session, user, reset=True)
    second = seed_demo_data(session, user)
    assert second["companies"] == 0
    total = len(list(session.scalars(select(Company).where(Company.user_id == user.id)).all()))
    assert total == first["companies"]


def test_clearing_demo_data_leaves_real_data_alone(session: Session, user: User) -> None:
    real = Company(
        user_id=user.id,
        company_name="Genuine Company",
        normalized_name="genuine",
        active=True,
        is_demo=False,
    )
    session.add(real)
    session.flush()

    seed_demo_data(session, user, reset=True)
    removed = clear_demo_data(session, user)
    assert removed >= 3

    remaining = list(session.scalars(select(Company).where(Company.user_id == user.id)).all())
    assert [c.company_name for c in remaining] == ["Genuine Company"]


def test_seed_refuses_an_address_outside_the_reserved_tld() -> None:
    from app.db.seed import _assert_safe

    _assert_safe(None)
    _assert_safe("someone@demo-payments.example")
    with pytest.raises(ValueError, match="Refusing to seed"):
        _assert_safe("real.person@gmail.com")
    with pytest.raises(ValueError):
        _assert_safe("recruiter@realcompany.com")


def test_demo_profile_never_overwrites_a_real_one(session: Session, user: User) -> None:
    profile = user.profile
    assert profile is not None
    profile.full_name = "My Real Name"
    profile.experience = "My real experience"
    session.flush()

    seed_demo_data(session, user, reset=True, seed_profile=True)
    session.refresh(profile)
    assert profile.full_name == "My Real Name"
    assert profile.experience == "My real experience"


def test_models_and_migrations_agree() -> None:
    """`alembic upgrade head` must produce exactly the models' schema."""
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(  # noqa: S603
        [str(root / ".venv/bin/alembic"), "check"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = result.stdout + result.stderr
    if "Target database is not up to date" in combined:
        pytest.skip("development database is behind head; run `alembic upgrade head`")
    assert result.returncode == 0, combined
    assert "No new upgrade operations detected" in combined
