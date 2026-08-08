"""Hiring activity scoring (0-100) and hiring signal detection.

    New relevant job            +25
    Job posted <24 hours ago    +25
    Multiple relevant openings  +20
    Relevant recruiter identified +15
    Recent career-page activity +10
    High role relevance         +5

The score is derived from concrete, individually-listed signals which are also
persisted to `hiring_signals`, so the company page can show *why* a company
looks like it is hiring right now rather than just asserting a number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.company import Company
from app.models.enums import JobStatus, SignalType
from app.models.job import Job
from app.models.recruiter import Recruiter
from app.models.signal import HiringSignal
from app.services.scoring.base import ScoreResult, humanize_age
from app.services.scoring.weights import Weights, default_weights


@dataclass
class DetectedSignal:
    signal_type: SignalType
    points: float
    description: str
    job_id: int | None = None
    details: dict | None = None


@dataclass
class HiringActivityInput:
    """Facts about a company's recent hiring, gathered from the database."""

    relevant_jobs_total: int = 0
    new_relevant_jobs: int = 0          # discovered within the new-job window
    freshest_job_age_hours: float | None = None
    freshest_job_id: int | None = None
    freshest_job_title: str | None = None
    best_relevance: float = 0.0
    relevant_recruiters: int = 0
    top_recruiter_name: str | None = None
    career_page_activity_hours: float | None = None  # hours since last new posting


def gather_activity(
    session: Session, company: Company, weights: Weights | None = None
) -> HiringActivityInput:
    weights = weights or default_weights()
    threshold = float(weights.option("relevance_threshold"))
    new_window = float(weights.option("new_job_window_hours"))
    now = utcnow()

    jobs = list(
        session.scalars(
            select(Job).where(Job.company_id == company.id, Job.status == JobStatus.OPEN)
        ).all()
    )
    relevant = [j for j in jobs if j.relevance_score >= threshold]

    data = HiringActivityInput(
        relevant_jobs_total=len(relevant),
        best_relevance=max((j.relevance_score for j in relevant), default=0.0),
    )

    def age_hours(job: Job) -> float:
        reference = job.posted_at or job.discovered_at
        return max(0.0, (now - reference).total_seconds() / 3600.0)

    if relevant:
        freshest = min(relevant, key=age_hours)
        data.freshest_job_age_hours = age_hours(freshest)
        data.freshest_job_id = freshest.id
        data.freshest_job_title = freshest.title
        data.new_relevant_jobs = sum(
            1
            for j in relevant
            if (now - j.discovered_at).total_seconds() / 3600.0 <= new_window
        )

    if jobs:
        newest_discovery = max(j.discovered_at for j in jobs)
        data.career_page_activity_hours = max(
            0.0, (now - newest_discovery).total_seconds() / 3600.0
        )

    recruiters = list(
        session.scalars(
            select(Recruiter).where(
                Recruiter.company_id == company.id, Recruiter.do_not_contact.is_(False)
            )
        ).all()
    )
    relevant_recruiters = [r for r in recruiters if r.role_relevance >= 50]
    data.relevant_recruiters = len(relevant_recruiters)
    if relevant_recruiters:
        top = max(relevant_recruiters, key=lambda r: r.role_relevance)
        data.top_recruiter_name = top.name

    return data


