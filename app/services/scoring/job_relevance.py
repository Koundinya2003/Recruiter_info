"""Job relevance scoring (0-100).

    Role match          0-30
    Industry match      0-20
    Experience match    0-15
    Skill match         0-15
    Location preference 0-10
    Freshness           0-10

Every component records why it scored what it did. A job whose title matches an
EXCLUDE taxonomy term is excluded outright with a stated reason rather than
being quietly buried by a low score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.db.base import utcnow
from app.models.job import Job
from app.services.scoring.base import ScoreResult, decay, humanize_age
from app.services.scoring.context import ProfileContext
from app.services.scoring.weights import Weights, default_weights
from app.utils.text import normalize_location, seniority_level, truncate


@dataclass
class JobScoreInput:
    """Scoring input, decoupled from the ORM so unsaved candidates score too."""

    title: str
    description: str | None = None
    location: str | None = None
    employment_type: str | None = None
    company_name: str | None = None
    company_industry: str | None = None
    posted_at: datetime | None = None
    discovered_at: datetime | None = None

    @property
    def age_hours(self) -> float | None:
        reference = self.posted_at or self.discovered_at
        if reference is None:
            return None
        return max(0.0, (utcnow() - reference).total_seconds() / 3600.0)

    @classmethod
    def from_job(cls, job: Job, *, industry: str | None = None) -> JobScoreInput:
        return cls(
            title=job.title,
            description=job.description,
            location=job.location,
            employment_type=job.employment_type,
            company_name=job.company.company_name if job.company else None,
            company_industry=industry
            if industry is not None
            else (job.company.industry if job.company else None),
            posted_at=job.posted_at,
            discovered_at=job.discovered_at,
        )


def score_job(
    job: JobScoreInput,
    context: ProfileContext,
    weights: Weights | None = None,
) -> ScoreResult:
    weights = weights or default_weights()
    w = weights.job
    result = ScoreResult()

    # --- Hard exclusion ------------------------------------------------------
    exclusion = context.match_exclusion(job.title)
    if exclusion.matched and exclusion.term is not None:
        result.excluded = True
        result.exclusion_reason = (
            f"Title matches your excluded term '{exclusion.term.term}'"
        )

    # --- Role match (0-30) ---------------------------------------------------
    role = context.match_role(job.title)
    if role.matched and role.term is not None:
        points = w["role_match"] * min(1.0, role.strength)
        label = {
            "exact": f"Matches your target role '{role.term.term}'",
            "alias": f"Matches your target role '{role.term.term}' (via a known alias)",
            "partial": f"Partially matches your target role '{role.term.term}'",
        }[role.kind]
        result.add("role_match", "Role match", points, w["role_match"], [label])
    else:
        result.add(
            "role_match",
            "Role match",
            0,
            w["role_match"],
            [f"'{truncate(job.title, 60)}' does not match any target role in your taxonomy"],
        )

    # --- Industry match (0-20) ----------------------------------------------
    industry = context.match_industry(job.company_industry, job.description, job.company_name)
    if industry.matched and industry.term is not None:
        points = w["industry_match"] * min(1.0, industry.strength)
        result.add(
            "industry_match",
            "Industry match",
            points,
            w["industry_match"],
            [f"{industry.term.term} company — one of your preferred domains"],
        )
    else:
        result.add(
            "industry_match",
            "Industry match",
            0,
            w["industry_match"],
            ["Industry is not one of your preferred domains (or is not stated)"],
        )

    # --- Experience / seniority match (0-15) --------------------------------
    job_level = seniority_level(job.title)
    desired = context.desired_seniority
    if job_level is None:
        result.add(
            "experience_match",
            "Experience match",
            w["experience_match"] * 0.5,
            w["experience_match"],
            ["Seniority is not stated in the title — treated as neutral"],
            matched=True,
        )
    elif desired is None:
        result.add(
            "experience_match",
            "Experience match",
            w["experience_match"] * 0.5,
            w["experience_match"],
            ["Your years of experience are not set — add them in Profile for a sharper score"],
            matched=True,
        )
    else:
        distance = abs(job_level - desired)
        factor = max(0.0, 1.0 - distance * 0.34)
        if distance == 0:
            reason = "Seniority lines up with your experience level"
        elif job_level > desired:
            reason = f"Role is {distance} level(s) more senior than your experience"
        else:
            reason = f"Role is {distance} level(s) more junior than your experience"
        result.add(
            "experience_match",
            "Experience match",
            w["experience_match"] * factor,
            w["experience_match"],
            [reason],
            matched=factor > 0,
        )

    # --- Skill match (0-15) --------------------------------------------------
    haystack = " ".join(filter(None, [job.title, job.description]))
    skill_hits = context.matching_skills(haystack)
    if skill_hits:
        # Three strong skills is treated as a full match; more is not "more matched".
        weight_sum = sum(weight for _, weight in skill_hits)
        factor = min(1.0, weight_sum / 3.0)
        names = ", ".join(term.term for term, _ in skill_hits[:4])
        result.add(
            "skill_match",
            "Skill match",
            w["skill_match"] * factor,
            w["skill_match"],
            [f"Mentions skills from your profile: {names}"],
        )
    elif not job.description:
        result.add(
            "skill_match",
            "Skill match",
            0,
            w["skill_match"],
            ["No job description available to match skills against"],
        )
    else:
        result.add(
            "skill_match",
            "Skill match",
            0,
            w["skill_match"],
            ["Description does not mention skills from your profile"],
        )

    # --- Location preference (0-10) -----------------------------------------
    location = context.match_location(job.location)
    if location.matched and location.term is not None:
        result.add(
            "location_match",
            "Location preference",
            w["location_match"] * min(1.0, location.strength),
            w["location_match"],
            [f"Location matches your preference: {location.term.term}"],
        )
    elif not job.location:
        result.add(
            "location_match",
            "Location preference",
            w["location_match"] * 0.4,
            w["location_match"],
            ["Location is not stated — treated as partially acceptable"],
            matched=True,
        )
    else:
        result.add(
            "location_match",
            "Location preference",
            0,
            w["location_match"],
            [f"{normalize_location(job.location) or job.location} is not a preferred location"],
        )

    # --- Freshness (0-10) ----------------------------------------------------
    age = job.age_hours
    factor = decay(
        age,
        float(weights.option("freshness_full_hours")),
        float(weights.option("freshness_zero_hours")),
    )
    if age is None:
        result.add(
            "freshness", "Freshness", 0, w["freshness"], ["No posting date available"]
        )
    else:
        source = "Posted" if job.posted_at else "First seen"
        result.add(
            "freshness",
            "Freshness",
            w["freshness"] * factor,
            w["freshness"],
            [f"{source} {humanize_age(age)}"],
            matched=factor > 0,
        )

    return result


def score_job_model(
    job: Job, context: ProfileContext, weights: Weights | None = None
) -> ScoreResult:
    """Convenience wrapper for a persisted :class:`Job`."""
    return score_job(JobScoreInput.from_job(job), context, weights)


def apply_job_score(job: Job, context: ProfileContext, weights: Weights | None = None) -> ScoreResult:
    """Score a job and write the result onto the model."""
    result = score_job_model(job, context, weights)
    job.relevance_score = result.total
    job.relevance_breakdown = result.to_dict()
    job.relevance_scored_at = utcnow()
    return result
