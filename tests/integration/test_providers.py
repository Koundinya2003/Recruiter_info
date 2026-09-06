"""Every provider reads its source's documented payload shape correctly.

The endpoints are pointed at the local mock server so the parsing is exercised
without touching a real API. What is under test is the mapping from a provider's
JSON into a ``RawJob`` — the layer where a mistake silently mislabels real data.
"""

from __future__ import annotations

import pytest

from app.collectors.http_client import SafeHTTPClient
from app.config import settings
from app.models.enums import SourceType
from app.providers.aggregators import (
    AdzunaProvider,
    ArbeitnowProvider,
    JobicyProvider,
    RemotiveProvider,
    TheMuseProvider,
    USAJobsProvider,
    linkedin_people_search_url,
)
from app.providers.company_boards import CompanyBoardProvider, candidate_tokens
from app.providers.registry import provider_statuses
from app.search.parser import parse_query_rules

QUERY = parse_query_rules(
    "Find Associate Product Manager roles for 0-2 years of experience "
    "in Bangalore and Hyderabad."
)


@pytest.fixture
def client():
    with SafeHTTPClient(allow_private=True, max_pages=60, max_retries=0) as http:
        yield http


class TestTheMuse:
    def test_parses_results_and_filters_by_title(self, client, mock_site, monkeypatch) -> None:
        monkeypatch.setattr(TheMuseProvider, "BASE", mock_site.url("/api/public/jobs"))
        result = TheMuseProvider(client).run(QUERY)

        assert result.skipped_reason is None
        titles = [job.title for job in result.jobs]
        assert "Associate Product Manager" in titles
        # The Muse has no server-side search, so an unrelated role must be
        # filtered out locally rather than passed on.
        assert "Principal Security Engineer" not in titles

        job = result.jobs[0]
        assert job.company_name == "Demo Fintech"
        assert job.location == "Bangalore, India"
        assert job.source is SourceType.THE_MUSE
        assert job.posted_at is not None
        assert "roadmap" in (job.description or "")


class TestRemotive:
    def test_parses_remote_jobs(self, client, mock_site, monkeypatch) -> None:
        monkeypatch.setattr(RemotiveProvider, "BASE", mock_site.url("/api/remote-jobs"))
        remote_query = parse_query_rules("remote Associate Product Manager roles")
        result = RemotiveProvider(client).run(remote_query)

        assert len(result.jobs) == 1
        job = result.jobs[0]
        assert job.company_name == "Remote Demo Co"
        assert job.is_remote
        assert job.source is SourceType.REMOTIVE

    def test_skipped_when_the_search_names_cities(self, client) -> None:
        result = RemotiveProvider(client).run(QUERY)
        assert result.skipped_reason
        assert "remote" in result.skipped_reason
        assert result.jobs == []


class TestAdzuna:
    def test_skipped_without_credentials(self, client) -> None:
        result = AdzunaProvider(client).run(QUERY)
        assert result.skipped_reason
        assert "adzuna_app_id" in result.skipped_reason

    def test_parses_results_when_configured(self, client, mock_site, monkeypatch) -> None:
        monkeypatch.setattr(settings, "adzuna_app_id", "test-id")
        monkeypatch.setattr(settings, "adzuna_app_key", "test-key")
        monkeypatch.setattr(
            AdzunaProvider, "BASE", mock_site.url("/v1/api/jobs/{country}/search/1")
        )
        result = AdzunaProvider(client).run(QUERY)

        assert result.skipped_reason is None
        job = result.jobs[0]
        assert job.title == "Associate Product Manager"
        assert job.company_name == "Demo Fintech"
        assert job.location == "Hyderabad, Telangana"
        assert job.source is SourceType.ADZUNA


class TestArbeitnow:
    def test_parses_and_filters(self, client, mock_site, monkeypatch) -> None:
        monkeypatch.setattr(ArbeitnowProvider, "BASE", mock_site.url("/api/job-board-api"))
        monkeypatch.setattr(ArbeitnowProvider, "MAX_PAGES", 1)
        result = ArbeitnowProvider(client).run(QUERY)
        assert result.jobs[0].company_name == "EU Demo GmbH"
        assert result.jobs[0].is_remote