def score_hiring_activity(
    data: HiringActivityInput, weights: Weights | None = None
) -> tuple[ScoreResult, list[DetectedSignal]]:
    weights = weights or default_weights()
    w = weights.hiring
    result = ScoreResult()
    signals: list[DetectedSignal] = []

    fresh_window = float(weights.option("fresh_job_window_hours"))
    new_window = float(weights.option("new_job_window_hours"))
    target_openings = max(1, int(weights.option("multiple_openings_target")))

    # --- New relevant job ----------------------------------------------------
    if data.new_relevant_jobs > 0:
        points = w["new_relevant_job"]
        description = (
            f"{data.new_relevant_jobs} new relevant opening(s) discovered in the last "
            f"{int(new_window)}h"
        )
        result.add("new_relevant_job", "New relevant job", points, w["new_relevant_job"], [description])
        signals.append(
            DetectedSignal(
                SignalType.NEW_JOB,
                points,
                description,
                job_id=data.freshest_job_id,
                details={"count": data.new_relevant_jobs},
            )
        )
    else:
        result.add(
            "new_relevant_job",
            "New relevant job",
            0,
            w["new_relevant_job"],
            ["No newly discovered relevant openings"],
        )

    # --- Posted within 24h ---------------------------------------------------
    age = data.freshest_job_age_hours
    if age is not None and age <= fresh_window:
        points = w["posted_within_24h"]
        description = f"Relevant role posted {humanize_age(age)}"
        result.add("posted_within_24h", "Posted recently", points, w["posted_within_24h"], [description])
        signals.append(
            DetectedSignal(
                SignalType.FRESH_JOB,
                points,
                description,
                job_id=data.freshest_job_id,
                details={"age_hours": round(age, 2)},
            )
        )
    elif age is not None:
        # Partial credit while the posting is still inside the wider window.
        factor = max(0.0, 1.0 - (age - fresh_window) / max(1.0, new_window - fresh_window)) * 0.6
        result.add(
            "posted_within_24h",
            "Posted recently",
            w["posted_within_24h"] * factor,
            w["posted_within_24h"],
            [f"Freshest relevant role is {humanize_age(age)}"],
            matched=factor > 0,
        )
    else:
        result.add(
            "posted_within_24h",
            "Posted recently",
            0,
            w["posted_within_24h"],
            ["No relevant openings found"],
        )

    # --- Multiple relevant openings -----------------------------------------
    if data.relevant_jobs_total >= 2:
        factor = min(1.0, data.relevant_jobs_total / target_openings)
        points = w["multiple_openings"] * factor
        description = f"{data.relevant_jobs_total} relevant openings are live at once"
        result.add("multiple_openings", "Multiple openings", points, w["multiple_openings"], [description])
        signals.append(
            DetectedSignal(
                SignalType.MULTIPLE_OPENINGS,
                points,
                description,
                details={"count": data.relevant_jobs_total},
            )
        )
    else:
        result.add(
            "multiple_openings",
            "Multiple openings",
            0,
            w["multiple_openings"],
            [f"Only {data.relevant_jobs_total} relevant opening(s)"],
        )

    # --- Relevant recruiter identified --------------------------------------
    if data.relevant_recruiters > 0:
        points = w["recruiter_identified"]
        who = data.top_recruiter_name or "a talent contact"
        description = f"Relevant recruiter identified: {who}"
        result.add(
            "recruiter_identified", "Recruiter identified", points, w["recruiter_identified"], [description]
        )
        signals.append(
            DetectedSignal(
                SignalType.RECRUITER_IDENTIFIED,
                points,
                description,
                details={"count": data.relevant_recruiters},
            )
        )
    else:
        result.add(
            "recruiter_identified",
            "Recruiter identified",
            0,
            w["recruiter_identified"],
            ["No relevant recruiter identified yet"],
        )

    # --- Recent career page activity ----------------------------------------
    activity = data.career_page_activity_hours
    if activity is not None and activity <= new_window:
        points = w["career_page_activity"]
        description = f"Career page produced a new posting {humanize_age(activity)}"
        result.add(
            "career_page_activity", "Career page activity", points, w["career_page_activity"], [description]
        )
        signals.append(
            DetectedSignal(SignalType.CAREER_PAGE_ACTIVITY, points, description)
        )
    else:
        result.add(
            "career_page_activity",
            "Career page activity",
            0,
            w["career_page_activity"],
            ["No new postings discovered recently"],
        )

    # --- High role relevance -------------------------------------------------
    if data.best_relevance >= 80:
        points = w["high_role_relevance"]
        description = f"Best matching role scores {data.best_relevance:.0f}/100 for your profile"
        result.add(
            "high_role_relevance", "High role relevance", points, w["high_role_relevance"], [description]
        )
        signals.append(
            DetectedSignal(
                SignalType.HIGH_RELEVANCE_ROLE,
                points,
                description,
                job_id=data.freshest_job_id,
            )
        )
    else:
        result.add(
            "high_role_relevance",
            "High role relevance",
            w["high_role_relevance"] * (data.best_relevance / 100.0),
            w["high_role_relevance"],
            [f"Best role relevance is {data.best_relevance:.0f}/100"],
            matched=data.best_relevance > 0,
        )

    return result, signals


def record_signals(
    session: Session,
    company: Company,
    signals: list[DetectedSignal],
    *,
    dedupe_window_hours: float = 12.0,
) -> int:
    """Persist detected signals, skipping ones already logged very recently."""
    cutoff = utcnow() - timedelta(hours=dedupe_window_hours)
    existing = set(
        session.execute(
            select(HiringSignal.signal_type, HiringSignal.job_id).where(
                HiringSignal.company_id == company.id, HiringSignal.detected_at >= cutoff
            )
        ).all()
    )
    created = 0
    for signal in signals:
        if (signal.signal_type, signal.job_id) in existing:
            continue
        session.add(
            HiringSignal(
                company_id=company.id,
                job_id=signal.job_id,
                signal_type=signal.signal_type,
                points=signal.points,
                description=signal.description,
                details=signal.details or {},
                is_demo=company.is_demo,
            )
        )
        created += 1
    session.flush()
    return created


def refresh_company_hiring_activity(
    session: Session,
    company: Company,
    weights: Weights | None = None,
    *,
    persist_signals: bool = True,
) -> tuple[ScoreResult, list[DetectedSignal]]:
    """Recompute and store a company's hiring activity score."""
    data = gather_activity(session, company, weights)
    result, signals = score_hiring_activity(data, weights)
    company.hiring_activity_score = result.total
    company.hiring_activity_computed_at = utcnow()
    if persist_signals and signals:
        record_signals(session, company, signals)
    return result, signals


def timeline(session: Session, company_id: int, days: int = 30) -> list[dict]:
    """Jobs-appeared-per-day series for the company hiring timeline chart."""
    since: datetime = utcnow() - timedelta(days=days)
    rows = session.execute(
        select(Job.discovered_at, Job.title, Job.relevance_score, Job.id)
        .where(Job.company_id == company_id, Job.discovered_at >= since)
        .order_by(Job.discovered_at.desc())
    ).all()
    return [
        {
            "date": discovered.date().isoformat(),
            "discovered_at": discovered.isoformat(),
            "title": title,
            "relevance_score": score,
            "job_id": job_id,
        }
        for discovered, title, score, job_id in rows
    ]
