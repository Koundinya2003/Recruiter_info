"""Dashboard — where everything stands right now.

Run with:  streamlit run frontend/Dashboard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frontend.api_client import APIError, get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    application_badge,
    empty_state,
    escape,
    format_dt,
    header,
    kpi,
    outreach_badge,
    page_setup,
    show_error,
    sidebar_status,
    validation_badge,
)

page_setup("Dashboard")
client = get_client()
health = sidebar_status(client)

header(
    "Your job search, end to end",
    "Search → review a validated posting → apply → contact someone → follow up. "
    "Nothing here applies or sends a message for you.",
)

if health is None:
    st.stop()

try:
    data = client.dashboard(limit=8)
except APIError as exc:
    show_error(exc)
    st.stop()

# --- Headline numbers ---------------------------------------------------------
row = st.columns(4)
tiles = [
    ("Jobs found", data["jobs_found"], "Postings that passed validation"),
    ("Valid jobs", data["valid_jobs"], "Confirmed reachable and open"),
    ("Saved jobs", data["saved_jobs"], "In your tracker"),
    ("Applications", data["applications_submitted"], "Marked as applied"),
]
for column, (label, value, hint) in zip(row, tiles, strict=True):
    with column:
        st.markdown(kpi(label, value, hint), unsafe_allow_html=True)

row = st.columns(4)
tiles = [
    ("Outreach sent", data["outreach_sent"], "People you have contacted"),
    ("Interviews", data["interviews"], "Currently interviewing"),
    ("Offers", data["offers"], "Offers received"),
    ("Follow-ups due", data["follow_ups_due"], "Due today or overdue"),
]
for column, (label, value, hint) in zip(row, tiles, strict=True):
    with column:
        st.markdown(kpi(label, value, hint), unsafe_allow_html=True)

if data["jobs_found"] == 0 and data["applications_submitted"] == 0:
    st.markdown("")
    empty_state(
        "Nothing found yet",
        "Open Search and describe the roles you want — for example, "
        "“Associate Product Manager roles for 0–2 years of experience in Bangalore "
        "and Hyderabad”.",
    )
    if data.get("sources_usable", 0) == 0:
        st.warning(
            "No job source is usable yet. Several need no credentials at all — "
            "open **Sources** to switch one on."
        )
    st.stop()

# --- Follow-ups ---------------------------------------------------------------
left, right = st.columns([3, 2], gap="large")

with left:
    st.markdown("## Due now")
    due = data.get("due_follow_ups") or []
    if not due:
        st.caption("No follow-ups are due. Set a follow-up date on the Applications page.")
    for application in due:
        with st.container(border=True):
            st.markdown(
                f"**{escape(application['job_title'])}** · {escape(application['company_name'])}",
            )
            st.markdown(
                application_badge(application["status"], application["status_label"])
                + outreach_badge(
                    application["outreach_status"], application["outreach_status_label"]
                ),
                unsafe_allow_html=True,
            )
            st.caption(
                f"Follow up due {format_dt(application['follow_up_date'])}"
                + (
                    f" · contact {application['contact_name']}"
                    if application.get("contact_name")
                    else ""
                )
            )

    st.markdown("## Recently found")
    recent = data.get("recent_jobs") or []
    if not recent:
        st.caption("No postings in your library yet.")
    for job in recent:
        with st.container(border=True):
            top, action = st.columns([4, 1])
            with top:
                st.markdown(
                    f"**{escape(job['title'])}** · {escape(job['company_name'])}"
                    f"<div class='jw-meta'>{escape(job.get('location') or 'Location not stated')}"
                    f" · {escape(job.get('experience_label'))}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(validation_badge(job.get("validation") or {}), unsafe_allow_html=True)
            with action:
                st.link_button("Open", job["job_url"], use_container_width=True)

with right:
    st.markdown("## Pipeline")
    pipeline = data.get("pipeline") or {}
    order = [
        "SAVED",
        "APPLIED",
        "OUTREACH_SENT",
        "INTERVIEW",
        "OFFER",
        "REJECTED",
        "CLOSED",
    ]
    for status in order:
        count = pipeline.get(status, 0)
        st.markdown(
            f"{application_badge(status)} &nbsp; **{count}**",
            unsafe_allow_html=True,
        )

    st.markdown("## Last search")
    last = data.get("last_search")
    if not last:
        st.caption("You have not run a search yet.")
    else:
        st.markdown(f"**{escape(last['query'])}**")
        st.caption(f"Run {format_dt(last['last_run_at'], '%d %b %Y, %H:%M')} · {last['status']}")
        st.markdown(
            f"<div class='jw-meta'>"
            f"{last['raw_found']} postings retrieved → "
            f"{last['duplicates_dropped']} duplicates, "
            f"{last['irrelevant_dropped']} off-target, "
            f"{last['rejected']} failed validation → "
            f"<b>{last['kept']} shown</b></div>",
            unsafe_allow_html=True,
        )

    st.markdown("## Sources")
    st.caption(
        f"{data.get('sources_usable', 0)} of {data.get('sources_total', 0)} job sources are "
        "ready to query."
    )
