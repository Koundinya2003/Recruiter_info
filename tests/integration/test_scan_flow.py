"""Database-backed flow: collect → ingest → dedupe → score → link → signals."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.http_client import SafeHTTPClient
from app.collectors.job_sources.ats import GreenhouseCollector
from app.collectors.public_sources.recruiter_sources import PublicRecruiterSourceCollector
from app.collectors.types import NormalizedRecruiter
from app.db.base import utcnow
from app.models.company import Company
from app.models.crawl import SourceRecord
from app.models.enums import (
    CrawlStatus,
    EmailConfidence,
    JobStatus,
    SourceType,
    VerificationStatus,
)
from app.models.job import Job
from app.models.recruiter import Contact, JobRecruiterLink, Recruiter
from app.models.signal import HiringSignal
from app.models.user import User
from app.services.crawl_tracking import finish_crawl_run, start_crawl_run
from app.services.email.service import verify_recruiter_email
from app.services.email.verifier import DNSVerifier
from app.services.jobs.ingest import ingest_jobs
from app.services.recruiters.inference import detect_pattern, infer_email
from app.services.recruiters.ingest import ingest_recruiters
from app.services.recruiters.linking import link_recruiters_to_jobs
from app.services.scan import rescore_company_recruiters, run_email_inference
from app.services.scoring.context import build_context
from app.services.scoring.hiring_activity import refresh_company_hiring_activity
from app.services.scoring.outreach_priority import ContactTodayFilters, contact_today
from app.services.scoring.weights import load_weights
from tests.conftest import requires_db
from tests.fixtures.mock_server import MockSite

pytestmark = requires_db


@pytest.fixture
def http() -> SafeHTTPClient:
    client = SafeHTTPClient(allow_private=True, max_pages=40, max_retries=0)
    yield client
    client.close()


def _collect_jobs(mock_site: MockSite, http: SafeHTTPClient, company: Company):
    collector = GreenhouseCollector(
        company_name=company.company_name,
        board_token="demo",
        client=http,
        api_url=mock_site.url("/v1/boards/demo/jobs"),
    )
    return collector.run()


def test_ingest_scores_and_persists_jobs(
    session: Session, user: User, company: Company, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    outcome = _collect_jobs(mock_site, http, company)
    context = build_context(session, user.id)
    weights = load_weights(session, user.id)

    result = ingest_jobs(session, company, outcome.records, context, weights, authoritative=True)
    assert result.added == len(outcome.records)

    jobs = list(session.scalars(select(Job).where(Job.company_id == company.id)).all())
    assert jobs
    for job in jobs:
        assert job.relevance_scored_at is not None
        assert job.relevance_breakdown["components"]
        assert job.content_hash

    analyst = next(j for j in jobs if j.title == "Product Analyst")
    assert analyst.relevance_score >= 80

    sales = next(j for j in jobs if "Sales" in j.title)
    assert sales.relevance_score == 0  # excluded by the taxonomy


def test_reingesting_does_not_duplicate(
    session: Session, user: User, company: Company, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    context = build_context(session, user.id)
    first = _collect_jobs(mock_site, http, company)
    ingest_jobs(session, company, first.records, context)

    second = _collect_jobs(mock_site, http, company)
    result = ingest_jobs(session, company, second.records, context)
    assert result.added == 0
    assert result.duplicates == len(second.records)

    jobs = list(session.scalars(select(Job).where(Job.company_id == company.id)).all())
    assert len(jobs) == len(first.records)


def test_same_job_from_a_different_source_is_not_duplicated(
    session: Session, user: User, company: Company, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    from app.collectors.types import NormalizedJob
    from app.utils.text import content_fingerprint, normalize_location, normalize_title

    context = build_context(session, user.id)
    outcome = _collect_jobs(mock_site, http, company)
    ingest_jobs(session, company, outcome.records, context)
    before = len(list(session.scalars(select(Job).where(Job.company_id == company.id)).all()))

    # The same posting, seen on the company's own site under a different URL.
    same_job = NormalizedJob(
        title="Product Analyst",
        normalized_title=normalize_title("Product Analyst"),
        url="https://testly.example/careers/product-analyst",
        canonical_url="https://testly.example/careers/product-analyst",
        content_hash=content_fingerprint(
            company.company_name, normalize_title("Product Analyst"), normalize_location("Bangalore, India")
        ),
        source=SourceType.CAREER_PAGE_JSONLD,
        source_url="https://testly.example/careers",
        location="Bangalore, India",
        normalized_location=normalize_location("Bangalore, India"),
    )
    result = ingest_jobs(session, company, [same_job], context)
    assert result.added == 0
    assert result.duplicates == 1
    after = len(list(session.scalars(select(Job).where(Job.company_id == company.id)).all()))
    assert after == before


def test_authoritative_source_closes_disappeared_jobs(
    session: Session, user: User, company: Company, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    context = build_context(session, user.id)
    outcome = _collect_jobs(mock_site, http, company)
    ingest_jobs(session, company, outcome.records, context, authoritative=True)

    # Next run returns only one of the postings.
    remaining = [r for r in outcome.records if r.title == "Product Analyst"]
    result = ingest_jobs(session, company, remaining, context, authoritative=True)
    assert result.closed >= 1

    closed = list(
        session.scalars(
            select(Job).where(Job.company_id == company.id, Job.status == JobStatus.CLOSED)
        ).all()
    )
    assert closed


def test_non_authoritative_source_never_closes_jobs(
    session: Session, user: User, company: Company, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    context = build_context(session, user.id)
    outcome = _collect_jobs(mock_site, http, company)
    ingest_jobs(session, company, outcome.records, context, authoritative=True)

    remaining = [r for r in outcome.records if r.title == "Product Analyst"]
    result = ingest_jobs(session, company, remaining, context, authoritative=False)
    assert result.closed == 0


def test_recruiter_ingest_keeps_provenance(
    session: Session, company: Company, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    collector = PublicRecruiterSourceCollector(
        company_name=company.company_name,
        urls=[mock_site.url("/team")],
        company_domain="demo-fintech.example",
        client=http,
    )
    outcome = collector.run()
    # The collector validates against the company's own domain, so relabel for this test.
    for record in outcome.records:
        record.company_name = company.company_name

    result = ingest_recruiters(session, company, outcome.records)
    assert result.added == len(outcome.records)

    contacts = list(session.scalars(select(Contact)).all())
    assert contacts
    for contact in contacts:
        assert contact.source_type is not None
        if not contact.is_inferred:
            assert contact.source_url, "a published contact must record where it was published"


def test_inferred_email_never_overwrites_a_published_one(
    session: Session, company: Company, recruiter: Recruiter
) -> None:
    published = recruiter.public_professional_email
    inferred = NormalizedRecruiter(
        name=recruiter.name,
        normalized_name=recruiter.normalized_name,
        company_name=company.company_name,
        title=recruiter.title,
        email="c.talent@testly.example",
        source=SourceType.PATTERN_INFERENCE,
        source_url=None,
        confidence=EmailConfidence.LOW,
        is_inferred=True,
    )
    ingest_recruiters(session, company, [inferred])
    session.refresh(recruiter)
    assert recruiter.public_professional_email == published
    assert recruiter.email_confidence is EmailConfidence.HIGH


def test_inferred_email_is_never_marked_verified(
    session: Session, company: Company
) -> None:
    guessed = NormalizedRecruiter(
        name="Guessy Person",
        normalized_name="guessy person",
        company_name=company.company_name,
        title="Talent Partner",
        email="guessy.person@testly.example",
        source=SourceType.PATTERN_INFERENCE,
        source_url=None,
        confidence=EmailConfidence.LOW,
        is_inferred=True,
    )
    ingest_recruiters(session, company, [guessed])
    recruiter = session.scalar(
        select(Recruiter).where(Recruiter.normalized_name == "guessy person")
    )
    assert recruiter is not None
    assert recruiter.email_confidence is EmailConfidence.LOW
    assert recruiter.email_verified is False

    verify_recruiter_email(session, recruiter, DNSVerifier())
    session.refresh(recruiter)
    # Checked, recorded — but still not "verified", because it is a guess.
    assert recruiter.email_verification_status is not VerificationStatus.NOT_CHECKED
    assert recruiter.email_verified is False


def test_pattern_detection_learns_from_a_published_address() -> None:
    detected = detect_pattern({"priya.sharma@acme.com": "Priya Sharma"})
    assert detected is not None
    pattern, basis = detected
    assert pattern == "{first}.{last}"
    assert "Priya Sharma" in basis

    guess = infer_email("Casey Talent", "acme.com", pattern=pattern)
    assert guess is not None
    assert guess.email == "casey.talent@acme.com"
    assert guess.confidence is EmailConfidence.LOW


def test_inference_requires_a_company_domain(session: Session, user: User) -> None:
    company = Company(
        user_id=user.id, company_name="No Domain Co", normalized_name="no domain", active=True
    )
    session.add(company)
    session.flush()
    session.add(
        Recruiter(
            company_id=company.id,
            name="Someone Talent",
            normalized_name="someone talent",
            company_name=company.company_name,
            title="Recruiter",
        )
    )
    session.flush()
    assert run_email_inference(session, company) == 0


def test_linking_records_a_rationale(
    session: Session, user: User, company: Company, recruiter: Recruiter, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    context = build_context(session, user.id)
    outcome = _collect_jobs(mock_site, http, company)
    ingest_jobs(session, company, outcome.records, context)

    created = link_recruiters_to_jobs(session, company.id)
    assert created > 0
    links = list(session.scalars(select(JobRecruiterLink)).all())
    for link in links:
        assert link.rationale
        assert 0 < link.confidence <= 1


def test_hiring_signals_are_recorded_and_scored(
    session: Session, user: User, company: Company, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    context = build_context(session, user.id)
    outcome = _collect_jobs(mock_site, http, company)
    ingest_jobs(session, company, outcome.records, context)
    rescore_company_recruiters(session, company)

    result, signals = refresh_company_hiring_activity(session, company)
    assert company.hiring_activity_score > 0
    assert result.reasons
    stored = list(session.scalars(select(HiringSignal).where(HiringSignal.company_id == company.id)).all())
    assert len(stored) == len(signals)


def test_crawl_runs_are_always_closed(
    session: Session, company: Company, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    run = start_crawl_run(
        session,
        collector="greenhouse_public_api",
        source=SourceType.GREENHOUSE_PUBLIC_API,
        company=company,
        target_url=mock_site.url("/v1/boards/demo/jobs"),
    )
    assert run.status is CrawlStatus.RUNNING

    outcome = _collect_jobs(mock_site, http, company)
    finish_crawl_run(session, run, outcome, added=len(outcome.records))

    session.refresh(run)
    assert run.status is CrawlStatus.SUCCESS
    assert run.completed_at is not None
    assert run.records_found == outcome.found
    assert run.pages_fetched >= 1

    # Rejected records are kept, with the reason, for auditability.
    records = list(session.scalars(select(SourceRecord).where(SourceRecord.crawl_run_id == run.id)).all())
    assert records
    rejected = [r for r in records if not r.accepted]
    assert rejected and all(r.reject_reason for r in rejected)


def test_blocked_crawl_is_recorded_as_blocked(session: Session, company: Company) -> None:
    from app.collectors.types import CollectionOutcome

    run = start_crawl_run(
        session, collector="career_page", source=SourceType.CAREER_PAGE, company=company
    )
    finish_crawl_run(
        session,
        run,
        CollectionOutcome(
            collector="career_page", source=SourceType.CAREER_PAGE, blocked_reason="robots_disallowed"
        ),
    )
    session.refresh(run)
    assert run.status is CrawlStatus.BLOCKED


def test_contact_today_ranks_and_respects_do_not_contact(
    session: Session, user: User, company: Company, recruiter: Recruiter, mock_site: MockSite, http: SafeHTTPClient
) -> None:
    context = build_context(session, user.id)
    outcome = _collect_jobs(mock_site, http, company)
    ingest_jobs(session, company, outcome.records, context)
    rescore_company_recruiters(session, company)
    refresh_company_hiring_activity(session, company)
    link_recruiters_to_jobs(session, company.id)

    opportunities = contact_today(session, user.id, ContactTodayFilters(limit=10))
    assert opportunities
    scores = [o.priority for o in opportunities]
    assert scores == sorted(scores, reverse=True)
    assert all(o.score.reasons for o in opportunities)

    recruiter.do_not_contact = True
    session.flush()
    after = contact_today(session, user.id, ContactTodayFilters(limit=10))
    assert all(o.recruiter.id != recruiter.id for o in after)


def test_contact_today_excludes_stale_and_irrelevant(
    session: Session, user: User, company: Company, recruiter: Recruiter
) -> None:
    stale = Job(
        company_id=company.id,
        title="Warehouse Supervisor",
        normalized_title="warehouse supervisor",
        job_url="https://testly.example/j/9",
        canonical_url="https://testly.example/j/9",
        content_hash="stalehash",
        source=SourceType.MANUAL_ENTRY,
        posted_at=utcnow() - timedelta(days=200),
        relevance_score=10.0,
    )
    session.add(stale)
    session.flush()
    assert contact_today(session, user.id, ContactTodayFilters(limit=10)) == []
