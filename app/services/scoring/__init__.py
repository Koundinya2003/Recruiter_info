"""Explainable scoring engines."""

from app.services.scoring.base import ScoreComponent, ScoreResult
from app.services.scoring.context import ProfileContext, build_context, seed_default_taxonomy
from app.services.scoring.hiring_activity import (
    refresh_company_hiring_activity,
    score_hiring_activity,
)
from app.services.scoring.job_relevance import JobScoreInput, apply_job_score, score_job
from app.services.scoring.outreach_priority import (
    ContactTodayFilters,
    Opportunity,
    contact_today,
    score_opportunity,
)
from app.services.scoring.recruiter_relevance import apply_recruiter_score, score_recruiter
from app.services.scoring.weights import Weights, default_weights, load_weights

__all__ = [
    "ContactTodayFilters",
    "JobScoreInput",
    "Opportunity",
    "ProfileContext",
    "ScoreComponent",
    "ScoreResult",
    "Weights",
    "apply_job_score",
    "apply_recruiter_score",
    "build_context",
    "contact_today",
    "default_weights",
    "load_weights",
    "refresh_company_hiring_activity",
    "score_hiring_activity",
    "score_job",
    "score_opportunity",
    "score_recruiter",
    "seed_default_taxonomy",
]
