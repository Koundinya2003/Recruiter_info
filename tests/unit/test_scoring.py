"""Relevance, hiring-activity, recruiter and outreach-priority scoring."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.db.base import utcnow
from app.models.enums import (
    CompanyPriority,
    EmailConfidence,
    TaxonomyKind,
    VerificationStatus,
    priority_band,
)
from app.services.scoring.base import ScoreResult, decay
from app.services.scoring.context import DEFAULT_TAXONOMY, MatchTerm, ProfileContext
from app.services.scoring.hiring_activity import HiringActivityInput, score_hiring_activity
from app.services.scoring.job_relevance import JobScoreInput, score_job
from app.services.scoring.outreach_priority import score_opportunity
from app.services.scoring.recruiter_relevance import score_recruiter, score_role_relevance
from app.services.scoring.weights import default_weights


def _terms(kind: TaxonomyKind) -> list[MatchTerm]:
    return [MatchTerm(t, list(a), w, p) for t, a, w, p in DEFAULT_TAXONOMY[kind]]


@pytest.fixture
def context() -> ProfileContext:
    return ProfileContext(
        user_id=1,
        roles=_terms(TaxonomyKind.ROLE),
        industries=_terms(TaxonomyKind.INDUSTRY),
        skills=_terms(TaxonomyKind.SKILL),
        locations=_terms(TaxonomyKind.LOCATION),
        excludes=_terms(TaxonomyKind.EXCLUDE),
        years_experience=2.0,
        desired_seniority=1,
    )


# --- Job relevance --------------------------------------------------------------


def test_perfect_job_scores_high_with_reasons(context: ProfileContext) -> None:
    result = score_job(
        JobScoreInput(
            title="Product Analyst",
            description="Own product analytics, run A/B testing, write SQL, build dashboards.",
            location="Bangalore, India",
            company_industry="Fintech",
            posted_at=utcnow() - timedelta(hours=6),
        ),
        context,
    )
    assert result.total >= 95
    assert len(result.reasons) >= 5
    # The score must never be a bare number: every component explains itself.
    assert all(c.reasons for c in result.components)


def test_irrelevant_job_scores_low(context: ProfileContext) -> None:
    result = score_job(
        JobScoreInput(
            title="Warehouse Supervisor",
            description="Manage forklift operations.",
            location="Leipzig",
            company_industry="Logistics",
            posted_at=utcnow() - timedelta(days=45),
        ),
        context,
    )
    assert result.total < 30
    assert result.gaps


def test_excluded_title_is_excluded_with_a_stated_reason(context: ProfileContext) -> None:
    result = score_job(JobScoreInput(title="Sales Development Representative"), context)
    assert result.excluded is True
    assert result.total == 0.0
    assert "Sales" in (result.exclusion_reason or "")


def test_freshness_decays(context: ProfileContext) -> None:
    def score(hours: float) -> float:
        return score_job(
            JobScoreInput(title="Product Analyst", posted_at=utcnow() - timedelta(hours=hours)),
            context,
        ).total

    assert score(2) > score(200) > score(2000)


def test_missing_posted_date_scores_no_freshness(context: ProfileContext) -> None:
    result = score_job(JobScoreInput(title="Product Analyst"), context)
    freshness = next(c for c in result.components if c.key == "freshness")
    assert freshness.points == 0
    assert "No posting date" in freshness.reasons[0]


def test_weights_are_configurable(context: ProfileContext) -> None:
    job = JobScoreInput(title="Product Analyst", company_industry="Fintech")
    weights = default_weights()
    baseline = score_job(job, context, weights).total

    weights.job["role_match"] = 90.0
    weights.job["industry_match"] = 5.0
    changed = score_job(job, context, weights).total
    assert changed != baseline


def test_score_components_never_exceed_their_maximum(context: ProfileContext) -> None:
    result = score_job(
        JobScoreInput(
            title="Product Analyst",
            description="SQL " * 500,
            location="Remote",
            company_industry="Fintech",
            posted_at=utcnow(),
        ),
        context,
    )
    for component in result.components:
        assert component.points <= component.max_points
    assert result.total <= 100


# --- Hiring activity ------------------------------------------------------------


def test_hiring_activity_full_signal_set() -> None:
    result, signals = score_hiring_activity(
        HiringActivityInput(
            relevant_jobs_total=3,
            new_relevant_jobs=2,
            freshest_job_age_hours=5,
            best_relevance=92,
            relevant_recruiters=1,
            top_recruiter_name="Casey",
            career_page_activity_hours=6,
        )
    )
    assert result.total >= 95
    kinds = {s.signal_type.value for s in signals}
    assert {"NEW_JOB", "FRESH_JOB", "MULTIPLE_OPENINGS", "RECRUITER_IDENTIFIED"} <= kinds


def test_hiring_activity_quiet_company_scores_zero() -> None:
    result, signals = score_hiring_activity(HiringActivityInput())
    assert result.total == 0.0
    assert signals == []
    assert result.gaps


def test_hiring_activity_partial_credit_for_stale_posting() -> None:
    result, _ = score_hiring_activity(
        HiringActivityInput(relevant_jobs_total=1, freshest_job_age_hours=48, best_relevance=70)
    )
    assert 0 < result.total < 60


# --- Recruiter relevance --------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "minimum"),
    [
        ("Talent Acquisition Partner, Product", 90),
        ("Technical Recruiter", 85),
        ("Recruiter", 70),
        ("Head of People", 70),
    ],
)
def test_role_relevance_recognises_talent_titles(title: str, minimum: int) -> None:
    score, reasons = score_role_relevance(title)
    assert score >= minimum
    assert reasons


@pytest.mark.parametrize("title", ["Backend Engineer", "Account Executive", None, ""])
def test_role_relevance_rejects_non_talent_titles(title: str | None) -> None:
    score, reasons = score_role_relevance(title)
    assert score == 0.0
    assert reasons


def _recruiter(**overrides: object) -> SimpleNamespace:
    base = {
        "name": "Casey Talent",
        "company_name": "Testly",
        "title": "Talent Acquisition Partner, Product",
        "public_professional_email": "casey@testly.example",
        "email_confidence": EmailConfidence.HIGH,
        "email_verified": True,
        "email_verification_status": VerificationStatus.VALID,
        "email_source_type": None,
        "professional_profile_url": "https://testly.example/team/casey",
        "do_not_contact": False,
        "last_seen": utcnow(),
        "relevance_score": 0.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _company(**overrides: object) -> SimpleNamespace:
    base = {
        "company_name": "Testly",
        "hiring_activity_score": 90.0,
        "priority": CompanyPriority.HIGH,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_recruiter_with_published_email_beats_inferred(context: ProfileContext) -> None:
    published = score_recruiter(_recruiter(), _company(), context)  # type: ignore[arg-type]
    inferred = score_recruiter(
        _recruiter(
            email_confidence=EmailConfidence.LOW,
            email_verified=False,
            email_verification_status=VerificationStatus.NOT_CHECKED,
        ),
        _company(),  # type: ignore[arg-type]
        context,
    )
    assert published.total > inferred.total
    assert any("INFERRED" in reason for reason in inferred.reasons)


def test_do_not_contact_recruiter_is_excluded(context: ProfileContext) -> None:
    result = score_recruiter(_recruiter(do_not_contact=True), _company(), context)  # type: ignore[arg-type]
    assert result.excluded is True
    assert result.total == 0.0


def test_recruiter_without_email_loses_confidence_points(context: ProfileContext) -> None:
    result = score_recruiter(
        _recruiter(public_professional_email=None, email_confidence=EmailConfidence.NONE),
        _company(),  # type: ignore[arg-type]
        context,
    )
    component = next(c for c in result.components if c.key == "email_confidence")
    assert component.points == 0


# --- Outreach priority ----------------------------------------------------------


def _job(**overrides: object) -> SimpleNamespace:
    base = {
        "title": "Product Analyst",
        "relevance_score": 95.0,
        "age_hours": 5.0,
        "relevance_breakdown": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_outreach_priority_prefers_fresh_relevant_verified() -> None:
    strong = score_opportunity(_company(), _job(), _recruiter(relevance_score=95.0))  # type: ignore[arg-type]
    weak = score_opportunity(
        _company(priority=CompanyPriority.LOW, hiring_activity_score=10.0),  # type: ignore[arg-type]
        _job(relevance_score=55.0, age_hours=600.0),
        _recruiter(
            relevance_score=40.0,
            email_confidence=EmailConfidence.LOW,
            email_verified=False,
            email_verification_status=VerificationStatus.NOT_CHECKED,
        ),
    )
    assert strong.total > weak.total
    assert strong.total >= 85


def test_outreach_priority_excludes_do_not_contact() -> None:
    result = score_opportunity(_company(), _job(), _recruiter(do_not_contact=True))  # type: ignore[arg-type]
    assert result.excluded is True
    assert result.total == 0.0


@pytest.mark.parametrize(
    ("score", "band"),
    [
        (94, "CONTACT NOW"),
        (87, "HIGH PRIORITY"),
        (76, "GOOD OPPORTUNITY"),
        (61, "REVIEW"),
        (42, "LOW PRIORITY"),
    ],
)
def test_priority_bands(score: float, band: str) -> None:
    assert priority_band(score) == band


# --- Score primitives -----------------------------------------------------------


def test_decay_curve() -> None:
    assert decay(0, 24, 720) == 1.0
    assert decay(24, 24, 720) == 1.0
    assert decay(720, 24, 720) == 0.0
    assert 0 < decay(300, 24, 720) < 1
    assert decay(None, 24, 720) == 0.0


def test_score_result_clamps_and_explains() -> None:
    result = ScoreResult()
    result.add("a", "A", 999, 30, ["over the max"])
    result.add("b", "B", -5, 20, ["under zero"])
    assert result.components[0].points == 30
    assert result.components[1].points == 0
    assert result.total == 60.0
    assert "over the max" in result.reasons
    assert "under zero" in result.gaps
