"""Applications — the tracker for everything you have applied to."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import APIError, get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    application_badge,
    empty_state,
    escape,
    format_dt,
    header,
    outreach_badge,
    page_setup,
    show_error,
    sidebar_status,
)

page_setup("Applications", "🗂️")
client = get_client()
health = sidebar_status(client)

header(
    "Application tracker",
    "Every role you are working on, its contact, and what happens next. "
    "Statuses only move when you move them.",
)

if health is None:
    st.stop()

STATUSES = [
    "SAVED",
    "APPLIED",
    "OUTREACH_SENT",
    "INTERVIEW",
    "REJECTED",
    "OFFER",
    "CLOSED",
]
OUTREACH = ["NOT_STARTED", "EMAIL_SENT", "LINKEDIN_SENT", "REPLIED", "NO_RESPONSE"]

with st.sidebar:
    st.markdown("### Filter")
    status_filter = st.selectbox("Status", ["All", *STATUSES])
    open_only = st.toggle("Open only", value=False)
    due_only = st.toggle("Follow-up due", value=False)
    term = st.text_input("Role or company")

try:
    applications = client.applications(
        status=None if status_filter == "All" else status_filter,
        open_only=open_only,
        due_only=due_only,
        q=term or None,
    )
except APIError as exc:
    show_error(exc)
    st.stop()

if not applications:
    empty_state(
        "No applications tracked yet",
        "Save a job from Search or Jobs and it will appear here.",
    )
    st.stop()

counts = st.columns(4)
counts[0].metric("Tracked", len(applications))
counts[1].metric("Applied", sum(1 for a in applications if a["date_applied"]))
counts[2].metric("Outreach sent", sum(1 for a in applications if a["outreach_sent_at"]))
counts[3].metric("Follow-ups due", sum(1 for a in applications if a["follow_up_due"]))

st.markdown("")

for application in applications:
    with st.container(border=True):
        top, actions = st.columns([3, 2], gap="medium")

        with top:
            st.markdown(
                f"<div class='jw-title'>{escape(application['job_title'])}</div>"
                f"<div class='jw-company'>{escape(application['company_name'])}</div>"
                f"<div class='jw-meta'>{escape(application.get('location') or 'Location not stated')}"
                f" · {escape(application.get('source') or 'source not recorded')}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                application_badge(application["status"], application["status_label"])
                + outreach_badge(
                    application["outreach_status"], application["outreach_status_label"]
                )
                + (
                    "<span class='jw-badge jw-b-warn'>Follow-up due</span>"
                    if application["follow_up_due"]
                    else ""
                ),
                unsafe_allow_html=True,
            )

            dates = st.columns(3)
            dates[0].caption(f"Found {format_dt(application['date_found'])}")
            dates[1].caption(
                f"Applied {format_dt(application['date_applied'])}"
                if application["date_applied"]
                else "Not applied yet"
            )
            dates[2].caption(
                f"Follow up {format_dt(application['follow_up_date'])}"
                if application["follow_up_date"]
                else "No follow-up set"
            )

            if application.get("contact_name"):
                st.markdown(
                    f"**Contact:** {escape(application['contact_name'])}"
                    + (
                        f" — {escape(application['contact_title'])}"
                        if application.get("contact_title")
                        else ""
                    ),
                )
                if application.get("contact_email"):
                    st.markdown(
                        f"<span class='jw-mono'>{escape(application['contact_email'])}</span>",
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No contact attached.")

            links = st.columns(3)
            with links[0]:
                st.link_button("View job", application["job_url"], use_container_width=True)
            with links[1]:
                if application.get("contact_profile_url"):
                    st.link_button(
                        "LinkedIn", application["contact_profile_url"], use_container_width=True
                    )
            with links[2]:
                if application.get("contact_email"):
                    with st.popover("Copy email", use_container_width=True):
                        st.code(application["contact_email"], language=None)

        with actions:
            with st.form(f"update-{application['id']}"):
                status = st.selectbox(
                    "Status",
                    STATUSES,
                    index=STATUSES.index(application["status"]),
                    key=f"status-{application['id']}",
                )
                outreach = st.selectbox(
                    "Outreach",
                    OUTREACH,
                    index=OUTREACH.index(application["outreach_status"]),
                    key=f"outreach-{application['id']}",
                )
                current_follow_up = application.get("follow_up_date")
                follow_up = st.date_input(
                    "Follow up on",
                    value=date.fromisoformat(current_follow_up) if current_follow_up else None,
                    key=f"followup-{application['id']}",
                )
                notes = st.text_area(
                    "Notes",
                    value=application.get("notes") or "",
                    key=f"notes-{application['id']}",
                    height=80,
                )
                if st.form_submit_button("Save changes", use_container_width=True):
                    payload: dict = {
                        "status": status,
                        "outreach_status": outreach,
                        "notes": notes,
                    }
                    if follow_up:
                        payload["follow_up_date"] = follow_up.isoformat()
                    elif current_follow_up:
                        payload["clear_follow_up"] = True
                    try:
                        client.update_application(application["id"], payload)
                        st.toast("Application updated.")
                        st.rerun()
                    except APIError as exc:
                        show_error(exc)

            quick = st.columns(2)
            with quick[0]:
                if st.button("Mark applied", key=f"apply-{application['id']}", use_container_width=True):
                    try:
                        client.mark_applied(application["id"])
                        st.rerun()
                    except APIError as exc:
                        show_error(exc)
            with quick[1]:
                if st.button(
                    "Outreach sent", key=f"outreach-btn-{application['id']}", use_container_width=True
                ):
                    try:
                        client.mark_outreach_sent(application["id"])
                        st.rerun()
                    except APIError as exc:
                        show_error(exc)

            if st.button("Remove", key=f"delete-{application['id']}", use_container_width=True):
                try:
                    client.delete_application(application["id"])
                    st.rerun()
                except APIError as exc:
                    show_error(exc)

        with st.expander("History"):
            try:
                for event in client.application_history(application["id"]):
                    st.markdown(
                        f"<div class='jw-meta'>{format_dt(event['created_at'], '%d %b %Y, %H:%M')}"
                        f" — <b>{escape(event['event_type'])}</b> {escape(event.get('detail') or '')}"
                        "</div>",
                        unsafe_allow_html=True,
                    )
            except APIError as exc:
                show_error(exc)
