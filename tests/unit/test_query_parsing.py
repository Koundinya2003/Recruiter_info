"""Reading a free-text search request."""

from __future__ import annotations

import pytest

from app.models.enums import ExperienceLevel
from app.search.experience import ExperienceRange, describe, parse_experience
from app.search.gazetteer import country_for, expand_title, resolve_location
from app.search.parser import parse_query_rules
from app.search.query import JobQuery


class TestExperienceParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("0-2 years of experience", (0.0, 2.0)),
            ("0–2 years", (0.0, 2.0)),
            ("1 to 3 yrs", (1.0, 3.0)),
            ("up to 4 years", (0.0, 4.0)),
            ("3+ years", (3.0, None)),
            ("at least 5 years", (5.0, None)),
            ("minimum of 2 years experience", (2.0, None)),
        ],
    )
    def test_numeric_ranges(self, text: str, expected: tuple) -> None:
        parsed = parse_experience(text)
        assert (parsed.min_years, parsed.max_years) == expected

    @pytest.mark.parametrize(
        ("text", "level"),
        [
            ("we are hiring freshers", ExperienceLevel.ENTRY),
            ("entry-level analyst", ExperienceLevel.ENTRY),
            ("new grad programme", ExperienceLevel.ENTRY),
            ("Senior Product Manager", ExperienceLevel.SENIOR),
            ("Summer internship", ExperienceLevel.INTERNSHIP),
            ("Director of Product", ExperienceLevel.EXECUTIVE),
        ],
    )
    def test_level_phrases(self, text: str, level: ExperienceLevel) -> None:
        assert parse_experience(text).level is level

    def test_unstated_stays_unstated(self) -> None:
        """A posting that says nothing must not acquire a default requirement."""
        parsed = parse_experience("Great team, great mission, apply now.")
        assert parsed.min_years is None
        assert parsed.max_years is None
        assert parsed.level is None
        assert describe(parsed) == "Not stated"

    def test_reversed_range_is_corrected(self) -> None:
        parsed = parse_experience("5-2 years")
        assert (parsed.min_years, parsed.max_years) == (2.0, 5.0)

    def test_overlap_is_unknown_when_either_side_is_silent(self) -> None:
        wanted = ExperienceRange(min_years=0.0, max_years=2.0)
        assert wanted.overlaps(ExperienceRange()) is None
        assert wanted.overlaps(ExperienceRange(min_years=1.0, max_years=3.0)) is True
        assert wanted.overlaps(ExperienceRange(min_years=8.0)) is False


class TestGazetteer:
    def test_city_aliases_resolve_to_one_name(self) -> None:
        assert resolve_location("Bengaluru") == ("Bangalore", "in")
        assert resolve_location("bangalore") == ("Bangalore", "in")
        assert resolve_location("Bangalore, Karnataka") == ("Bangalore", "in")

    def test_unknown_place_is_not_guessed(self) -> None:
        assert resolve_location("Somewhere Nice") is None

    def test_countries_are_derived_from_cities(self) -> None:
        assert country_for(["Bangalore", "Hyderabad"]) == ["in"]
        assert country_for(["London", "Berlin"]) == ["gb", "de"]

    def test_abbreviations_expand(self) -> None:
        assert "associate product manager" in [t.lower() for t in expand_title("apm")]


class TestQueryParser:
    def test_the_worked_example(self) -> None:
        query = parse_query_rules(
            "Find Associate Product Manager roles for 0-2 years of experience "
            "in Bangalore and Hyderabad."
        )
        assert query.titles == ["Associate Product Manager"]
        assert query.locations == ["Bangalore", "Hyderabad"]
        assert (query.min_years, query.max_years) == (0.0, 2.0)
        assert query.countries == ["in"]
        assert not query.remote_only

    def test_companies_and_skills(self) -> None:
        query = parse_query_rules(
            "APM openings at Razorpay and Swiggy in Bangalore with SQL and Python"
        )
        assert query.companies == ["Razorpay", "Swiggy"]
        assert query.locations == ["Bangalore"]
        assert query.keywords == ["SQL", "Python"]
        assert query.titles == ["Associate Product Manager"]

    def test_remote_is_recognised(self) -> None:
        query = parse_query_rules("remote data analyst roles for freshers")
        assert query.remote_only
        assert query.titles == ["data analyst"]
        assert query.experience_level is ExperienceLevel.ENTRY

    def test_industry_and_open_ended_experience(self) -> None:
        query = parse_query_rules("fintech product manager 3+ years Mumbai")
        assert query.industries == ["fintech"]
        assert query.min_years == 3.0
        assert query.max_years is None
        assert query.locations == ["Mumbai"]

    def test_exclusions(self) -> None:
        query = parse_query_rules("marketing internship in London, not agency")
        assert query.exclusions == ["agency"]
        assert query.locations == ["London"]

    def test_a_location_word_is_not_mistaken_for_a_company(self) -> None:
        query = parse_query_rules("product manager at Bangalore")
        assert query.companies == []
        assert query.locations == ["Bangalore"]

    def test_unparseable_request_falls_back_to_the_whole_phrase(self) -> None:
        query = parse_query_rules("zzzqqq")
        assert query.titles == ["zzzqqq"]
        assert not query.is_empty

    def test_empty_request_is_reported_not_invented(self) -> None:
        query = parse_query_rules("")
        assert query.is_empty
        assert query.parse_notes


class TestQueryRoundTrip:
    def test_dict_round_trip_preserves_everything(self) -> None:
        original = parse_query_rules(
            "Senior backend engineer jobs in Berlin with Python, 5+ years"
        )
        restored = JobQuery.from_dict(original.to_dict())
        assert restored.titles == original.titles
        assert restored.locations == original.locations
        assert restored.min_years == original.min_years
        assert restored.keywords == original.keywords
