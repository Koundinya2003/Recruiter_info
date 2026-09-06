"""Finding people to contact, and refusing to invent them."""

from __future__ import annotations

import pytest

from app.collectors.http_client import SafeHTTPClient
from app.models.enums import ContactRole, EmailStatus, SourceType
from app.services.contacts import (
    ContactCandidate,
    classify_title,
    dedupe_contacts,
    discover_contacts,
    domain_from_url,
    rank_contacts,
)


@pytest.fixture
def client():
    with SafeHTTPClient(allow_private=True, max_pages=20, max_retries=0) as http:
        yield http


class TestTitleClassification:
    @pytest.mark.parametrize(
        ("title", "role"),
        [
            ("Technical Recruiter", ContactRole.FUNCTION_RECRUITER),
            ("Talent Acquisition Partner, Product", ContactRole.TALENT_ACQUISITION),
            ("Recruiter", ContactRole.TALENT_ACQUISITION),
            ("Hiring Manager", ContactRole.HIRING_MANAGER),
            ("Director of Product", ContactRole.TEAM_LEAD),
        ],
    )
    def test_hiring_titles_are_recognised(self, title, role) -> None:
        assert classify_title(title) is role

    def test_unrelated_titles_are_ignored(self) -> None:
        assert classify_title("Warehouse Operative") is None
        assert classify_title(None) is None


class TestDiscovery:
    def test_reads_published_contacts_from_a_team_page(self, client, mock_site) -> None:
        result = discover_contacts(
            client,
            company_name="Demo Fintech",
            company_domain=None,
            careers_url=mock_site.url("/team"),
        )
        people = [c for c in result.contacts if c.role.is_person and c.name]
        names = {c.name for c in people}
        assert "Demo Recruiter" in names  # from JSON-LD
        assert "Priya Demo" in names  # from a mailto link next to the name

        recruiter = next(c for c in people if c.name == "Demo Recruiter")
        assert recruiter.email == "demo.recruiter@demo-fintech.example"
        assert recruiter.email_status is EmailStatus.PUBLISHED_ATTRIBUTED
        assert recruiter.source_url  # provenance is never optional

    def test_a_team_inbox_is_recorded_as_an_inbox_not_a_person(self, client, mock_site) -> None:
        result = discover_contacts(
            client,
            company_name="Demo Fintech",
            company_domain=None,
            careers_url=mock_site.url("/team"),
        )
        aliases = [c for c in result.contacts if c.role is ContactRole.TALENT_ALIAS]
        assert any(c.email == "careers@demo-fintech.example" for c in aliases)
        assert all(c.name is None for c in aliases), "an inbox must not be given a person's name"

    def test_personal_mailboxes_are_never_collected(self, client, mock_site) -> None:
        result = discover_contacts(
            client,
            company_name="Demo Fintech",
            company_domain=None,
            careers_url=mock_site.url("/team"),
        )
        addresses = {c.email for c in result.contacts if c.email}
        assert not any(a.endswith("@gmail.com") for a in addresses)

    def test_a_directory_search_link_is_always_offered(self, client, mock_site) -> None:
        result = discover_contacts(
            client,
            company_name="Demo Fintech",
            company_domain=None,
            careers_url=mock_site.url("/team"),
        )
        links = [c for c in result.contacts if c.role is ContactRole.SEARCH_LINK]
        assert len(links) == 1
        assert links[0].name is None, "a search link must not claim a person exists"
        assert links[0].search_url.startswith("https://www.linkedin.com/")

    def test_a_company_with_no_published_people_says_so(self, client, mock_site) -> None:
        result = discover_contacts(
            client,
            company_name="Silent Corp",
            company_domain=None,
            careers_url=mock_site.url("/jobs/4001"),
        )
        people = [c for c in result.contacts if c.role.is_person and c.name]
        assert people == []
        assert any("will not guess" in note for note in result.notes)

    def test_no_pages_to_check_is_reported(self, client) -> None:
        result = discover_contacts(
            client, company_name="Unknown Co", company_domain=None, careers_url=None
        )
        assert any("no pages to check" in note for note in result.notes)
        # The search link is still offered, so the user has somewhere to start.
        assert len(result.contacts) == 1

    def test_addresses_on_other_domains_are_rejected(self, client, mock_site) -> None:
        """An address on someone else's domain is not this company's contact."""
        result = discover_contacts(
            client,
            company_name="Demo Fintech",
            company_domain="somewhere-else.example",
            careers_url=mock_site.url("/team"),
        )
        assert all(
            c.email is None or c.email.endswith("somewhere-else.example")
            for c in result.contacts
        )

    def test_a_posting_contact_is_read_from_the_posting(self, client) -> None:
        result = discover_contacts(
            client,
            company_name="Demo Fintech",
            company_domain="demo-fintech.example",
            careers_url=None,
            posting_url="https://demo-fintech.example/jobs/1",
            posting_description="Questions? Write to careers@demo-fintech.example.",
        )
        from_posting = [
            c for c in result.contacts if c.source is SourceType.JOB_POSTING_CONTACT
        ]
        assert from_posting
        assert from_posting[0].email == "careers@demo-fintech.example"


class TestRankingAndDedupe:
    def _candidate(self, **kwargs) -> ContactCandidate:
        base = {
            "role": ContactRole.TALENT_ACQUISITION,
            "company_name": "Acme",
            "source": SourceType.COMPANY_TEAM_PAGE,
        }
        base.update(kwargs)
        return ContactCandidate(**base)

    def test_the_better_evidenced_duplicate_wins(self) -> None:
        without = self._candidate(name="Sam Talent", confidence=0.5)
        with_email = self._candidate(
            name="Sam Talent", email="sam@acme.example", confidence=0.9
        )
        result = dedupe_contacts([without, with_email])
        assert len(result) == 1
        assert result[0].email == "sam@acme.example"

    def test_recruiters_rank_above_search_links(self) -> None:
        ordered = rank_contacts(
            [
                self._candidate(role=ContactRole.SEARCH_LINK, search_url="https://x.example"),
                self._candidate(role=ContactRole.TALENT_ALIAS, email="jobs@acme.example"),
                self._candidate(
                    role=ContactRole.FUNCTION_RECRUITER,
                    name="Ada Recruiter",
                    email="ada@acme.example",
                ),
            ]
        )
        assert ordered[0].role is ContactRole.FUNCTION_RECRUITER
        assert ordered[-1].role is ContactRole.SEARCH_LINK


class TestDomainExtraction:
    def test_an_employer_domain_is_extracted(self) -> None:
        assert domain_from_url("https://www.acme.example/careers/1") == "acme.example"

    @pytest.mark.parametrize(
        "url",
        [
            "https://boards.greenhouse.io/acme/jobs/1",
            "https://jobs.lever.co/acme/1",
            "https://www.linkedin.com/jobs/view/1",
            "https://www.themuse.com/jobs/acme/apm",
        ],
    )
    def test_a_third_party_host_is_not_the_employer(self, url: str) -> None:
        assert domain_from_url(url) is None
