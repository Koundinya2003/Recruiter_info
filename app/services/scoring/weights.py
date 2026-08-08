"""Default scoring weights and the loader that overlays user configuration.

The defaults reproduce the tables in the brief exactly. Anything stored in
`scoring_configs` overrides them per-key, so a user can rebalance from the
Settings page without touching code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.config import ScoringConfig

DEFAULT_JOB_WEIGHTS: dict[str, float] = {
    "role_match": 30,
    "industry_match": 20,
    "experience_match": 15,
    "skill_match": 15,
    "location_match": 10,
    "freshness": 10,
}

DEFAULT_HIRING_WEIGHTS: dict[str, float] = {
    "new_relevant_job": 25,
    "posted_within_24h": 25,
    "multiple_openings": 20,
    "recruiter_identified": 15,
    "career_page_activity": 10,
    "high_role_relevance": 5,
}

DEFAULT_RECRUITER_WEIGHTS: dict[str, float] = {
    "company_match": 25,
    "role_function_match": 25,
    "hiring_activity": 20,
    "email_confidence": 15,
    "professional_relevance": 10,
    "recency": 5,
}

DEFAULT_OUTREACH_WEIGHTS: dict[str, float] = {
    "job_relevance": 30,
    "hiring_freshness": 20,
    "recruiter_relevance": 20,
    "company_priority": 15,
    "email_confidence": 10,
    "recency": 5,
}

DEFAULT_OPTIONS: dict[str, Any] = {
    # Job freshness curve (hours).
    "freshness_full_hours": 24,
    "freshness_zero_hours": 720,
    # A job counts as a "hiring signal" while it is this new (hours).
    "new_job_window_hours": 72,
    "fresh_job_window_hours": 24,
    # Jobs at/above this relevance count towards "multiple relevant openings".
    "relevance_threshold": 60,
    "multiple_openings_target": 3,
    # Recruiter recency curve (days).
    "recruiter_recency_full_days": 7,
    "recruiter_recency_zero_days": 120,
    # Minimum relevance for a job to be eligible for the Contact Today list.
    "contact_today_min_job_relevance": 50,
    "contact_today_min_priority": 40,
}


@dataclass
class Weights:
    """Resolved weights for one user."""

    job: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_JOB_WEIGHTS))
    hiring: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_HIRING_WEIGHTS))
    recruiter: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_RECRUITER_WEIGHTS))
    outreach: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_OUTREACH_WEIGHTS))
    options: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_OPTIONS))

    def option(self, key: str) -> Any:
        return self.options.get(key, DEFAULT_OPTIONS.get(key))

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "hiring": self.hiring,
            "recruiter": self.recruiter,
            "outreach": self.outreach,
            "options": self.options,
        }


def _merge(defaults: dict[str, float], overrides: dict[str, Any] | None) -> dict[str, float]:
    merged = dict(defaults)
    for key, value in (overrides or {}).items():
        if key in merged:
            try:
                merged[key] = float(value)
            except (TypeError, ValueError):
                continue
    return merged


def default_weights() -> Weights:
    return Weights()


def load_weights(session: Session, user_id: int) -> Weights:
    """Load the active scoring config for ``user_id``, falling back to defaults."""
    config = session.scalar(
        select(ScoringConfig).where(
            ScoringConfig.user_id == user_id, ScoringConfig.is_active.is_(True)
        )
    )
    if config is None:
        return default_weights()

    options = dict(DEFAULT_OPTIONS)
    options.update(config.options or {})
    return Weights(
        job=_merge(DEFAULT_JOB_WEIGHTS, config.job_weights),
        hiring=_merge(DEFAULT_HIRING_WEIGHTS, config.hiring_weights),
        recruiter=_merge(DEFAULT_RECRUITER_WEIGHTS, config.recruiter_weights),
        outreach=_merge(DEFAULT_OUTREACH_WEIGHTS, config.outreach_weights),
        options=options,
    )


def ensure_scoring_config(session: Session, user_id: int) -> ScoringConfig:
    """Create the default scoring config row for a user if absent."""
    config = session.scalar(
        select(ScoringConfig).where(
            ScoringConfig.user_id == user_id, ScoringConfig.is_active.is_(True)
        )
    )
    if config is not None:
        return config
    config = ScoringConfig(
        user_id=user_id,
        name="default",
        is_active=True,
        job_weights=dict(DEFAULT_JOB_WEIGHTS),
        hiring_weights=dict(DEFAULT_HIRING_WEIGHTS),
        recruiter_weights=dict(DEFAULT_RECRUITER_WEIGHTS),
        outreach_weights=dict(DEFAULT_OUTREACH_WEIGHTS),
        options=dict(DEFAULT_OPTIONS),
    )
    session.add(config)
    session.flush()
    return config
