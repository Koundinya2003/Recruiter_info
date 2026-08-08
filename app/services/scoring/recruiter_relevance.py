"""Recruiter relevance scoring (0-100).

    Company match           25
    Role/function match     25
    Hiring activity         20
    Email confidence        15
    Professional relevance  10
    Recency                  5

Email confidence deliberately rewards *provenance*, not merely the presence of
an address: a pattern-inferred address scores a fraction of a published one and
is always labelled as inferred.
"""

from __future__ import annotations

from app.db.base import utcnow
from app.models.company import Company
from app.models.enums import EmailConfidence, SourceType, VerificationStatus
from app.models.recruiter import Recruiter
from app.services.scoring.base import ScoreResult, clamp, decay, humanize_age
from app.services.scoring.context import ProfileContext
from app.services.scoring.weights import Weights, default_weights
from app.utils.text import basic_normalize, contains_term

# Titles that indicate this person works on hiring at all.
TALENT_FUNCTION_TERMS = (
    "talent acquisition",
    "recruiter",
    "recruiting",
    "recruitment",
    "talent partner",
    "talent",
    "people operations",
    "people partner",
    "head of people",
    "people lead",
    "chief people officer",
    "hiring manager",
    "sourcer",
    "hr business partner",
    "human resources",
)

# Titles that indicate they hire for *the kind of role the user wants*.
FUNCTION_ALIGNMENT_TERMS = (
    "product",
    "technical",
    "tech",
    "engineering",
    "analytics",
    "data",
    "business",
    "growth",
    "gtm",
)

SENIOR_TALENT_TERMS = ("lead", "senior", "head", "manager", "director", "principal")


def score_role_relevance(title: str | None) -> tuple[float, list[str]]:
    """0-100 for how relevant a person's title is to hiring for target roles."""
    if not title:
        return 0.0, ["No job title recorded for this contact"]

    reasons: list[str] = []
    score = 0.0

    talent_hit = next((t for t in TALENT_FUNCTION_TERMS if contains_term(title, t)), None)
    if talent_hit:
        score += 60.0
        reasons.append(f"Works in a talent/hiring function ({talent_hit})")
    else:
        return 0.0, [f"'{title}' is not a recognised talent or hiring role"]

    function_hit = next((t for t in FUNCTION_ALIGNMENT_TERMS if contains_term(title, t)), None)
    if function_hit:
        score += 30.0
        reasons.append(f"Focused on {function_hit} hiring, which covers your target roles")
    else:
        score += 12.0
        reasons.append("General talent role — not specialised to your function")

    if any(contains_term(title, t) for t in SENIOR_TALENT_TERMS):
        score += 10.0
        reasons.append("Senior enough to own a requisition")

    return min(100.0, score), reasons


