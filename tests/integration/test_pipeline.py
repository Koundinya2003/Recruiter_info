"""The search pipeline, end to end, against stand-in sources."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.application import Application
from app.models.contact import Contact, JobContact
from app.models.enums import SearchRunStatus, SourceType, ValidationStatus
from app.models.job import Job
from app.models.search import SearchRun
from app.models.user import User
from app.providers.aggregators import TheMuseProvider
from app.search.parser import parse_query_rules
from app.services.discovery import run_search
from tests.conftest import requires_db

pytestmark = requires_db

QUERY_TEXT = (
    "Find Associate Product Manager roles for 0-2 years of experience "
    "in Bangalore and Hyderabad."
)


@pytest.fixture
def muse_only(mock_site, monkeypatch):
    """A search that consults only The Muse, served by the mock site."""
    monkeypatch.setattr(TheMuseProvider, "BASE", mock_site.url("/api/public/jobs"))
    monkeypatch.setattr(settings, "crawler_allow_private_networks", True)
    monkeypatch.setattr(settings, "contacts_max_companies", 4)
    return mock_site


def _run(session: Session, user: User, text: str = QUERY_TEXT, **kwargs):
    return run_search(
        session, user, parse_query_rules(text), only_providers=["themuse"], **kwargs
    )


class TestFunnel:
    def test_a_search_keeps_only_matching_validated_postings(
        self, session, user, muse_only
    ) -> None:
        outcome = _run(session, user)

        assert outcome.status in {SearchRunStatus.SUCCESS, SearchRunStatus.PARTIAL}
        assert outcome.raw_found >= 1
        assert outcome.job_ids

        jobs = session.scalars(select(Job).where(Job.user_id == user.id)).all()
        assert len(jobs) == len(outcome.job_ids)
        for job in jobs:
            assert job.validation_status.is_displayable
            assert job.validation_reason, "every posting must carry its verdict"
            assert job.relevance_score >= settings.search_min_relevance

    def test_an_off_target_role_never_reaches_the_library(
        self, session, user, muse_only
    ) -> None:
        """The mock source also returns a senior Berlin role; it must not appear."""
        _run(session, user)
        titles = {
            job.title for job in session.scalars(select(Job).where(Job.user_id == user.id)).all()
        }
        assert "Principal Security Engineer" not in titles

    def test_the_pipeline_drops_what_a_source_could_not_filter(
        self, session, user, muse_only
    ) -> None:
        """A posting the provider passes through is still gated on location.

        The Muse has no server-side search, so its own filter only looks at
        titles. This search matches that title but not the city, which means
        the drop has to happen in the pipeline — and be counted.
        """
        outcome = _run(session, user, "Principal Security Engineer roles in Bangalore")
        assert outcome.raw_found >= 1
        assert outcome.irrelevant_dropped >= 1
        assert outcome.job_ids == []

    def test_the_run_record_explains_where_everything_went(
        self, session, user, muse_only
    ) -> None:
        outcome = _run(session, user)
        run = session.get(SearchRun, outcome.run_id)

        assert run is not None
        assert run.finished_at is not None
        assert run.raw_found == outcome.raw_found
        accounted = (
            run.duplicates_dropped
            + run.irrelevant_dropped
            + run.rejected
            + run.validated
            + run.unverified
        )
        assert accounted <= run.raw_found
        assert "The Muse" in run.providers_queried

    def test_unusable_sources_are_recorded_with_a_reason(
        self, session, user, muse_only
    ) -> None:
        outcome = run_search(session, user, parse_query_rules(QUERY_TEXT))
        assert outcome.providers_skipped
        for reason in outcome.providers_skipped.values():
            assert reason


class TestIdempotence:
    def test_running_the_same_search_twice_does_not_duplicate_jobs(
        self, session, user, muse_only
    ) -> None:
        first = _run(session, user)
        before = session.scalars(select(Job).where(Job.user_id == user.id)).all()
        second = _run(session, user)
        after = session.scalars(select(Job).where(Job.user_id == user.id)).all()

        assert len(after) == len(before)
        assert first.job_ids == second.job_ids

    def test_a_company_is_created_once_and_reused(self, session, user, muse_only) -> None:
        from app.models.company import Company

        _run(session, user)
        _run(session, user)
        companies = session.scalars(select(Company)).all()
        names = [c.normalized_name for c in companies]
        assert len(names) == len(set(names))


class TestContactsAreAttached:
    def test_every_job_gets_at_least_a_way_to_find_someone(
        self, session, user, muse_only
    ) -> None:
        outcome = _run(session, user)
        for job_id in outcome.job_ids:
            links = session.scalars(
                select(JobContact).where(JobContact.job_id == job_id)
            ).all()
            assert links, "a job with no contact route at all is not useful"

    def test_no_contact_is_stored_without_provenance(self, session, user, muse_only) -> None:
        _run(session, user)
        for contact in session.scalars(select(Contact)).all():
            assert contact.source is not None
            if contact.email:
                assert contact.source_url, "a stored address must cite where it was published"
            if contact.source is SourceType.DIRECTORY_SEARCH_LINK:
                assert contact.name is None
                assert contact.search_url

    def test_contacts_can_be_skipped(self, session, user, muse_only) -> None:
        outcome = _run(session, user, find_contacts=False)
        links = session.scalars(select(JobContact)).all()
        assert outcome.job_ids
        assert links == []


class TestTrackerHandover:
    def test_a_found_job_can_be_tracked(self, session, user, muse_only) -> None:
        from app.services import applications as tracker

        outcome = _run(session, user)
        job = session.get(Job, outcome.job_ids[0])
        application = tracker.save_job(session, user, job)

        assert application.job_title == job.title
        assert application.company_name == job.company_name
        assert application.job_url
        assert job.is_saved

    def test_the_application_survives_the_posting_being_deleted(
        self, session, user, muse_only
    ) -> None:
        from app.services import applications as tracker

        outcome = _run(session, user)
        job = session.get(Job, outcome.job_ids[0])
        application = tracker.save_job(session, user, job)
        title, url = application.job_title, application.job_url

        session.delete(job)
        session.flush()
        session.expire_all()

        survivor = session.get(Application, application.id)
        assert survivor is not None
        assert survivor.job_id is None
        assert survivor.job_title == title
        assert survivor.job_url == url


class TestNothingIsInvented:
    def test_every_job_traces_back_to_a_real_provider_response(
        self, session, user, muse_only
    ) -> None:
        _run(session, user)
        for job in session.scalars(select(Job)).all():
            assert job.source in set(SourceType)
            assert job.source_query_url, "a posting must record the query that produced it"
            assert job.job_url.startswith("http")

    def test_an_experience_requirement_is_never_filled_in(
        self, session, user, muse_only
    ) -> None:
        """A posting that states no requirement must stay silent about it."""
        _run(session, user)
        jobs = session.scalars(select(Job)).all()
        assert jobs
        for job in jobs:
            stated = job.experience_text or job.min_years is not None or job.max_years is not None
            if not stated:
                assert job.experience_label == "Not stated"
            else:
                # A stated requirement must come from the posting's own words.
                assert job.experience_text or job.min_years is not None

    def test_a_search_with_no_usable_source_returns_nothing(
        self, session, user, monkeypatch
    ) -> None:
        outcome = run_search(
            session, user, parse_query_rules(QUERY_TEXT), only_providers=["adzuna"]
        )
        assert outcome.job_ids == []
        assert outcome.providers_skipped
        assert outcome.status is not SearchRunStatus.SUCCESS


class TestValidationIsNotOptional:
    def test_a_posting_that_fails_validation_never_reaches_the_library(
        self, session, user, muse_only, monkeypatch
    ) -> None:
        from app.services import validation as validation_module
        from app.services.discovery import validate_posting  # noqa: F401

        def always_expired(client, **kwargs):
            return validation_module.ValidationOutcome(
                status=ValidationStatus.EXPIRED, reason="closed in this test"
            )

        monkeypatch.setattr("app.services.discovery.validate_posting", always_expired)
        outcome = _run(session, user)

        assert outcome.job_ids == []
        assert outcome.rejected >= 1
        assert session.scalars(select(Job)).all() == []
