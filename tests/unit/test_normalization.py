"""Job normalisation and duplicate detection."""

from __future__ import annotations

import pytest

from app.collectors.job_sources.base_job import JobCollectorMixin
from app.collectors.types import RawJob
from app.models.enums import SourceType
from app.security.url_guard import normalize_url
from app.utils.text import (
    content_fingerprint,
    jaccard,
    normalize_company_name,
    normalize_location,
    normalize_title,
    seniority_level,
    similarity,
)


class _Collector(JobCollectorMixin):
    """Minimal concrete collector so the mixin can be exercised directly."""

    name = "test"
    source_type = SourceType.MANUAL_ENTRY

    def collect(self) -> list[RawJob]:
        return []


@pytest.fixture
def collector() -> _Collector:
    return _Collector(company_name="Razorpay Software Pvt. Ltd.")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Razorpay Software Pvt. Ltd.", "razorpay"),
        ("Acme Technologies Inc", "acme"),
        ("Stripe, Inc.", "stripe"),
        ("Zoho Corporation Private Limited", "zoho"),
        ("  Flipkart  ", "flipkart"),
    ],
)
def test_normalize_company_name(raw: str, expected: str) -> None:
    assert normalize_company_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Product Analyst (Remote)", "product analyst"),
        ("Sr. Product Manager - Bangalore", "senior product manager"),
        ("APM, Payments [Full-time]", "associate product manager payments"),
        ("Data Analyst (f/m/d)", "data analyst"),
        ("Business Analyst — Req #12345", "business analyst"),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Bengaluru, India", "bangalore india"),
        ("Remote - India", "remote india"),
        ("Work From Home", "remote"),
        ("Gurugram", "gurgaon"),
        ("", ""),
    ],
)
def test_normalize_location(raw: str, expected: str) -> None:
    assert normalize_location(raw) == expected


def test_seniority_level_ordering() -> None:
    assert seniority_level("Intern, Product") == 0
    assert seniority_level("Associate Product Manager") == 1
    assert seniority_level("Senior Product Manager") == 3
    assert seniority_level("Director of Product") == 6
    assert seniority_level("Product Manager") is None


def test_normalize_url_canonicalises() -> None:
    assert normalize_url("HTTPS://Example.com:443/careers/") == "https://example.com/careers"
    assert normalize_url("http://example.com/jobs#apply") == "http://example.com/jobs"
    # Query strings carry the posting id on many boards and must survive.
    assert normalize_url("https://x.com/j?id=7") == "https://x.com/j?id=7"


def test_content_fingerprint_is_stable_and_discriminating() -> None:
    a = content_fingerprint("Acme Inc", "product analyst", "bangalore")
    b = content_fingerprint("acme inc", "Product Analyst", "Bangalore")
    c = content_fingerprint("Acme Inc", "data analyst", "bangalore")
    assert a == b
    assert a != c


def test_same_job_from_two_sources_collapses(collector: _Collector) -> None:
    """The whole point of the content hash: one posting, two URLs, one job."""
    from_ats = collector.normalize(
        RawJob(
            title="Product Analyst (Remote)",
            url="https://boards.greenhouse.io/razorpay/jobs/123",
            source=SourceType.GREENHOUSE_PUBLIC_API,
            source_url="api",
            location="Bengaluru, India",
        )
    )
    from_site = collector.normalize(
        RawJob(
            title="Product Analyst",
            url="https://razorpay.com/careers/product-analyst",
            source=SourceType.CAREER_PAGE_JSONLD,
            source_url="site",
            location="Bangalore, India",
        )
    )
    assert from_ats is not None and from_site is not None
    assert from_ats.content_hash == from_site.content_hash
    assert from_ats.canonical_url != from_site.canonical_url


def test_deduplicate_drops_repeats(collector: _Collector) -> None:
    records = []
    for url in ("https://a.example/j/1", "https://a.example/j/2"):
        record = collector.normalize(
            RawJob(
                title="Product Analyst",
                url=url,
                source=SourceType.MANUAL_ENTRY,
                source_url="x",
                location="Remote",
            )
        )
        assert record is not None
        records.append(record)

    unique = collector.deduplicate(records)
    assert len(unique) == 1
    assert collector.outcome.duplicates_dropped == 1


@pytest.mark.parametrize(
    ("title", "valid"),
    [
        ("Product Analyst", True),
        ("General Application", False),
        ("Talent Pool", False),
        ("Future Opportunities", False),
        ("AI", False),  # too short after normalisation
    ],
)
def test_validate_rejects_non_jobs(collector: _Collector, title: str, valid: bool) -> None:
    record = collector.normalize(
        RawJob(title=title, url="https://a.example/j", source=SourceType.MANUAL_ENTRY, source_url="x")
    )
    assert record is not None
    ok, reason = collector.validate(record)
    assert ok is valid
    if not valid:
        assert reason


def test_normalize_rejects_missing_fields(collector: _Collector) -> None:
    assert collector.normalize(RawJob(title="", url="https://a.example", source=SourceType.MANUAL_ENTRY, source_url="x")) is None
    assert collector.normalize(RawJob(title="Analyst", url="", source=SourceType.MANUAL_ENTRY, source_url="x")) is None


def test_similarity_helpers() -> None:
    assert similarity("product analyst", "product analyst") == 1.0
    assert similarity("product analyst", "product analytics") > 0.8
    assert similarity("product analyst", "backend engineer") < 0.5
    assert jaccard("sql and python", "python and sql") == 1.0
