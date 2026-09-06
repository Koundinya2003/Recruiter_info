"""Scoring a posting against the search that found it.

The score answers one question: *does this posting match what the user asked
for?* It deliberately has no notion of a globally "good" job — a role is only
relevant relative to a request.

Anything scoring below ``settings.search_min_relevance`` is dropped rather than
shown, because the product's whole promise is a short list of real matches
instead of a long list padded with near-misses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.search.experience import ExperienceRange, parse_experience
from app.search.gazetteer import country_aliases, location_aliases
from app.search.query import JobQuery
from app.utils.text import basic_normalize, normalize_company_name, token_set

# Weights sum to 100. Title dominates: a Product Manager role in the wrong city
# is a near-miss the user can judge, whereas a Sales role in the right city is
# simply the wrong search result.
WEIGHTS = {
    "title": 45.0,
    "location": 20.0,
    "experience": 15.0,
    "keywords": 10.0,
    "company": 5.0,
    "industry": 5.0,
}

# A posting whose title shares almost nothing with the request is never shown,
# however well it scores elsewhere.
MIN_TITLE_SIMILARITY = 0.20

# Score a posting is capped to when it violates a constraint the user stated.
# Below every sane value of ``search_min_relevance``.
GATE_SCORE = 15.0


@dataclass
class RelevanceResult:
    score: float
    breakdown: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    experience: ExperienceRange = field(default_factory=ExperienceRange)

    @property
    def is_match(self) -> bool:
        from app.config import settings

        return self.score >= settings.search_min_relevance


def _title_score(query: JobQuery, title: str) -> tuple[float, str | None, float]:
    """Best match between the posting title and any requested title."""
    normalized = basic_normalize(title)
    if not normalized:
        return 0.0, None, 0.0
    title_tokens = token_set(normalized)

    best = 0.0
    best_reason: str | None = None
    for candidate in query.search_titles:
        wanted = basic_normalize(candidate)
        if not wanted:
            continue
        if wanted == normalized:
            return 1.0, f"Title is exactly “{candidate}”", 1.0
        if wanted in normalized:
            score = 0.9
        else:
            wanted_tokens = token_set(wanted)
            if not wanted_tokens:
                continue
            overlap = len(wanted_tokens & title_tokens) / len(wanted_tokens)
            # Jaccard on top, so "Senior Director of Product Management" does
            # not score as highly as "Product Manager" for a PM search.
            union = len(wanted_tokens | title_tokens) or 1
            score = 0.75 * overlap + 0.25 * (len(wanted_tokens & title_tokens) / union)
        if score > best:
            best = score
            best_reason = f"Title matches “{candidate}”"
    return best, best_reason, best


def _contains_alias(haystack: str, aliases: set[str]) -> bool:
    """Whole-word containment, so "Pune" does not match "Puneet"."""
    for alias in aliases:
        index = haystack.find(alias)
        while index != -1:
            before_ok = index == 0 or not haystack[index - 1].isalnum()
            after = index + len(alias)
            after_ok = after >= len(haystack) or not haystack[after].isalnum()
            if before_ok and after_ok:
                return True
            index = haystack.find(alias, index + 1)
    return False


def _location_score(query: JobQuery, location: str | None, is_remote: bool) -> tuple[float, str]:
    if query.remote_only:
        if is_remote or "remote" in (location or "").lower():
            return 1.0, "Remote, as requested"
        return 0.0, "Not a remote role"
    if not query.locations:
        return 1.0, "No location requested"
    normalized = basic_normalize(location)
    if not normalized:
        # Unknown is not a mismatch: the user gets to judge it.
        return 0.4, "Location not stated on the posting"

    # City match, through the gazetteer so "Bengaluru" satisfies "Bangalore".
    for wanted in query.locations:
        if _contains_alias(normalized, location_aliases(wanted)):
            return 1.0, f"Located in {wanted}"

    # Right country, unknown city — partial credit, and never a hard mismatch.
    for code in query.countries:
        if _contains_alias(normalized, country_aliases(code)):
            return 0.55, f"In the right country, but the posting says “{location}”"

    if is_remote or "remote" in normalized:
        return 0.7, "Remote — reachable from your locations"
    return 0.0, f"Located in {location}, not {', '.join(query.locations)}"


def _describe_posting_experience(posting: ExperienceRange) -> str:
    if posting.text:
        return posting.text
    if posting.level is not None:
        return posting.level.label.lower()
    return "an unstated amount of experience"


def _experience_score(query: JobQuery, posting: ExperienceRange) -> tuple[float, str]:
    wanted = query.experience
    if wanted.bounds is None:
        return 1.0, "No experience level requested"
    if not posting.is_stated and posting.level is None:
        # Unstated is not a mismatch — it is unknown, and the user decides.
        return 0.6, "Posting does not state an experience requirement"
    overlap = wanted.overlaps(posting)
    if overlap is None:
        return 0.6, "Experience requirement could not be compared"
    described = _describe_posting_experience(posting)
    if overlap:
        return 1.0, f"Experience requirement ({described}) fits"
    return 0.0, f"Wants {described}, outside the range you asked for"


def _keyword_score(query: JobQuery, blob: str) -> tuple[float, list[str]]:
    if not query.keywords:
        return 1.0, []
    hits = [k for k in query.keywords if basic_normalize(k) and basic_normalize(k) in blob]
    return (len(hits) / len(query.keywords)), hits


def _company_score(query: JobQuery, company: str) -> tuple[float, str | None]:
    if not query.companies:
        return 1.0, None
    normalized = normalize_company_name(company)
    for wanted in query.companies:
        if normalize_company_name(wanted) == normalized:
            return 1.0, f"At {company}, one of the companies you named"
    return 0.0, None


def _industry_score(query: JobQuery, blob: str) -> tuple[float, str | None]:
    if not query.industries:
        return 1.0, None
    for term in query.industry_keywords:
        if term and term in blob:
            return 1.0, f"Mentions {term}"
    return 0.0, None


def score_job(
    query: JobQuery,
    *,
    title: str,
    company_name: str,
    location: str | None,
    description: str | None,
    is_remote: bool = False,
) -> RelevanceResult:
    """Score one posting against ``query``."""
    blob = basic_normalize(f"{title} {company_name} {location or ''} {(description or '')[:4000]}")
    posting_experience = parse_experience(f"{title}. {description or ''}")

    title_score, title_reason, title_similarity = _title_score(query, title)
    location_score, location_reason = _location_score(query, location, is_remote)
    experience_score, experience_reason = _experience_score(query, posting_experience)
    keyword_score, keyword_hits = _keyword_score(query, blob)
    company_score, company_reason = _company_score(query, company_name)
    industry_score, industry_reason = _industry_score(query, blob)

    parts = {
        "title": title_score,
        "location": location_score,
        "experience": experience_score,
        "keywords": keyword_score,
        "company": company_score,
        "industry": industry_score,
    }
    total = sum(WEIGHTS[name] * value for name, value in parts.items())

    result = RelevanceResult(
        score=round(total, 1),
        breakdown={
            name: {
                "score": round(value * WEIGHTS[name], 1),
                "max": WEIGHTS[name],
                "ratio": round(value, 3),
            }
            for name, value in parts.items()
        },
        experience=posting_experience,
    )

    for text in (title_reason, company_reason, industry_reason):
        if text:
            result.reasons.append(text)
    if location_score >= 0.7 and location_reason:
        result.reasons.append(location_reason)
    else:
        result.mismatches.append(location_reason)
    if experience_score >= 1.0:
        result.reasons.append(experience_reason)
    elif experience_score <= 0.0:
        result.mismatches.append(experience_reason)
    else:
        result.mismatches.append(experience_reason)
    if keyword_hits:
        result.reasons.append("Mentions " + ", ".join(keyword_hits[:4]))
    elif query.keywords:
        result.mismatches.append("None of your keywords appear in the posting")

    for excluded in query.exclusions:
        key = basic_normalize(excluded)
        if key and key in blob:
            result.score = 0.0
            result.mismatches.append(f"Excluded by your search: mentions “{excluded}”")
            return result

    # --- Hard gates ---------------------------------------------------------
    # Weighted scoring alone lets a strong title carry a posting past a
    # constraint the user stated outright. These constraints are not
    # preferences to be traded off, so a clear violation caps the score below
    # the display threshold and says why. "Unknown" never trips a gate — only
    # a stated value that contradicts the request.
    if title_similarity < MIN_TITLE_SIMILARITY:
        result.score = min(result.score, GATE_SCORE)
        result.mismatches.append("Title does not resemble the role you asked for")

    if location_score <= 0.0:
        result.score = min(result.score, GATE_SCORE)

    if experience_score <= 0.0:
        result.score = min(result.score, GATE_SCORE)

    if query.companies and company_score <= 0.0:
        result.score = min(result.score, GATE_SCORE)
        result.mismatches.append(f"{company_name} is not one of the companies you named")

    return result
