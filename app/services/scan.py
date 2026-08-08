"""The company scan — the vertical slice that ties the whole system together.

    collectors -> ingest -> score jobs -> discover recruiters -> score recruiters
              -> link jobs to recruiters -> recompute hiring activity

Everything is wrapped in crawl-run tracking, so the admin page can always show
what ran, what it found, and what went wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.http_client import SafeHTTPClient
from app.collectors.registry import (
    build_job_collectors,
    build_recruiter_collectors,
    is_authoritative_source,
)
from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.company import Company
from app.models.enums import CrawlStatus, CrawlTrigger, SourceType
from app.models.recruiter import Recruiter
from app.services.crawl_tracking import fail_crawl_run, finish_crawl_run, start_crawl_run
from app.services.jobs.ingest import ingest_jobs
from app.services.recruiters.inference import as_candidate, infer_for_recruiter
from app.services.recruiters.ingest import ingest_recruiters
from app.services.recruiters.linking import link_recruiters_to_jobs
from app.services.scoring.context import build_context
from app.services.scoring.hiring_activity import refresh_company_hiring_activity
from app.services.scoring.recruiter_relevance import apply_recruiter_score
from app.services.scoring.weights import load_weights

log = get_logger(__name__)


@dataclass
class ScanSummary:
    company_id: int
    company_name: str
    jobs_found: int = 0
    jobs_added: int = 0
    jobs_updated: int = 0
    jobs_closed: int = 0
    recruiters_found: int = 0
    recruiters_added: int = 0
    emails_found: int = 0
    inferred_emails: int = 0
    links_created: int = 0
    hiring_activity_score: float = 0.0
    crawl_run_ids: list[int] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return CrawlStatus.FAILED.value not in self.statuses

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": self.company_id,
            "company_name": self.company_name,
            "jobs_found": self.jobs_found,
            "jobs_added": self.jobs_added,
            "jobs_updated": self.jobs_updated,
            "jobs_closed": self.jobs_closed,
            "recruiters_found": self.recruiters_found,
            "recruiters_added": self.recruiters_added,
            "emails_found": self.emails_found,
            "inferred_emails": self.inferred_emails,
            "links_created": self.links_created,
            "hiring_activity_score": self.hiring_activity_score,
            "crawl_run_ids": self.crawl_run_ids,
            "statuses": self.statuses,
            "errors": self.errors,
            "notes": self.notes,
            "ok": self.ok,
        }


def scan_company(
    session: Session,
    company: Company,
    *,
    trigger: CrawlTrigger = CrawlTrigger.MANUAL,
    discover_recruiters: bool = True,
    infer_emails: bool = False,
    extra_recruiter_urls: list[str] | None = None,
    client: SafeHTTPClient | None = None,
) -> ScanSummary:
    """Run a full discovery pass for one company."""
    summary = ScanSummary(company_id=company.id, company_name=company.company_name)
    weights = load_weights(session, company.user_id)
    context = build_context(session, company.user_id)

    owns_client = client is None
    client = client or SafeHTTPClient()

    try:
        # --- Jobs ------------------------------------------------------------
        job_collectors = build_job_collectors(company, client=client)
        if not job_collectors:
            summary.notes.append(
                "No career page URL set for this company — add one to discover jobs."
            )

        for job_collector in job_collectors:
            run = start_crawl_run(
                session,
                collector=job_collector.name,
                source=job_collector.source_type,
                company=company,
                target_url=getattr(job_collector, "api_url", None)
                or getattr(job_collector, "career_page_url", None),
                trigger=trigger,
            )
            try:
                outcome = job_collector.run()
                job_ingest = ingest_jobs(
                    session,
                    company,
                    outcome.records,
                    context,
                    weights,
                    authoritative=is_authoritative_source(job_collector),
                )
                finish_crawl_run(
                    session, run, outcome, added=job_ingest.added, updated=job_ingest.updated
                )
                summary.jobs_found += outcome.found
                summary.jobs_added += job_ingest.added
                summary.jobs_updated += job_ingest.updated
                summary.jobs_closed += job_ingest.closed
                summary.crawl_run_ids.append(run.id)
                summary.statuses.append(run.status.value)
                summary.errors.extend(e.message for e in outcome.errors)
                summary.notes.extend(outcome.notes)
            except Exception as exc:  # noqa: BLE001
                fail_crawl_run(session, run, f"{type(exc).__name__}: {exc}")
                summary.crawl_run_ids.append(run.id)
                summary.statuses.append(CrawlStatus.FAILED.value)
                summary.errors.append(f"{job_collector.name}: {exc}")
                log.exception("scan.job_collector_failed", collector=job_collector.name)

        # --- Recruiters -------------------------------------------------------
        if discover_recruiters:
            for rec_collector in build_recruiter_collectors(
                company, extra_urls=extra_recruiter_urls, client=client
            ):
                run = start_crawl_run(
                    session,
                    collector=rec_collector.name,
                    source=rec_collector.source_type,
                    company=company,
                    target_url=rec_collector.urls[0] if rec_collector.urls else None,
                    trigger=trigger,
                )
                try:
                    outcome = rec_collector.run()
                    rec_ingest = ingest_recruiters(session, company, outcome.records)
                    finish_crawl_run(
                        session, run, outcome, added=rec_ingest.added, updated=rec_ingest.updated
                    )
                    summary.recruiters_found += outcome.found
                    summary.recruiters_added += rec_ingest.added
                    summary.emails_found += rec_ingest.emails_found
                    summary.crawl_run_ids.append(run.id)
                    summary.statuses.append(run.status.value)
                    summary.errors.extend(e.message for e in outcome.errors)
                    summary.notes.extend(outcome.notes)
                except Exception as exc:  # noqa: BLE001
                    fail_crawl_run(session, run, f"{type(exc).__name__}: {exc}")
                    summary.crawl_run_ids.append(run.id)
                    summary.statuses.append(CrawlStatus.FAILED.value)
                    summary.errors.append(f"{rec_collector.name}: {exc}")
                    log.exception("scan.recruiter_collector_failed")

        # --- Optional low-confidence inference --------------------------------
        if infer_emails:
            summary.inferred_emails = run_email_inference(session, company)

        # --- Scoring ----------------------------------------------------------
        summary.links_created = link_recruiters_to_jobs(session, company.id)
        rescore_company_recruiters(session, company)
        result, _ = refresh_company_hiring_activity(session, company, weights)
        summary.hiring_activity_score = result.total

        company.last_checked_at = utcnow()
        company.last_scan_status = (
            CrawlStatus.FAILED.value
            if not summary.ok
            else (summary.statuses[0] if summary.statuses else CrawlStatus.SKIPPED.value)
        )
        session.flush()
    finally:
        if owns_client:
            client.close()

    log.info("scan.complete", **summary.to_dict())
    return summary


def run_email_inference(session: Session, company: Company) -> int:
    """Generate low-confidence pattern addresses for recruiters lacking one."""
    recruiters = list(
        session.scalars(
            select(Recruiter).where(
                Recruiter.company_id == company.id,
                Recruiter.public_professional_email.is_(None),
                Recruiter.do_not_contact.is_(False),
            )
        ).all()
    )
    candidates = []
    for recruiter in recruiters:
        inferred = infer_for_recruiter(session, company, recruiter)
        if inferred is not None:
            candidates.append(as_candidate(recruiter, inferred, company))
    if not candidates:
        return 0

    run = start_crawl_run(
        session,
        collector="pattern_inference",
        source=SourceType.PATTERN_INFERENCE,
        company=company,
        trigger=CrawlTrigger.MANUAL,
    )
    ingest = ingest_recruiters(session, company, candidates)
    from app.collectors.types import CollectionOutcome

    outcome = CollectionOutcome(
        collector="pattern_inference",
        source=SourceType.PATTERN_INFERENCE,
        records=list(candidates),
        notes=[
            f"{len(candidates)} address(es) INFERRED from a name pattern. These are guesses: "
            "they are not published anywhere and are never treated as verified."
        ],
    )
    finish_crawl_run(session, run, outcome, added=ingest.emails_found)
    return ingest.emails_found


def rescore_company_recruiters(session: Session, company: Company) -> int:
    """Recompute recruiter relevance using the company's current hiring picture."""
    from app.models.enums import JobStatus
    from app.models.job import Job

    context = build_context(session, company.user_id)
    weights = load_weights(session, company.user_id)

    jobs = list(
        session.scalars(
            select(Job).where(Job.company_id == company.id, Job.status == JobStatus.OPEN)
        ).all()
    )
    relevant = sorted(
        [j for j in jobs if j.relevance_score >= float(weights.option("relevance_threshold"))],
        key=lambda j: (j.age_hours if j.age_hours is not None else 1e9),
    )
    freshest = relevant[0] if relevant else None

    recruiters = list(
        session.scalars(select(Recruiter).where(Recruiter.company_id == company.id)).all()
    )
    for recruiter in recruiters:
        apply_recruiter_score(
            recruiter,
            company,
            context,
            weights,
            has_relevant_job=freshest is not None,
            freshest_job_age_hours=freshest.age_hours if freshest else None,
            freshest_job_title=freshest.title if freshest else None,
        )
    session.flush()
    return len(recruiters)
