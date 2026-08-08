"""Background scanning with APScheduler.

Deliberately simple: one in-process job that walks the active companies on an
interval, with the same rate limiting and crawl-run tracking as a manual scan.
No broker, no worker fleet — a single-user research tool does not need one.
"""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.logging_config import get_logger
from app.models.company import Company
from app.models.enums import CrawlTrigger
from app.services.scan import scan_company

log = get_logger(__name__)


def scan_all_active_companies(*, trigger: CrawlTrigger = CrawlTrigger.SCHEDULED) -> dict[str, int]:
    """Scan every active company. Returns aggregate counts."""
    totals = {"companies": 0, "jobs_added": 0, "recruiters_added": 0, "failures": 0}
    with session_scope() as session:
        companies = list(
            session.scalars(
                select(Company).where(Company.active.is_(True), Company.is_demo.is_(False))
            ).all()
        )
        for company in companies:
            totals["companies"] += 1
            try:
                summary = scan_company(session, company, trigger=trigger)
                totals["jobs_added"] += summary.jobs_added
                totals["recruiters_added"] += summary.recruiters_added
                if not summary.ok:
                    totals["failures"] += 1
            except Exception:  # noqa: BLE001 - one bad company must not stop the sweep
                totals["failures"] += 1
                log.exception("scheduler.company_scan_failed", company=company.company_name)
    log.info("scheduler.sweep_complete", **totals)
    return totals


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        scan_all_active_companies,
        "interval",
        minutes=settings.scheduler_interval_minutes,
        id="scan_active_companies",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    scheduler.start()
    log.info("scheduler.started", interval_minutes=settings.scheduler_interval_minutes)
    return scheduler
