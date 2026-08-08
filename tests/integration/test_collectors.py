"""Collectors against the local mock site — no real website is contacted."""

from __future__ import annotations

import pytest

from app.collectors.career_pages.career_page import CareerPageCollector
from app.collectors.http_client import FetchBlocked, SafeHTTPClient
from app.collectors.job_sources.ats import (
    AshbyCollector,
    GreenhouseCollector,
    LeverCollector,
    detect_ats,
)
from app.collectors.public_sources.recruiter_sources import PublicRecruiterSourceCollector
from app.collectors.registry import is_authoritative_source
from app.models.enums import EmailConfidence, SourceType
from tests.fixtures.mock_server import MockSite


@pytest.fixture
def client() -> SafeHTTPClient:
    # allow_private is the documented test-only escape hatch for the mock site.
    http = SafeHTTPClient(allow_private=True, max_pages=40, max_retries=0)
    yield http
    http.close()


# --- ATS detection --------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "platform", "token"),
    [
        ("https://boards.greenhouse.io/razorpay", "greenhouse", "razorpay"),
        ("https://job-boards.greenhouse.io/acme/jobs", "greenhouse", "acme"),
        ("https://boards.greenhouse.io/embed/job_board?for=stripe", "greenhouse", "stripe"),
        ("https://jobs.lever.co/acme", "lever", "acme"),
        ("https://jobs.ashbyhq.com/openai", "ashby", "openai"),
    ],
)
def test_detect_ats(url: str, platform: str, token: str) -> None:
    detection = detect_ats(url)
    assert detection is not None
    assert (detection.platform, detection.board_token) == (platform, token)


@pytest.mark.parametrize("url", ["https://example.com/careers", "", None, "not a url"])
def test_detect_ats_returns_none_for_non_ats(url: str | None) -> None:
    assert detect_ats(url) is None


# --- Job collectors -------------------------------------------------------------


def test_greenhouse_collector_reads_the_public_api(mock_site: MockSite, client: SafeHTTPClient) -> None:
    collector = GreenhouseCollector(
        company_name="Demo Fintech",
        board_token="demo",
        client=client,
        api_url=mock_site.url("/v1/boards/demo/jobs"),
    )
    outcome = collector.run()

    titles = {record.title for record in outcome.records}
    assert "Product Analyst" in titles
    assert "Associate Product Manager" in titles
    # "General Application" is not a real opening and must be rejected with a reason.
    assert "General Application" not in titles
    assert any("general" in reason.lower() for _, reason in outcome.rejected)
    assert outcome.errors == []
    assert outcome.pages_fetched >= 1

    posting = next(r for r in outcome.records if r.title == "Product Analyst")
    assert posting.location == "Bangalore, India"
    assert posting.posted_at is not None
    assert posting.source is SourceType.GREENHOUSE_PUBLIC_API
    assert "SQL" in (posting.description or "")


def test_lever_collector_reads_the_public_api(mock_site: MockSite, client: SafeHTTPClient) -> None:
    collector = LeverCollector(
        company_name="Demo Fintech",
        board_token="demo",
        client=client,
        api_url=mock_site.url("/v0/postings/demo"),
    )
    outcome = collector.run()
    assert len(outcome.records) == 1
    record = outcome.records[0]
    assert record.title == "Data Analyst"
    assert record.employment_type == "Full-time"
    assert record.posted_at is not None


def test_ashby_collector_handles_an_empty_board(mock_site: MockSite, client: SafeHTTPClient) -> None:
    collector = AshbyCollector(
        company_name="Demo",
        board_token="demo",
        client=client,
        api_url=mock_site.url("/missing"),
    )
    outcome = collector.run()
    assert outcome.records == []
    # A 404 is not a silent success: it is reported.
    assert outcome.errors or outcome.blocked_reason is None


def test_ats_collectors_are_authoritative_sources() -> None:
    collector = GreenhouseCollector(company_name="X", board_token="y")
    assert is_authoritative_source(collector) is True
    collector.close()


# --- Career page ----------------------------------------------------------------


def test_career_page_prefers_structured_data(mock_site: MockSite, client: SafeHTTPClient) -> None:
    collector = CareerPageCollector(
        company_name="Demo Fintech", career_page_url=mock_site.url("/careers"), client=client
    )
    outcome = collector.run()

    assert len(outcome.records) == 1
    record = outcome.records[0]
    assert record.title == "Product Operations Associate"
    assert record.source is SourceType.CAREER_PAGE_JSONLD
    assert record.location == "Bengaluru, IN"
    assert record.posted_at is not None
    assert any("JSON-LD" in note for note in outcome.notes)


def test_career_page_reports_when_it_finds_nothing(mock_site: MockSite, client: SafeHTTPClient) -> None:
    """The failure mode this project cares about most: quiet emptiness."""
    collector = CareerPageCollector(
        company_name="Demo", career_page_url=mock_site.url("/jobs/empty-page"), client=client
    )
    outcome = collector.run()
    assert outcome.records == []
    assert outcome.notes, "a collector that found nothing must say why"