class TestJobicy:
    def test_parses_remote_jobs(self, client, mock_site, monkeypatch) -> None:
        monkeypatch.setattr(JobicyProvider, "BASE", mock_site.url("/api/v2/remote-jobs"))
        result = JobicyProvider(client).run(parse_query_rules("remote product manager"))
        assert result.jobs[0].company_name == "Jobicy Demo"
        assert result.jobs[0].is_remote


class TestUSAJobs:
    def test_skipped_without_credentials(self, client) -> None:
        result = USAJobsProvider(client).run(QUERY)
        assert result.skipped_reason

    def test_skipped_outside_the_united_states(self, client, monkeypatch) -> None:
        monkeypatch.setattr(settings, "usajobs_api_key", "k")
        monkeypatch.setattr(settings, "usajobs_user_agent", "me@example.com")
        result = USAJobsProvider(client).run(QUERY)
        assert result.skipped_reason
        assert "United States" in result.skipped_reason

    def test_parses_results(self, client, mock_site, monkeypatch) -> None:
        monkeypatch.setattr(settings, "usajobs_api_key", "k")
        monkeypatch.setattr(settings, "usajobs_user_agent", "me@example.com")
        monkeypatch.setattr(USAJobsProvider, "BASE", mock_site.url("/api/search"))
        result = USAJobsProvider(client).run(parse_query_rules("Program Analyst in Washington DC"))
        job = result.jobs[0]
        assert job.company_name == "Demo Agency"
        assert job.apply_url


class TestCompanyBoards:
    def test_board_slugs_are_guesses_to_be_tested(self) -> None:
        assert candidate_tokens("Razorpay Software Private Limited") == ["razorpay"]
        assert "themuse" in candidate_tokens("The Muse")
        assert candidate_tokens("") == []

    def test_a_confirmed_board_yields_its_jobs(self, client, mock_site, monkeypatch) -> None:
        monkeypatch.setitem(
            __import__("app.providers.company_boards", fromlist=["PLATFORMS"]).PLATFORMS,
            "greenhouse",
            {
                "api": mock_site.url("/v1/boards/{token}/jobs"),
                "public": mock_site.url("/boards/{token}"),
                "source": SourceType.GREENHOUSE,
                "hosts": ("127.0.0.1", "localhost"),
            },
        )
        query = parse_query_rules("Associate Product Manager at Demofintech")
        provider = CompanyBoardProvider(client)
        result = provider.run(query)

        assert result.skipped_reason is None
        assert result.jobs, "the confirmed board should return its postings"
        assert all(job.company_name == "Demofintech" for job in result.jobs)
        assert result.jobs[0].source is SourceType.GREENHOUSE
        assert any("Confirmed" in note for note in result.notes)

    def test_an_unknown_company_is_reported_not_invented(self, client, monkeypatch) -> None:
        """A company with no public board must produce a note, never a job."""
        query = parse_query_rules("Product Manager at Zzzznotarealcompanyzzz")
        provider = CompanyBoardProvider(client)
        result = provider.run(query)
        assert result.jobs == []
        assert any("No public" in note for note in result.notes)


class TestRegistry:
    def test_every_provider_reports_its_configuration(self) -> None:
        statuses = provider_statuses()
        names = {s.name for s in statuses}
        assert {"adzuna", "themuse", "remotive", "company_boards"} <= names
        for status in statuses:
            assert status.label
            assert status.coverage
            if not status.configured:
                assert status.missing_settings

    def test_key_free_sources_are_usable_out_of_the_box(self) -> None:
        usable = {s.name for s in provider_statuses() if s.usable}
        assert {"themuse", "remotive", "arbeitnow", "jobicy"} <= usable


def test_directory_link_is_a_search_not_a_person() -> None:
    url = linkedin_people_search_url("Demo Fintech", "recruiter")
    assert url.startswith("https://www.linkedin.com/search/results/people/")
    assert "Demo%20Fintech" in url or "Demo+Fintech" in url
