"""Jobs — everything found so far, with the contacts for each."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import APIError, get_client  # noqa: E402
from frontend.components.job_card import render_job_card  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    empty_state,
    header,
    page_setup,
    show_error,
    sidebar_status,
)

page_setup("Jobs", "📋")
client = get_client()
health = sidebar_status(client)

header(
    "Your job library",
    "Every validated posting from every search, with the people you could contact about it.",
)

if health is None:
    st.stop()

with st.sidebar:
    st.markdown("### Filter")
    search_term = st.text_input("Title, company or location")
    company = st.text_input("Company")
    saved_only = st.toggle("Saved only", value=False)
    confirmed_only = st.toggle(
        "Confirmed only",
        value=False,
        help="Hide postings the source would not let us re-check.",
    )
    show_contacts = st.toggle("Show contacts", value=True)
    limit = st.slider("Show at most", 10, 200, 50, step=10)

try:
    jobs = client.jobs(
        q=search_term or None,
        company=company or None,
        saved_only=saved_only,
        confirmed_only=confirmed_only,
        limit=limit,
    )
    counts = client.job_counts()
except APIError as exc:
    show_error(exc)
    st.stop()

summary = st.columns(3)
summary[0].metric("In library", counts["total"])
summary[1].metric("Confirmed open", counts["confirmed"])
summary[2].metric("Showing", len(jobs))


def _reload() -> None:
    st.rerun()


def _save(job: dict, contact: dict | None) -> None:
    try:
        client.create_application(
            {"job_id": job["id"], "contact_id": contact["id"] if contact else None}
        )
        st.toast(f"{job['title']} saved.")
        _reload()
    except APIError as exc:
        show_error(exc)


def _mark_applied(job: dict, contact: dict | None) -> None:
    try:
        application = client.create_application(
            {"job_id": job["id"], "contact_id": contact["id"] if contact else None}
        )
        client.mark_applied(application["id"])
        st.toast(f"Marked {job['title']} as applied.")
        _reload()
    except APIError as exc:
        show_error(exc)


def _dismiss(job: dict) -> None:
    try:
        client.update_job(job["id"], {"dismissed": True})
        _reload()
    except APIError as exc:
        show_error(exc)


def _rediscover(job_id: int) -> None:
    try:
        with st.spinner("Looking for published contacts…"):
            client.discover_contacts(job_id)
        _reload()
    except APIError as exc:
        show_error(exc)


if not jobs:
    empty_state(
        "Nothing here yet",
        "Run a search, or relax the filters in the sidebar.",
    )
else:
    for job in jobs:
        render_job_card(
            job,
            on_save=_save,
            on_mark_applied=_mark_applied,
            on_dismiss=_dismiss,
            on_rediscover=_rediscover,
            show_contacts=show_contacts,
        )
        with st.expander("Re-check this posting"):
            st.caption(
                "Worth doing before you apply to an older find — postings come down "
                "without notice."
            )
            if st.button("Re-check now", key=f"revalidate-{job['id']}"):
                try:
                    outcome = client.revalidate_job(job["id"])
                    st.success(f"{outcome['label']} — {outcome.get('reason') or ''}")
                    _reload()
                except APIError as exc:
                    show_error(exc)
