"""The application tracker."""

from __future__ import annotations

from datetime import date, timedelta

from app.models.application import Application
from app.models.enums import ApplicationStatus, OutreachStatus
from app.services import applications as tracker
from tests.conftest import requires_db

pytestmark = requires_db


class TestSaving:
    def test_saving_snapshots_the_job_and_contact(self, session, user, job, contact) -> None:
        application = tracker.save_job(session, user, job, contact=contact)

        assert application.job_title == job.title
        assert application.company_name == job.company_name
        assert application.location == job.location
        assert application.job_url == job.job_url
        assert application.contact_name == contact.name
        assert application.contact_email == contact.email
        assert application.status is ApplicationStatus.SAVED
        assert job.is_saved

    def test_saving_twice_returns_the_same_row(self, session, user, job) -> None:
        first = tracker.save_job(session, user, job)
        second = tracker.save_job(session, user, job)
        assert first.id == second.id

    def test_saving_directly_as_applied_stamps_the_date(self, session, user, job) -> None:
        application = tracker.save_job(
            session, user, job, status=ApplicationStatus.APPLIED
        )
        assert application.date_applied is not None


class TestStatusTransitions:
    def test_the_full_pipeline_can_be_walked(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        for status in (
            ApplicationStatus.APPLIED,
            ApplicationStatus.OUTREACH_SENT,
            ApplicationStatus.INTERVIEW,
            ApplicationStatus.OFFER,
        ):
            change = tracker.set_status(session, application, status)
            assert change.current is status
        assert application.status is ApplicationStatus.OFFER

    def test_applying_stamps_the_date_once(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.set_status(session, application, ApplicationStatus.APPLIED)
        first = application.date_applied
        tracker.set_status(session, application, ApplicationStatus.INTERVIEW)
        assert application.date_applied == first

    def test_outreach_sent_sets_the_outreach_state_too(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.set_status(session, application, ApplicationStatus.OUTREACH_SENT)
        assert application.outreach_status is OutreachStatus.EMAIL_SENT
        assert application.outreach_sent_at is not None

    def test_every_change_is_recorded(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.set_status(session, application, ApplicationStatus.APPLIED)
        tracker.set_outreach_status(session, application, OutreachStatus.LINKEDIN_SENT)
        types = [event.event_type for event in application.events]
        assert "CREATED" in types
        assert "STATUS_CHANGED" in types
        assert "OUTREACH_CHANGED" in types

    def test_clearing_outreach_clears_its_timestamp(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.set_outreach_status(session, application, OutreachStatus.EMAIL_SENT)
        assert application.outreach_sent_at is not None
        tracker.set_outreach_status(session, application, OutreachStatus.NOT_STARTED)
        assert application.outreach_sent_at is None


class TestFollowUps:
    def test_a_past_date_on_an_open_application_is_due(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.update_fields(
            session, application, follow_up_date=date.today() - timedelta(days=1)
        )
        assert application.follow_up_due
        assert application in tracker.follow_ups_due(session, user)

    def test_a_future_date_is_not_due(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.update_fields(
            session, application, follow_up_date=date.today() + timedelta(days=7)
        )
        assert not application.follow_up_due

    def test_a_closed_application_is_never_due(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.update_fields(
            session, application, follow_up_date=date.today() - timedelta(days=3)
        )
        tracker.set_status(session, application, ApplicationStatus.REJECTED)
        assert not application.follow_up_due
        assert tracker.follow_ups_due(session, user) == []

    def test_a_follow_up_can_be_cleared(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.update_fields(session, application, follow_up_date=date.today())
        tracker.update_fields(session, application, clear_follow_up=True)
        assert application.follow_up_date is None


class TestDashboardCounts:
    def test_counts_reflect_the_pipeline(self, session, user, job, contact) -> None:
        application = tracker.save_job(session, user, job, contact=contact)
        counts = tracker.dashboard_counts(session, user)
        assert counts["jobs_found"] == 1
        assert counts["valid_jobs"] == 1
        assert counts["saved_jobs"] == 1
        assert counts["applications_submitted"] == 0

        tracker.set_status(session, application, ApplicationStatus.APPLIED)
        tracker.set_outreach_status(session, application, OutreachStatus.EMAIL_SENT)
        counts = tracker.dashboard_counts(session, user)
        assert counts["applications_submitted"] == 1
        assert counts["outreach_sent"] == 1

        tracker.set_status(session, application, ApplicationStatus.INTERVIEW)
        assert tracker.dashboard_counts(session, user)["interviews"] == 1

        tracker.set_status(session, application, ApplicationStatus.OFFER)
        assert tracker.dashboard_counts(session, user)["offers"] == 1

    def test_the_breakdown_covers_every_status(self, session, user) -> None:
        breakdown = tracker.pipeline_breakdown(session, user)
        assert set(breakdown) == {s.value for s in ApplicationStatus}


class TestRemoval:
    def test_removing_an_application_unsaves_the_job(self, session, user, job) -> None:
        application = tracker.save_job(session, user, job)
        tracker.delete_application(session, application)
        assert not job.is_saved
        assert session.get(Application, application.id) is None
