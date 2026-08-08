"""Crawl run lifecycle.

Opens a `crawl_runs` row before the first network call and always closes it
with a terminal status, so a crawl can never disappear without a trace. Raw
items — including rejected ones and the reason they were rejected — are written
to `source_records`.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.collectors.types import CollectionOutcome
from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.company import Company
from app.models.crawl import CrawlRun, SourceRecord
from app.models.enums import CrawlStatus, CrawlTrigger, SourceType

log = get_logger(__name__)

MAX_SOURCE_RECORDS_PER_RUN = 200


def start_crawl_run(
    session: Session,
    *,
    collector: str,
    source: SourceType,
    company: Company | None = None,
    target_url: str | None = None,
    trigger: CrawlTrigger = CrawlTrigger.MANUAL,
) -> CrawlRun:
    run = CrawlRun(
        company_id=company.id if company else None,
        collector=collector,
        source=source,
        target_url=target_url,
        trigger=trigger,
        status=CrawlStatus.RUNNING,
    )
    session.add(run)
    session.flush()
    log.info("crawl.start", crawl_id=run.id, collector=collector, target=target_url)
    return run


def _payload(record: Any) -> dict[str, Any]:
    if is_dataclass(record) and not isinstance(record, type):
        data = asdict(record)
    elif isinstance(record, dict):
        data = dict(record)
    else:
        return {"repr": str(record)[:500]}
    return {
        key: (value.isoformat() if hasattr(value, "isoformat") else value)
        for key, value in data.items()
        if key != "payload"
    }


def finish_crawl_run(
    session: Session,
    run: CrawlRun,
    outcome: CollectionOutcome,
    *,
    added: int = 0,
    updated: int = 0,
    record_sources: bool = True,
) -> CrawlRun:
    """Close a crawl run from a collector outcome."""
    run.completed_at = utcnow()
    run.records_found = outcome.found
    run.records_added = added
    run.records_updated = updated
    run.records_rejected = len(outcome.rejected)
    run.pages_fetched = outcome.pages_fetched
    run.bytes_downloaded = outcome.bytes_downloaded
    run.rate_limit_waits = outcome.rate_limit_waits
    run.rate_limit_seconds = round(outcome.rate_limit_seconds, 2)
    run.error_count = len(outcome.errors)
    run.errors = [error.to_dict() for error in outcome.errors]
    run.notes = "\n".join(outcome.notes) if outcome.notes else None

    if outcome.failed:
        run.status = CrawlStatus.FAILED
    elif outcome.blocked_reason:
        run.status = CrawlStatus.BLOCKED
    elif outcome.errors:
        run.status = CrawlStatus.PARTIAL
    else:
        run.status = CrawlStatus.SUCCESS

    if record_sources:
        written = 0
        for record in outcome.records:
            if written >= MAX_SOURCE_RECORDS_PER_RUN:
                break
            session.add(
                SourceRecord(
                    crawl_run_id=run.id,
                    record_type=getattr(record, "record_type", None) or _guess_type(record),
                    source_type=getattr(record, "source", outcome.source),
                    source_url=getattr(record, "source_url", None),
                    external_id=getattr(record, "external_id", None),
                    content_hash=getattr(record, "content_hash", None),
                    accepted=True,
                    raw_payload=_payload(record),
                )
            )
            written += 1
        for record, reason in outcome.rejected:
            if written >= MAX_SOURCE_RECORDS_PER_RUN:
                break
            session.add(
                SourceRecord(
                    crawl_run_id=run.id,
                    record_type=_guess_type(record),
                    source_type=getattr(record, "source", outcome.source),
                    source_url=getattr(record, "source_url", None),
                    external_id=getattr(record, "external_id", None),
                    content_hash=getattr(record, "content_hash", None),
                    accepted=False,
                    reject_reason=(reason or "")[:300],
                    raw_payload=_payload(record),
                )
            )
            written += 1

    session.flush()
    log.info(
        "crawl.finish",
        crawl_id=run.id,
        status=run.status.value,
        found=run.records_found,
        added=run.records_added,
        errors=run.error_count,
    )
    return run


def fail_crawl_run(session: Session, run: CrawlRun, message: str) -> CrawlRun:
    """Close a run that blew up outside the collector pipeline."""
    run.completed_at = utcnow()
    run.status = CrawlStatus.FAILED
    run.error_count += 1
    run.errors = [*(run.errors or []), {"stage": "orchestration", "message": message[:1000], "fatal": True}]
    session.flush()
    log.warning("crawl.failed", crawl_id=run.id, message=message)
    return run


def _guess_type(record: Any) -> str:
    name = type(record).__name__.lower()
    if "recruiter" in name:
        return "recruiter"
    if "job" in name:
        return "job"
    return "unknown"
