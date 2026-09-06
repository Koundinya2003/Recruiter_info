"""Checking postings against a stand-in job site."""

from __future__ import annotations

import pytest

from app.collectors.http_client import SafeHTTPClient
from app.models.enums import CheckOutcome, SourceType, ValidationCheck, ValidationStatus
from app.services.validation import validate_posting
from tests.fixtures.mock_server import MockSite


@pytest.fixture
def client():
    with SafeHTTPClient(allow_private=True, max_pages=40, max_retries=0) as http:
        yield http


def _validate(client: SafeHTTPClient, site: MockSite, path: str, **kwargs):
    defaults = {
        "title": "Associate Product Manager",
        "company_name": "Demo Fintech",
        "source": SourceType.THE_MUSE,
    }
    defaults.update(kwargs)
    return validate_posting(client, url=site.url(path), **defaults)


class TestLivePostings:
    def test_a_live_posting_is_valid(self, client, mock_site) -> None:
        outcome = _validate(client, mock_site, "/posting/live")
        assert outcome.status is ValidationStatus.VALID
        assert outcome.checks[ValidationCheck.URL_REACHABLE.value] == CheckOutcome.PASS.value
        assert outcome.checks[ValidationCheck.COMPANY_MATCHES.value] == CheckOutcome.PASS.value
        assert outcome.checks[ValidationCheck.STILL_ACTIVE.value] == CheckOutcome.PASS.value
        assert outcome.displayable

    def test_the_reason_is_always_stated(self, client, mock_site) -> None:
        outcome = _validate(client, mock_site, "/posting/live")
        assert outcome.reason


class TestRejectedPostings:
    def test_a_closed_posting_is_withheld(self, client, mock_site) -> None:
        outcome = _validate(client, mock_site, "/posting/closed")
        assert outcome.status is ValidationStatus.EXPIRED
        assert not outcome.displayable
        assert "no longer accepting applications" in outcome.reason

    def test_an_expired_valid_through_is_withheld(self, client, mock_site) -> None:
        outcome = _validate(client, mock_site, "/posting/expired")
        assert outcome.status is ValidationStatus.EXPIRED
        assert not outcome.displayable

    def test_a_dead_link_is_withheld(self, client, mock_site) -> None:
        outcome = _validate(client, mock_site, "/posting/gone")
        assert outcome.status is ValidationStatus.BROKEN
        assert outcome.http_status == 404
        assert not outcome.displayable

    def test_a_posting_naming_another_company_is_withheld(self, client, mock_site) -> None:
        outcome = _validate(client, mock_site, "/posting/wrong-company")
        assert outcome.status is ValidationStatus.MISMATCH
        assert "Completely Different Holdings" in outcome.reason
        assert not outcome.displayable


class TestCouldNotConfirm:
    def test_a_robots_disallowed_path_is_unverified_not_rejected(self, client, mock_site) -> None:
        """A site declining automated access says nothing about the job."""
        outcome = _validate(client, mock_site, "/private/secret")
        assert outcome.status is ValidationStatus.UNVERIFIED
        assert outcome.displayable
        assert not outcome.status.is_confirmed
        assert "declined" in outcome.reason

    def test_a_403_is_unverified_not_rejected(self, client, mock_site) -> None:
        outcome = _validate(client, mock_site, "/blocked")
        assert outcome.status is ValidationStatus.UNVERIFIED
        assert outcome.displayable

    def test_a_first_party_posting_survives_a_blocked_page(self, client, mock_site) -> None:
        """The company's own API already said the role is open."""
        outcome = _validate(
            client, mock_site, "/blocked", source=SourceType.GREENHOUSE
        )
        assert outcome.status is ValidationStatus.LIKELY_VALID
        assert "own job board API" in outcome.reason


class TestUnreachableHosts:
    def test_a_private_address_is_refused_outright(self, mock_site) -> None:
        with SafeHTTPClient(allow_private=False, max_retries=0) as guarded:
            outcome = validate_posting(
                guarded,
                url=mock_site.url("/posting/live"),
                title="Associate Product Manager",
                company_name="Demo Fintech",
                source=SourceType.THE_MUSE,
            )
        assert outcome.status is ValidationStatus.UNVERIFIED