def score_recruiter(
    recruiter: Recruiter,
    company: Company,
    context: ProfileContext,
    weights: Weights | None = None,
    *,
    has_relevant_job: bool = False,
    freshest_job_age_hours: float | None = None,
    freshest_job_title: str | None = None,
) -> ScoreResult:
    weights = weights or default_weights()
    w = weights.recruiter
    result = ScoreResult()

    if recruiter.do_not_contact:
        result.excluded = True
        result.exclusion_reason = "Marked DO NOT CONTACT — excluded from all recommendations"

    # --- Company match (25) --------------------------------------------------
    same_company = basic_normalize(recruiter.company_name) == basic_normalize(company.company_name)
    if same_company:
        result.add(
            "company_match",
            "Company match",
            w["company_match"],
            w["company_match"],
            [f"Works at {company.company_name}, a company you are tracking"],
        )
    else:
        result.add(
            "company_match",
            "Company match",
            w["company_match"] * 0.4,
            w["company_match"],
            [f"Listed under {recruiter.company_name}, which differs from {company.company_name}"],
            matched=True,
        )

    # --- Role / function match (25) -----------------------------------------
    role_score, role_reasons = score_role_relevance(recruiter.title)
    result.add(
        "role_function_match",
        "Role / function match",
        w["role_function_match"] * (role_score / 100.0),
        w["role_function_match"],
        role_reasons,
        matched=role_score > 0,
    )

    # --- Hiring activity (20) ------------------------------------------------
    activity = clamp(company.hiring_activity_score / 100.0)
    activity_reasons: list[str] = []
    if has_relevant_job and freshest_job_age_hours is not None:
        title = freshest_job_title or "a relevant role"
        activity_reasons.append(f"{title} posted {humanize_age(freshest_job_age_hours)}")
    if company.hiring_activity_score > 0:
        activity_reasons.append(
            f"Company hiring activity score is {company.hiring_activity_score:.0f}/100"
        )
    if not activity_reasons:
        activity_reasons.append("No current hiring activity detected at this company")
    result.add(
        "hiring_activity",
        "Hiring activity",
        w["hiring_activity"] * activity,
        w["hiring_activity"],
        activity_reasons,
        matched=activity > 0,
    )

    # --- Email confidence (15) ----------------------------------------------
    confidence = recruiter.email_confidence
    if confidence is EmailConfidence.NONE or not recruiter.public_professional_email:
        result.add(
            "email_confidence",
            "Email confidence",
            0,
            w["email_confidence"],
            ["No public professional email found for this person"],
        )
    else:
        factor = confidence.score / 100.0
        reasons = []
        if confidence is EmailConfidence.HIGH:
            reasons.append("Professional email is published and attributed to this person")
        elif confidence is EmailConfidence.MEDIUM:
            reasons.append(
                "Email published by the company but not directly attributed to this person"
            )
        else:
            reasons.append("Email is PATTERN-INFERRED, not published — treat as unverified")

        if recruiter.email_verified and recruiter.email_verification_status is VerificationStatus.VALID:
            factor = min(1.0, factor + 0.15)
            reasons.append("Address passed verification")
        elif recruiter.email_verification_status is VerificationStatus.INVALID:
            factor *= 0.2
            reasons.append("Verification reported this address as invalid")
        elif recruiter.email_verification_status is VerificationStatus.RISKY:
            factor *= 0.6
            reasons.append("Verification flagged this address as risky")
        elif recruiter.email_verification_status is VerificationStatus.NOT_CHECKED:
            reasons.append("Not verified yet")

        result.add(
            "email_confidence",
            "Email confidence",
            w["email_confidence"] * factor,
            w["email_confidence"],
            reasons,
        )

    # --- Professional relevance (10) ----------------------------------------
    prof_points = 0.0
    prof_reasons: list[str] = []
    if recruiter.professional_profile_url:
        prof_points += 0.5
        prof_reasons.append("Public professional profile available")
    if recruiter.email_source_type and recruiter.email_source_type not in {
        SourceType.PATTERN_INFERENCE,
        SourceType.DEMO_SEED,
    }:
        prof_points += 0.3
        prof_reasons.append(
            f"Contact detail traced to a public source ({recruiter.email_source_type.value})"
        )
    role_outcome = context.match_role(freshest_job_title) if freshest_job_title else None
    if role_outcome and role_outcome.matched and role_outcome.term is not None:
        prof_points += 0.2
        prof_reasons.append(f"Associated with hiring for {role_outcome.term.term}")
    if not prof_reasons:
        prof_reasons.append("Limited public professional information")
    result.add(
        "professional_relevance",
        "Professional relevance",
        w["professional_relevance"] * clamp(prof_points),
        w["professional_relevance"],
        prof_reasons,
        matched=prof_points > 0,
    )

    # --- Recency (5) ---------------------------------------------------------
    age_days = (utcnow() - recruiter.last_seen).total_seconds() / 86400.0
    factor = decay(
        age_days,
        float(weights.option("recruiter_recency_full_days")),
        float(weights.option("recruiter_recency_zero_days")),
    )
    result.add(
        "recency",
        "Recency",
        w["recency"] * factor,
        w["recency"],
        [f"Contact details last confirmed {humanize_age(age_days * 24)}"],
        matched=factor > 0,
    )

    return result


def apply_recruiter_score(
    recruiter: Recruiter,
    company: Company,
    context: ProfileContext,
    weights: Weights | None = None,
    **kwargs: object,
) -> ScoreResult:
    """Score a recruiter and write the result onto the model."""
    role_score, _ = score_role_relevance(recruiter.title)
    recruiter.role_relevance = role_score
    result = score_recruiter(recruiter, company, context, weights, **kwargs)  # type: ignore[arg-type]
    recruiter.relevance_score = result.total
    recruiter.relevance_breakdown = result.to_dict()
    recruiter.relevance_scored_at = utcnow()
    return result