# --- Recruiter sources ----------------------------------------------------------


def test_recruiter_collector_assigns_confidence_by_provenance(
    mock_site: MockSite, client: SafeHTTPClient
) -> None:
    collector = PublicRecruiterSourceCollector(
        company_name="Demo Fintech",
        urls=[mock_site.url("/team")],
        company_domain="demo-fintech.example",
        client=client,
    )
    outcome = collector.run()
    by_name = {r.name: r for r in outcome.records}

    # Published + attributed to a person -> HIGH
    assert by_name["Demo Recruiter"].confidence is EmailConfidence.HIGH
    assert by_name["Demo Recruiter"].email == "demo.recruiter@demo-fintech.example"
    # A person with a published mailto link next to their name -> HIGH
    assert by_name["Priya Demo"].email == "priya.demo@demo-fintech.example"
    # Shared company address, not attributable to an individual -> MEDIUM
    team = by_name["Demo Fintech Talent Team"]
    assert team.confidence is EmailConfidence.MEDIUM
    # Every record keeps where it came from.
    for record in outcome.records:
        assert record.source_url
        assert record.is_inferred is False


def test_recruiter_collector_never_collects_personal_mailboxes(
    mock_site: MockSite, client: SafeHTTPClient
) -> None:
    collector = PublicRecruiterSourceCollector(
        company_name="Demo Fintech",
        urls=[mock_site.url("/team")],
        company_domain="demo-fintech.example",
        client=client,
    )
    outcome = collector.run()
    addresses = {r.email for r in outcome.records if r.email}
    assert not any(a.endswith("@gmail.com") for a in addresses)


def test_recruiter_collector_says_so_when_nothing_is_published(
    mock_site: MockSite, client: SafeHTTPClient
) -> None:
    collector = PublicRecruiterSourceCollector(
        company_name="Demo",
        urls=[mock_site.url("/careers")],
        company_domain="demo-fintech.example",
        client=client,
    )
    outcome = collector.run()
    assert outcome.records == []
    assert any("do not publish" in note for note in outcome.notes)


# --- HTTP policy ----------------------------------------------------------------


def test_robots_disallowed_path_is_not_fetched(mock_site: MockSite, client: SafeHTTPClient) -> None:
    with pytest.raises(FetchBlocked) as excinfo:
        client.fetch(mock_site.url("/private/secret"))
    assert excinfo.value.reason == "robots_disallowed"
    assert client.stats.blocked_by_robots == 1


def test_site_refusal_is_terminal_and_not_worked_around(
    mock_site: MockSite, client: SafeHTTPClient
) -> None:
    with pytest.raises(FetchBlocked) as excinfo:
        client.fetch(mock_site.url("/blocked"))
    assert excinfo.value.reason == "http_403"
    assert client.stats.blocked_by_site == 1
    # One attempt only — retrying a 403 is exactly the evasion we refuse to do.
    assert client.stats.requests == 1


def test_redirects_are_followed_but_revalidated(mock_site: MockSite, client: SafeHTTPClient) -> None:
    result = client.fetch(mock_site.url("/redirect"))
    assert result.ok
    assert result.final_url.endswith("/careers")


def test_crawl_budget_is_enforced(mock_site: MockSite) -> None:
    http = SafeHTTPClient(allow_private=True, max_pages=2, max_retries=0)
    try:
        http.fetch(mock_site.url("/careers"))
        http.fetch(mock_site.url("/team"))
        with pytest.raises(FetchBlocked) as excinfo:
            http.fetch(mock_site.url("/careers"))
        assert excinfo.value.reason == "budget_exhausted"
    finally:
        http.close()


def test_response_size_cap_is_enforced(mock_site: MockSite) -> None:
    http = SafeHTTPClient(allow_private=True, max_bytes=10, max_retries=0)
    try:
        with pytest.raises(FetchBlocked) as excinfo:
            http.fetch(mock_site.url("/careers"))
        assert excinfo.value.reason == "response_too_large"
    finally:
        http.close()


def test_client_refuses_unsafe_urls_even_in_private_mode() -> None:
    http = SafeHTTPClient(allow_private=False, max_retries=0)
    try:
        with pytest.raises(FetchBlocked) as excinfo:
            http.fetch("http://169.254.169.254/latest/meta-data")
        assert excinfo.value.reason == "unsafe_url"
        assert http.stats.ssrf_rejected == 1
    finally:
        http.close()


def test_collector_never_raises_out_of_run() -> None:
    """A broken collector must produce a reported failure, not an exception."""
    collector = GreenhouseCollector(
        company_name="X",
        board_token="y",
        client=SafeHTTPClient(allow_private=False, max_retries=0),
        api_url="http://127.0.0.1:9/nothing-here",
    )
    outcome = collector.run()
    assert outcome.records == []
    assert outcome.errors or outcome.blocked_reason
    collector.close()
