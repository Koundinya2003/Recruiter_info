"""Collapsing the same role found on several boards."""

from __future__ import annotations

from app.services.validation import canonical_place, canonicalize_url, duplicate_key


class TestCanonicalUrl:
    def test_tracking_parameters_are_stripped(self) -> None:
        assert canonicalize_url(
            "https://WWW.Example.com/jobs/123/?utm_source=x&gh_src=y&id=7"
        ) == "https://example.com/jobs/123?id=7"

    def test_case_and_trailing_slash_do_not_create_duplicates(self) -> None:
        assert canonicalize_url("https://Example.com/Jobs/9/") == canonicalize_url(
            "https://www.example.com/Jobs/9"
        )

    def test_nonsense_input_is_returned_unchanged(self) -> None:
        assert canonicalize_url("not a url") == "not a url"


class TestFingerprint:
    def test_same_role_different_spelling_collapses(self) -> None:
        a = duplicate_key(
            company_name="Acme Inc.",
            title="Senior Product Manager",
            location="Bangalore, Karnataka, India",
        )
        b = duplicate_key(
            company_name="Acme", title="Product Manager, Senior", location="Bengaluru"
        )
        assert a == b

    def test_different_seniority_does_not_collapse(self) -> None:
        a = duplicate_key(company_name="Acme", title="Senior Product Manager", location="Pune")
        b = duplicate_key(company_name="Acme", title="Product Manager", location="Pune")
        assert a != b

    def test_different_company_does_not_collapse(self) -> None:
        a = duplicate_key(company_name="Acme", title="Product Manager", location="Pune")
        b = duplicate_key(company_name="Globex", title="Product Manager", location="Pune")
        assert a != b


class TestCanonicalPlace:
    def test_known_city_is_normalised(self) -> None:
        assert canonical_place("Bengaluru, Karnataka, India") == "bangalore"

    def test_unknown_place_is_left_alone(self) -> None:
        assert canonical_place("Springfield") == "springfield"

    def test_empty_is_empty(self) -> None:
        assert canonical_place(None) == ""
