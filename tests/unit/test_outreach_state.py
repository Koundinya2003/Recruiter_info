"""Outreach state machine, approval gate and duplicate prevention."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.enums import OutreachStatus as S
from app.models.enums import ResponseStatus
from app.models.recruiter import Recruiter
from app.models.user import User
from app.services.outreach.service import (
    ALLOWED_TRANSITIONS,
    DoNotContactError,
    DuplicateLeadError,
    InvalidTransitionError,
    OutreachError,
    add_note,
    approve_draft,
    clear_do_not_contact,
    create_lead,
    edit_draft,
    record_follow_up,
    record_outreach,
    record_response,
    set_do_not_contact,
    transition,
)
from tests.conftest import requires_db

pytestmark = requires_db


def _lead(session: Session, user: User, recruiter: Recruiter, company: Company):
    lead = create_lead(session, user.id, recruiter, None, company)
    lead.draft_subject = "Hello"
    lead.draft_body = "A short, specific note about the role."
    session.flush()
    return lead


# --- The state machine itself ---------------------------------------------------


def test_transition_table_has_no_way_back_from_do_not_contact() -> None:
    assert ALLOWED_TRANSITIONS[S.DO_NOT_CONTACT] == set()
    # Every other state can reach DO_NOT_CONTACT.
    for state, allowed in ALLOWED_TRANSITIONS.items():
        if state is not S.DO_NOT_CONTACT:
            assert S.DO_NOT_CONTACT in allowed


def test_illegal_transition_is_refused(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    with pytest.raises(InvalidTransitionError, match="Cannot move"):
        transition(session, lead, S.REPLIED)
    assert lead.status is S.NEW


def test_contacted_cannot_be_set_by_hand(session, user, recruiter, company) -> None:
    """Marking CONTACTED must go through record_outreach so it is logged."""
    lead = _lead(session, user, recruiter, company)
    approve_draft(session, lead)
    with pytest.raises(OutreachError, match="record outreach"):
        transition(session, lead, S.CONTACTED)


def test_approval_is_required_before_the_approved_state(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    transition(session, lead, S.REVIEWED)
    with pytest.raises(OutreachError, match="Approve the email draft"):
        transition(session, lead, S.APPROVED)


# --- Approval gate --------------------------------------------------------------


def test_generated_draft_is_never_auto_approved(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    assert lead.draft_approved is False
    assert lead.ready_to_send is False


def test_approving_an_empty_draft_is_refused(session, user, recruiter, company) -> None:
    lead = create_lead(session, user.id, recruiter, None, company)
    with pytest.raises(OutreachError, match="no draft to approve"):
        approve_draft(session, lead)


def test_editing_a_draft_revokes_approval(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    approve_draft(session, lead)
    assert lead.draft_approved is True and lead.status is S.APPROVED

    edit_draft(session, lead, body="A different message entirely.")
    assert lead.draft_approved is False
    assert lead.status is S.REVIEWED


def test_outreach_requires_approval(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    with pytest.raises(OutreachError):
        record_outreach(session, lead)
    assert lead.contacted_at is None


# --- Duplicate prevention -------------------------------------------------------


def test_duplicate_lead_for_same_pair_is_refused(session, user, recruiter, company) -> None:
    create_lead(session, user.id, recruiter, None, company)
    with pytest.raises(DuplicateLeadError, match="already in the outreach queue"):
        create_lead(session, user.id, recruiter, None, company)


def test_duplicate_outreach_is_refused_with_an_accurate_reason(
    session, user, recruiter, company
) -> None:
    lead = _lead(session, user, recruiter, company)
    approve_draft(session, lead)
    record_outreach(session, lead)
    assert lead.contacted_at is not None
    assert lead.contact_count == 1

    with pytest.raises(DuplicateLeadError, match="already contacted"):
        record_outreach(session, lead)
    assert lead.contact_count == 1


def test_follow_up_is_the_supported_way_to_contact_again(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    approve_draft(session, lead)
    record_outreach(session, lead)
    first_contact = lead.contacted_at

    record_follow_up(session, lead, note="Checking in")
    assert lead.contact_count == 2
    assert lead.contacted_at == first_contact  # first-contact time is immutable
    assert lead.last_contact_at >= first_contact


def test_follow_up_before_first_contact_is_refused(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    with pytest.raises(OutreachError, match="not been contacted"):
        record_follow_up(session, lead)


# --- DO NOT CONTACT -------------------------------------------------------------


def test_do_not_contact_blocks_new_leads(session, user, recruiter, company) -> None:
    set_do_not_contact(session, recruiter, reason="Asked not to be contacted")
    assert recruiter.do_not_contact is True
    with pytest.raises(DoNotContactError):
        create_lead(session, user.id, recruiter, None, company)


def test_do_not_contact_retires_existing_leads(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    retired = set_do_not_contact(session, recruiter, reason="Requested removal")
    assert retired == 1
    assert lead.status is S.DO_NOT_CONTACT


def test_do_not_contact_blocks_approval_and_sending(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    set_do_not_contact(session, recruiter)
    with pytest.raises(DoNotContactError):
        approve_draft(session, lead)
    with pytest.raises(DoNotContactError):
        record_outreach(session, lead)


def test_clearing_do_not_contact_does_not_revive_old_leads(
    session, user, recruiter, company
) -> None:
    lead = _lead(session, user, recruiter, company)
    set_do_not_contact(session, recruiter)
    clear_do_not_contact(session, recruiter)
    assert recruiter.do_not_contact is False
    assert lead.status is S.DO_NOT_CONTACT  # deliberately stays retired


# --- History --------------------------------------------------------------------


def test_every_action_is_recorded_in_history(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    approve_draft(session, lead)
    record_outreach(session, lead)
    record_response(session, lead, ResponseStatus.POSITIVE)
    add_note(session, lead, "They asked for my résumé")

    session.refresh(lead)
    kinds = [event.event_type.value for event in lead.events]
    assert kinds[0] == "LEAD_CREATED"
    assert "DRAFT_APPROVED" in kinds
    assert "CONTACT_RECORDED" in kinds
    assert "RESPONSE_RECORDED" in kinds
    assert "NOTE_ADDED" in kinds
    assert lead.status is S.REPLIED
    assert lead.response_status is ResponseStatus.POSITIVE


def test_response_before_contact_is_refused(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    with pytest.raises(OutreachError, match="Record the outreach"):
        record_response(session, lead, ResponseStatus.POSITIVE)


def test_empty_note_is_refused(session, user, recruiter, company) -> None:
    lead = _lead(session, user, recruiter, company)
    with pytest.raises(OutreachError, match="empty"):
        add_note(session, lead, "   ")
