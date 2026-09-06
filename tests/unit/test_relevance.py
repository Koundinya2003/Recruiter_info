"""Scoring a posting against the request that found it."""

from __future__ import annotations

from app.config import settings
from app.search.parser import parse_query_rules
from app.search.relevance import GATE_SCORE, score_job

APM_QUERY = parse_query_rules(
    "Find Associate Product Manager roles for 0-2 years of experience "
    "in Bangalore and Hyderabad."
)


def score(title: str, location: str | None, description: str = "", company: str = "Acme"):
    return score_job(
        APM_QUERY,
        title=title,
        company_name=company,
        location=location,
        description=description,
    )


class TestMatching:
    def test_exact_match_scores_top(self) -> None:
        result = score("Associate Product Manager", "Bangalore, India", "0-2 years of experience")
        assert result.score >= 95
        assert result.is_match

    def test_city_alias_counts_as_the_same_place(self) -> None:
        result = score("Associate Product Manager", "Bengaluru", "0-2 years")
        assert result.is_match

    def test_related_title_still_matches(self) -> None:
        result = score("Product Manager I", "Hyderabad", "Entry level role")
        assert result.is_match

    def test_missing_location_is_unknown_not_rejected(self) -> None:
        """The user judges a posting that does not say where it is."""
        result = score("Associate Product Manager", None, "0-2 years")
        assert result.is_match

    def test_missing_experience_is_unknown_not_rejected(self) -> None:
        result = score("Associate Product Manager", "Bangalore", "No requirement stated here.")
        assert result.is_match


class TestHardGates:
    """A constraint the user stated outright is not a weight to trade off."""

    def test_wrong_experience_is_gated_out(self) -> None:
        result = score("Senior Product Manager", "Bangalore", "8+ years of experience required")
        assert result.score <= GATE_SCORE
        assert not result.is_match
        assert any("8+ years" in m for m in result.mismatches)

    def test_wrong_city_is_gated_out(self) -> None:
        result = score("Associate Product Manager", "Berlin, Germany", "0-2 years")
        assert result.score <= GATE_SCORE
        assert not result.is_match

    def test_unrelated_title_is_gated_out(self) -> None:
        result = score("Sales Development Representative", "Bangalore", "0-2 years")
        assert result.score <= GATE_SCORE
        assert not result.is_match

    def test_exclusions_zero_the_score(self) -> None:
        query = parse_query_rules("product manager in Bangalore, not agency")
        result = score_job(
            query,
            title="Product Manager",
            company_name="Acme",
            location="Bangalore",
            description="A fast-growing agency looking for a PM.",
        )
        assert result.score == 0.0

    def test_named_company_is_required_when_the_user_names_one(self) -> None:
        query = parse_query_rules("Associate Product Manager at Razorpay in Bangalore")
        wrong = score_job(
            query,
            title="Associate Product Manager",
            company_name="Somebody Else",
            location="Bangalore",
            description="",
        )
        right = score_job(
            query,
            title="Associate Product Manager",
            company_name="Razorpay",
            location="Bangalore",
            description="",
        )
        assert not wrong.is_match
        assert right.is_match


class TestCountryFallback:
    def test_country_match_is_partial_credit_not_a_rejection(self) -> None:
        result = score("Associate Product Manager", "India", "0-2 years")
        assert result.is_match
        assert result.score < 100


class TestRemote:
    def test_remote_request_rejects_onsite_roles(self) -> None:
        query = parse_query_rules("remote product manager roles")
        onsite = score_job(
            query,
            title="Product Manager",
            company_name="Acme",
            location="Chennai",
            description="",
            is_remote=False,
        )
        remote = score_job(
            query,
            title="Product Manager",
            company_name="Acme",
            location="Anywhere",
            description="",
            is_remote=True,
        )
        assert not onsite.is_match
        assert remote.is_match


def test_threshold_comes_from_settings() -> None:
    assert 0 < settings.search_min_relevance < 100
    assert settings.search_min_relevance > GATE_SCORE
