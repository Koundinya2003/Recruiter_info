"""Jobs: filtering, sorting and the relevance explanation."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    badge,
    demo_badge,
    empty_state,
    format_dt,
    header,
    humanize_hours,
    page_setup,
    render_breakdown,
    show_error,
    sidebar_status,
)

page_setup("Jobs", "💼")
client = get_client()
if sidebar_status(client) is None:
    st.stop()

header(
    "Jobs",
    "Every discovered role, scored against your target profile. The score is never a black "
    "box — open any job to see each component and the reason behind it.",
)

try:
    companies = client.companies(limit=200)
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()

company_names = {c["id"]: c["company_name"] for c in companies}

with st.sidebar:
    st.markdown("### Filters")
    query = st.text_input("Search", placeholder="title, description or company")
    company_choice = st.selectbox(
        "Company", ["All companies", *company_names.values()], index=0
    )
    status = st.selectbox("Status", ["All", "OPEN", "CLOSED", "STALE"], index=1)
    min_relevance = st.slider("Minimum relevance", 0, 100, 0, step=5)
    freshness = st.selectbox(
        "Freshness", ["Any", "Last 24 hours", "Last 3 days", "Last 7 days", "Last 30 days"]
    )
    location = st.text_input("Location contains", placeholder="Bangalore, Remote…")
    sort = st.selectbox("Sort by", ["relevance", "posted", "discovered", "title"])
    include_demo = st.toggle("Include demo data", value=True)

company_id = None
if company_choice != "All companies":
    company_id = next((cid for cid, name in company_names.items() if name == company_choice), None)

max_age = {
    "Any": None,
    "Last 24 hours": 24,
    "Last 3 days": 72,
    "Last 7 days": 168,
    "Last 30 days": 720,
}[freshness]

try:
    jobs = client.jobs(
        q=query or None,
        company_id=company_id,
        status=None if status == "All" else status,
        min_relevance=min_relevance or None,
        max_age_hours=max_age,
        location=location or None,
        sort=sort,
        include_demo=include_demo,
        limit=100,
    )
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()

if not jobs:
    empty_state(
        "No jobs match these filters",
        "Either the filters are too tight, or no company has been scanned yet.",
        "Try lowering the minimum relevance, or scan a company on the Companies page.",
    )
    st.stop()

top = sum(1 for j in jobs if j["relevance_score"] >= 80)
st.caption(f"{len(jobs)} jobs · {top} scoring 80 or above")

for job in jobs:
    with st.container(border=True):
        cols = st.columns([3.4, 1.6, 1.3, 1.1, 1.0])
        with cols[0]:
            demo = " " + demo_badge() if job["is_demo"] else ""
            st.markdown(f"**[{job['title']}]({job['job_url']})**{demo}", unsafe_allow_html=True)
            st.markdown(
                f"<span class='roi-meta'>{company_names.get(job['company_id'], '—')} · "
                f"{job.get('employment_type') or 'type not stated'} · source {job['source']}</span>",
                unsafe_allow_html=True,
            )
        cols[1].markdown(
            f"<span class='roi-meta'>{job.get('location') or 'Location not stated'}</span>",
            unsafe_allow_html=True,
        )
        with cols[2]:
            posted = job.get("posted_at")
            st.markdown(
                f"<span class='roi-meta'>{format_dt(posted, '%d %b %Y') if posted else 'Date unknown'}"
                f"<br>discovered {format_dt(job['discovered_at'], '%d %b')}</span>",
                unsafe_allow_html=True,
            )
        with cols[3]:
            score = job["relevance_score"]
            style = "roi-b-good" if score >= 80 else "roi-b-info" if score >= 60 else "roi-b-mute"
            st.markdown(
                f"**{score:.0f}**/100 {badge(job['status'], style)}", unsafe_allow_html=True
            )
        with cols[4]:
            if st.button("Why?", key=f"why_{job['id']}", use_container_width=True):
                st.session_state["open_job"] = (
                    None if st.session_state.get("open_job") == job["id"] else job["id"]
                )

        if st.session_state.get("open_job") == job["id"]:
            try:
                detail = client.job(job["id"])
            except Exception as exc:  # noqa: BLE001
                show_error(exc)
                continue

            st.divider()
            left, right = st.columns([3, 2])
            with left:
                st.markdown("#### Why this score")
                render_breakdown(detail["relevance"])
            with right:
                st.markdown("#### Linked recruiters")
                if not detail["linked_recruiters"]:
                    st.caption(
                        "No recruiter linked to this role yet. Scan the company with recruiter "
                        "discovery enabled."
                    )
                for link in detail["linked_recruiters"]:
                    st.markdown(
                        f"**{link['name']}** · {link.get('title') or '—'} · "
                        f"{link['recruiter_score']:.0f}/100"
                    )
                    st.markdown(
                        f"<span class='roi-meta'>{link['rationale'] or link['relation']} "
                        f"(confidence {link['confidence']:.0%})</span>",
                        unsafe_allow_html=True,
                    )
                    if link.get("do_not_contact"):
                        st.markdown(badge("DO NOT CONTACT", "roi-b-bad"), unsafe_allow_html=True)

                st.markdown("#### Provenance")
                st.markdown(
                    f"<div class='roi-meta'>Source: <strong>{detail['source']}</strong><br>"
                    f"Canonical URL: <span class='roi-mono'>{detail['canonical_url'][:80]}</span><br>"
                    f"Dedupe hash: <span class='roi-mono'>{detail['content_hash'][:16]}…</span><br>"
                    f"Age: {humanize_hours(detail.get('age_hours'))}</div>",
                    unsafe_allow_html=True,
                )

            if detail.get("description"):
                with st.expander("Job description as collected"):
                    st.write(detail["description"][:4000])
