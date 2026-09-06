"""Search — describe the roles you want, in your own words."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import APIError, get_client  # noqa: E402
from frontend.components.job_card import render_job_card  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    empty_state,
    escape,
    header,
    page_setup,
    show_error,
    sidebar_status,
)

page_setup("Search", "🔎")
client = get_client()
health = sidebar_status(client)

header(
    "Find real, open roles",
    "Describe what you are looking for. Every posting shown here was retrieved from a "
    "public job source and checked before it reached this page.",
)

if health is None:
    st.stop()

EXAMPLE = (
    "Find Associate Product Manager roles for 0-2 years of experience in "
    "Bangalore and Hyderabad."
)

st.session_state.setdefault("query_text", EXAMPLE)
st.session_state.setdefault("parsed", None)
st.session_state.setdefault("result", None)
st.session_state.setdefault("jobs", [])


def _sync_query() -> None:
    st.session_state.parsed = None


# --- The request --------------------------------------------------------------
query_text = st.text_area(
    "What are you looking for?",
    key="query_text",
    height=90,
    placeholder=EXAMPLE,
    on_change=_sync_query,
    help="Plain English works. Role, experience, locations, companies, skills — any of them.",
)

controls = st.columns([1, 1, 1, 2])
with controls[0]:
    limit = st.number_input("Max results", min_value=5, max_value=120, value=40, step=5)
with controls[1]:
    find_contacts = st.toggle("Find contacts", value=True, help="Look for people to contact at each company.")
with controls[2]:
    use_llm = st.toggle(
        "Use AI to read the request",
        value=True,
        help="Rules read your request first either way; AI only fills gaps they leave.",
    )

button_row = st.columns([1, 1, 4])
with button_row[0]:
    check_clicked = st.button("Check understanding", use_container_width=True)
with button_row[1]:
    search_clicked = st.button("Search", type="primary", use_container_width=True)

if check_clicked and query_text.strip():
    try:
        st.session_state.parsed = client.parse_query(query_text, use_llm=use_llm)
    except APIError as exc:
        show_error(exc)

# --- What the request was understood to mean ----------------------------------
parsed = st.session_state.parsed
overrides: dict = {}

if parsed:
    with st.container(border=True):
        st.markdown("**Understood as:** " + escape(parsed["summary"]))
        st.caption(
            f"Read with {parsed['parse_method']}. Correct anything below before searching."
        )
        fields = st.columns(3)
        with fields[0]:
            titles = st.text_input("Role titles", ", ".join(parsed["titles"]))
            locations = st.text_input("Locations", ", ".join(parsed["locations"]))
        with fields[1]:
            companies = st.text_input("Companies", ", ".join(parsed["companies"]))
            keywords = st.text_input("Skills / keywords", ", ".join(parsed["keywords"]))
        with fields[2]:
            years = st.slider(
                "Years of experience",
                0.0,
                20.0,
                (
                    float(parsed["min_years"] if parsed["min_years"] is not None else 0.0),
                    float(parsed["max_years"] if parsed["max_years"] is not None else 20.0),
                ),
                step=0.5,
            )
            remote_only = st.toggle("Remote only", value=parsed["remote_only"])

        def _split(value: str) -> list[str]:
            return [v.strip() for v in value.split(",") if v.strip()]

        overrides = {
            "titles": _split(titles),
            "locations": _split(locations),
            "companies": _split(companies),
            "keywords": _split(keywords),
            "min_years": years[0],
            "max_years": years[1] if years[1] < 20.0 else None,
            "remote_only": remote_only,
        }
        for note in parsed.get("parse_notes") or []:
            st.caption(f"· {note}")

# --- Run ----------------------------------------------------------------------
if search_clicked and query_text.strip():
    payload = {
        "query": query_text,
        "limit": int(limit),
        "find_contacts": find_contacts,
        "use_llm": use_llm,
        **overrides,
    }
    with st.spinner(
        "Querying job sources, checking each posting is live, and looking for contacts…"
    ):
        try:
            result = client.run_search(payload)
            st.session_state.result = result
            st.session_state.jobs = client.search_jobs(result["search_id"])
        except APIError as exc:
            show_error(exc)
            st.session_state.result = None

result = st.session_state.result
jobs = st.session_state.jobs


# --- Actions ------------------------------------------------------------------
def _save(job: dict, contact: dict | None, status: str = "SAVED") -> None:
    try:
        client.create_application(
            {
                "job_id": job["id"],
                "contact_id": contact["id"] if contact else None,
                "status": status,
            }
        )
        st.toast(f"{job['title']} saved to your tracker.")
        st.session_state.jobs = client.search_jobs(result["search_id"]) if result else jobs
    except APIError as exc:
        show_error(exc)


def _mark_applied(job: dict, contact: dict | None) -> None:
    try:
        application = client.create_application(
            {
                "job_id": job["id"],
                "contact_id": contact["id"] if contact else None,
                "status": "SAVED",
            }
        )
        client.mark_applied(application["id"])
        st.toast(f"Marked {job['title']} as applied.")
        st.session_state.jobs = client.search_jobs(result["search_id"]) if result else jobs
    except APIError as exc:
        show_error(exc)


def _dismiss(job: dict) -> None:
    try:
        client.update_job(job["id"], {"dismissed": True})
        st.session_state.jobs = [j for j in st.session_state.jobs if j["id"] != job["id"]]
        st.rerun()
    except APIError as exc:
        show_error(exc)


def _rediscover(job_id: int) -> None:
    try:
        with st.spinner("Looking for published contacts…"):
            client.discover_contacts(job_id)
        st.session_state.jobs = client.search_jobs(result["search_id"]) if result else jobs
        st.rerun()
    except APIError as exc:
        show_error(exc)


# --- Results ------------------------------------------------------------------
if result:
    funnel = result["funnel"]
    with st.container(border=True):
        st.markdown(f"**{escape(result['query']['summary'])}**")
        counts = st.columns(5)
        counts[0].metric("Retrieved", funnel["raw_found"])
        counts[1].metric("Duplicates", funnel["duplicates_dropped"])
        counts[2].metric("Off-target", funnel["irrelevant_dropped"])
        counts[3].metric("Failed checks", funnel["rejected"])
        counts[4].metric("Shown", funnel["validated"] + funnel["unverified"])

        if funnel["unverified"]:
            st.caption(
                f"{funnel['unverified']} of these could not be independently confirmed — "
                "they are marked, not hidden."
            )
        if result["providers_queried"]:
            st.caption("Sources queried: " + ", ".join(result["providers_queried"]))
        if result["providers_skipped"]:
            with st.expander(f"{len(result['providers_skipped'])} source(s) were not used"):
                for name, reason in result["providers_skipped"].items():
                    st.markdown(f"**{escape(name)}** — {escape(reason)}")
        if result["errors"]:
            with st.expander(f"{len(result['errors'])} source problem(s)"):
                for error in result["errors"]:
                    st.markdown(f"· {escape(error)}")
        if result["notes"]:
            with st.expander("What happened during this search"):
                for note in result["notes"]:
                    st.markdown(f"· {escape(note)}")

if jobs:
    st.markdown(f"## {len(jobs)} posting(s)")
    for job in jobs:
        render_job_card(
            job,
            on_save=_save,
            on_mark_applied=_mark_applied,
            on_dismiss=_dismiss,
            on_rediscover=_rediscover,
        )
elif result:
    empty_state(
        "No postings survived the checks",
        "Every result was a duplicate, off-target, or failed validation. Try widening the "
        "locations or the experience range, or configure another source.",
    )
elif not search_clicked:
    empty_state(
        "Describe what you are looking for",
        "For example: “Associate Product Manager roles for 0–2 years of experience in "
        "Bangalore and Hyderabad”.",
    )

# --- Recent searches ----------------------------------------------------------
with st.sidebar:
    st.markdown("### Recent searches")
    try:
        for saved in client.search_history(limit=8):
            if st.button(saved["raw_query"][:60], key=f"hist-{saved['id']}", use_container_width=True):
                st.session_state.query_text = saved["raw_query"]
                st.session_state.parsed = None
                st.rerun()
    except APIError:
        st.caption("Search history unavailable.")
