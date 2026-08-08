"""Demo mode: realistic-looking but unmistakably synthetic data.

Safety rules, enforced by :func:`_assert_safe`:

* every email uses the RFC 2606 reserved ``.example`` TLD, which cannot resolve
  and cannot deliver mail to a real person;
* every company, person and job is flagged ``is_demo=True``;
* every display name carries a ``[DEMO]`` marker so no screen can present a
  synthetic record as a real one.

There is no path by which demo data can cause an email to reach a real inbox.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.company import Company
from app.models.enums import (
    CompanyPriority,
    ContactType,
    EmailConfidence,
    JobStatus,
    SourceType,
    VerificationStatus,
)
from app.models.job import Job
from app.models.recruiter import Contact, Recruiter
from app.models.user import User, UserProfile
from app.services.recruiters.linking import link_recruiters_to_jobs
from app.services.scan import rescore_company_recruiters
from app.services.scoring.context import build_context, seed_default_taxonomy
from app.services.scoring.hiring_activity import refresh_company_hiring_activity
from app.services.scoring.job_relevance import apply_job_score
from app.services.scoring.weights import ensure_scoring_config, load_weights
from app.utils.text import (
    basic_normalize,
    content_fingerprint,
    normalize_company_name,
    normalize_location,
    normalize_title,
)

log = get_logger(__name__)

DEMO_MARKER = "[DEMO]"
DEMO_TLD = ".example"


@dataclass
class DemoJob:
    title: str
    location: str
    hours_ago: float
    description: str
    employment_type: str = "Full-time"


@dataclass
class DemoRecruiter:
    name: str
    title: str
    email: str | None
    confidence: EmailConfidence
    source_type: SourceType
    verified: bool = False
    inferred: bool = False


@dataclass
class DemoCompany:
    name: str
    domain: str
    industry: str
    priority: CompanyPriority
    jobs: list[DemoJob]
    recruiters: list[DemoRecruiter]


DEMO_DATA: list[DemoCompany] = [
    DemoCompany(
        name="Demo Payments Co",
        domain="demo-payments.example",
        industry="Fintech",
        priority=CompanyPriority.CRITICAL,
        jobs=[
            DemoJob(
                "Product Analyst",
                "Bangalore, India",
                14,
                "Own product analytics for our payments funnel. You will write SQL daily, "
                "run A/B testing on checkout flows, build dashboards for the product team, "
                "and work closely with product managers on roadmapping decisions.",
            ),
            DemoJob(
                "Associate Product Manager",
                "Remote - India",
                30,
                "Work with product, design and engineering on our merchant onboarding "
                "experience. Strong analytical background and experimentation experience "
                "expected. Product analytics and stakeholder management are core.",
            ),
            DemoJob(
                "Senior Backend Engineer",
                "Bangalore, India",
                96,
                "Design and build distributed payment systems in Go and Kubernetes.",
            ),
        ],
        recruiters=[
            DemoRecruiter(
                "Demo Recruiter One",
                "Talent Acquisition Partner, Product",
                f"demo.recruiter.one@demo-payments{DEMO_TLD}",
                EmailConfidence.HIGH,
                SourceType.COMPANY_TEAM_PAGE,
                verified=True,
            ),
            DemoRecruiter(
                "Demo Payments Talent Team",
                "Talent / Recruiting (shared team address)",
                f"careers@demo-payments{DEMO_TLD}",
                EmailConfidence.MEDIUM,
                SourceType.CAREER_PAGE,
            ),
        ],
    ),
    DemoCompany(
        name="Demo Consumer App",
        domain="demo-consumer.example",
        industry="Consumer Technology",
        priority=CompanyPriority.HIGH,
        jobs=[
            DemoJob(
                "Growth Analyst",
                "Mumbai, India",
                20,
                "Drive growth analytics across acquisition and retention. Heavy SQL, "
                "experimentation, and dashboarding in Looker. Partner with the product team.",
            ),
            DemoJob(
                "Product Operations Associate",
                "Remote",
                55,
                "Run product ops: release coordination, product metrics reporting, and "
                "cross functional stakeholder management.",
            ),
        ],
        recruiters=[
            DemoRecruiter(
                "Demo Recruiter Two",
                "Technical Recruiter - Product & Data",
                f"demo.recruiter.two@demo-consumer{DEMO_TLD}",
                EmailConfidence.HIGH,
                SourceType.COMPANY_TEAM_PAGE,
                verified=True,
            ),
            DemoRecruiter(
                "Demo Recruiter Three",
                "Head of People",
                f"demo.recruiter.three@demo-consumer{DEMO_TLD}",
                EmailConfidence.LOW,
                SourceType.PATTERN_INFERENCE,
                inferred=True,
            ),
        ],
    ),
    DemoCompany(
        name="Demo SaaS Platform",
        domain="demo-saas.example",
        industry="SaaS",
        priority=CompanyPriority.MEDIUM,
        jobs=[
            DemoJob(
                "Business Analyst",
                "Hyderabad, India",
                72,
                "Support revenue operations with SQL analysis, reporting and process design "
                "for our B2B SaaS platform.",
            ),
            DemoJob(
                "Data Analyst",
                "Remote - India",
                8,
                "Build and maintain analytics models. Python, SQL and dashboarding. "
                "Partner with product on experimentation.",
            ),
        ],
        recruiters=[
            DemoRecruiter(
                "Demo Recruiter Four",
                "Talent Acquisition Specialist",
                f"demo.recruiter.four@demo-saas{DEMO_TLD}",
                EmailConfidence.MEDIUM,
                SourceType.JOB_POSTING_CONTACT,
            ),
        ],
    ),
    DemoCompany(
        name="Demo AI Labs",
        domain="demo-ai.example",
        industry="AI Products",
        priority=CompanyPriority.HIGH,
        jobs=[
            DemoJob(
                "AI Product Analyst",
                "Bangalore, India",
                4,
                "Analyse how customers use our LLM products. Product analytics, "
                "experimentation, SQL, and close work with AI product managers.",
            ),
        ],
        recruiters=[
            DemoRecruiter(
                "Demo Recruiter Five",
                "Talent Partner, AI Product",
                None,
                EmailConfidence.NONE,
                SourceType.COMPANY_TEAM_PAGE,
            ),
        ],
    ),
]


def _assert_safe(email: str | None) -> None:
    """Refuse to seed anything that could reach a real mailbox."""
    if email is None:
        return
    domain = email.rsplit("@", 1)[-1].lower()
    if not domain.endswith(DEMO_TLD):
        raise ValueError(
            f"Refusing to seed demo address {email!r}: demo data must use the reserved "
            f"'{DEMO_TLD}' TLD so it can never reach a real person."
        )


def clear_demo_data(session: Session, user: User) -> int:
    """Remove every demo record for a user. Real records are untouched."""
    companies = list(
        session.scalars(
            select(Company).where(Company.user_id == user.id, Company.is_demo.is_(True))
        ).all()
    )
    if not companies:
        return 0
    ids = [c.id for c in companies]
    from app.models.outreach import OutreachLead

    session.execute(delete(OutreachLead).where(OutreachLead.company_id.in_(ids)))
    for company in companies:
        session.delete(company)
    session.flush()
    log.info("seed.demo_cleared", companies=len(companies))
    return len(companies)


def seed_demo_data(
    session: Session,
    user: User,
    *,
    reset: bool = False,
    seed_profile: bool = True,
    rng_seed: int = 42,
) -> dict[str, int]:
    """Create the demo dataset. Idempotent when ``reset`` is True."""
    rng = random.Random(rng_seed)
    if reset:
        clear_demo_data(session, user)

    ensure_scoring_config(session, user.id)
    seed_default_taxonomy(session, user.id)

    if seed_profile:
        _seed_demo_profile(session, user)

    now = utcnow()
    counts = {"companies": 0, "jobs": 0, "recruiters": 0, "contacts": 0}

    for spec in DEMO_DATA:
        display_name = f"{spec.name} {DEMO_MARKER}"
        existing = session.scalar(
            select(Company).where(
                Company.user_id == user.id,
                Company.normalized_name == normalize_company_name(display_name),
            )
        )
        if existing is not None:
            continue

        company = Company(
            user_id=user.id,
            company_name=display_name,
            normalized_name=normalize_company_name(display_name),
            company_domain=spec.domain,
            career_page_url=f"https://{spec.domain}/careers",
            industry=spec.industry,
            priority=spec.priority,
            active=True,
            is_demo=True,
            notes="Synthetic demo record. Not a real company; contacts cannot receive mail.",
            last_checked_at=now - timedelta(minutes=rng.randint(5, 90)),
            last_scan_status="SUCCESS",
        )
        session.add(company)
        session.flush()
        counts["companies"] += 1

        for job_spec in spec.jobs:
            posted = now - timedelta(hours=job_spec.hours_ago)
            slug = basic_normalize(job_spec.title).replace(" ", "-")
            url = f"https://{spec.domain}/careers/{slug}"
            job = Job(
                company_id=company.id,
                title=f"{job_spec.title} {DEMO_MARKER}",
                normalized_title=normalize_title(job_spec.title),
                description=job_spec.description,
                location=job_spec.location,
                normalized_location=normalize_location(job_spec.location),
                employment_type=job_spec.employment_type,
                job_url=url,
                canonical_url=url,
                content_hash=content_fingerprint(
                    display_name, normalize_title(job_spec.title), normalize_location(job_spec.location)
                ),
                source=SourceType.DEMO_SEED,
                source_job_id=f"demo-{slug}",
                posted_at=posted,
                discovered_at=posted + timedelta(minutes=rng.randint(10, 120)),
                last_seen_at=now,
                status=JobStatus.OPEN,
                is_demo=True,
            )
            session.add(job)
            counts["jobs"] += 1

        for rec_spec in spec.recruiters:
            _assert_safe(rec_spec.email)
            name = f"{rec_spec.name} {DEMO_MARKER}"
            recruiter = Recruiter(
                company_id=company.id,
                name=name,
                normalized_name=basic_normalize(name),
                company_name=display_name,
                title=rec_spec.title,
                professional_profile_url=f"https://{spec.domain}/team/{basic_normalize(rec_spec.name).replace(' ', '-')}",
                public_professional_email=rec_spec.email,
                email_source_url=(
                    None
                    if rec_spec.inferred or not rec_spec.email
                    else f"https://{spec.domain}/careers"
                ),
                email_source_type=rec_spec.source_type if rec_spec.email else None,
                email_confidence=rec_spec.confidence,
                email_confidence_score=rec_spec.confidence.score,
                # An inferred address is never verified, in demo data either.
                email_verified=rec_spec.verified and not rec_spec.inferred,
                email_verification_status=(
                    VerificationStatus.VALID
                    if (rec_spec.verified and not rec_spec.inferred)
                    else VerificationStatus.NOT_CHECKED
                ),
                email_verified_at=now if rec_spec.verified and not rec_spec.inferred else None,
                discovered_at=now - timedelta(days=rng.randint(1, 20)),
                last_seen=now - timedelta(hours=rng.randint(1, 48)),
                is_demo=True,
                notes="Synthetic demo contact.",
            )
            session.add(recruiter)
            session.flush()
            counts["recruiters"] += 1

            if rec_spec.email:
                session.add(
                    Contact(
                        recruiter_id=recruiter.id,
                        contact_type=ContactType.EMAIL,
                        value=rec_spec.email,
                        source_url=(
                            None if rec_spec.inferred else f"https://{spec.domain}/careers"
                        ),
                        source_type=rec_spec.source_type,
                        source_excerpt=(
                            "INFERRED from a name pattern — not published anywhere."
                            if rec_spec.inferred
                            else "Published on the company's public careers page (synthetic)."
                        ),
                        confidence=rec_spec.confidence,
                        is_inferred=rec_spec.inferred,
                        is_primary=True,
                    )
                )
                counts["contacts"] += 1
        session.flush()

    # Score everything so the demo dashboard is immediately meaningful.
    context = build_context(session, user.id)
    weights = load_weights(session, user.id)
    for company in session.scalars(
        select(Company).where(Company.user_id == user.id, Company.is_demo.is_(True))
    ).all():
        for job in session.scalars(select(Job).where(Job.company_id == company.id)).all():
            apply_job_score(job, context, weights)
        session.flush()
        rescore_company_recruiters(session, company)
        link_recruiters_to_jobs(session, company.id)
        refresh_company_hiring_activity(session, company, weights)

    session.flush()
    log.info("seed.demo_created", **counts)
    return counts


def _seed_demo_profile(session: Session, user: User) -> None:
    """Fill an empty profile so the AI drafter has something to work with."""
    profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is None:
        profile = UserProfile(user_id=user.id)
        session.add(profile)
        session.flush()
    if profile.full_name or profile.experience:
        return  # never overwrite a real profile

    profile.full_name = f"Demo Candidate {DEMO_MARKER}"
    profile.headline = "product analyst with 2 years in payments and consumer analytics"
    profile.years_experience = 2.0
    profile.education = "B.Tech, Computer Science (synthetic demo profile)"
    profile.experience = (
        "Product Analyst at a synthetic demo company: owned the retention dashboard used "
        "by the payments team, ran experimentation on the checkout funnel, and partnered "
        "with product managers on quarterly roadmapping."
    )
    profile.skills = [
        "SQL",
        "Product Analytics",
        "A/B Testing",
        "Python",
        "Dashboarding",
        "Roadmapping",
    ]
    profile.target_roles = [
        "Associate Product Manager",
        "Product Analyst",
        "Product Operations",
        "Business Analyst",
        "Growth Analyst",
        "AI Product",
        "Data Analyst",
        "Product Strategy",
    ]
    profile.target_industries = ["Fintech", "Consumer Technology", "SaaS", "AI Products"]
    profile.preferred_locations = ["Remote", "Bangalore", "Mumbai"]
    profile.portfolio_url = "https://demo-candidate.example"
    session.flush()
