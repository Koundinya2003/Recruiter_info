"""Recruiter Outreach Intelligence — dashboard.

Run with:  streamlit run frontend/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frontend.api_client import get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    demo_badge,
    email_badge,
    empty_state,
    header,
    humanize_hours,
    kpi,
    page_setup,
    priority_badge,
    reasons_list,
    render_breakdown,
    score_bar,
    show_error,
    sidebar_status,
)

page_setup("Dashboard")
client = get_client()
health = sidebar_status(client)

header(
    "Who should I contact today?",
    "Ranked by job relevance, hiring freshness, recruiter fit, email confidence and your "
    "company priorities. Every score below can be opened up and explained.",
)

if health is None:
    st.stop()

with st.sidebar:
    st.markdown("### View")
    limit = st.slider("Opportunities to show", 5, 50, 15, step=5)
    include_demo = st.toggle("Include demo data", value=True)
    if st.button("Refresh", use_container_width=True):
        st.rerun()

try:
    data = client.dashboard(limit=limit, include_demo=include_demo)
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()

stats = data["stats"]
opportunities = data["opportunities"]

# --- Overview -----------------------------------------------------------------
st.markdown("## Overview")
cols = st.columns(5)
tiles = [
    ("Hiring signals today", stats["hiring_signals_today"], "detected in the last 24h"),
    ("Relevant jobs", stats["relevant_jobs"], "open and above your threshold"),
    ("Recruiters found", stats["recruiters_found"], "from public sources"),
    ("Verified emails", stats["verified_emails"], "checked and valid"),
    ("High priority leads", stats["high_priority_leads"], "scoring 85+ right now"),
]
for col, (label, value, hint) in zip(cols, tiles, strict=True):
    col.markdown(kpi(label, value, hint), unsafe_allow_html=True)

secondary = st.columns(5)
extra = [
    ("Companies tracked", stats["companies_tracked"], "active and paused"),
    ("Jobs tracked", stats["jobs_tracked"], "all statuses"),
    ("Awaiting approval", stats["leads_awaiting_approval"], "in the outreach queue"),
    ("Contacted this week", stats["contacted_this_week"], "recorded by you"),
    ("Inferred addresses", stats["inferred_emails"], "guessed — never verified"),
]
for col, (label, value, hint) in zip(secondary, extra, strict=True):
    col.markdown(kpi(label, value, hint), unsafe_allow_html=True)

if stats.get("demo_mode"):
    st.info(
        "Demo mode is active. Records marked **[DEMO]** are synthetic and use the reserved "
        "`.example` domain, so nothing here can reach a real person. "
        "Clear it with `python scripts/seed_demo.py --clear`.",
        icon="🧪",
    )

# --- Today's opportunities -----------------------------------------------------
st.markdown("## Today's opportunities")

if not opportunities:
    empty_state(
        "Nothing to contact yet",
        "This list fills up when a tracked company has a relevant, recently-posted role "
        "<em>and</em> a recruiter discovered from a public source.",
        "Add a company on the Companies page, then run a scan — or load demo data with "
        "<code>python scripts/seed_demo.py</code>.",
    )
else:
    st.caption(
        f"{len(opportunities)} opportunities. Highest value first — open one to see exactly "
        "why it ranks where it does."
    )
    head = st.columns([2.1, 2.3, 1.0, 1.9, 0.9, 1.5, 1.2, 0.9])
    for col, title in zip(
        head,
        ["Company", "Role", "Posted", "Recruiter", "Score", "Email", "Priority", ""],
        strict=True,
    ):
        col.markdown(f"<div class='roi-kpi-label'>{title}</div>", unsafe_allow_html=True)

    for opp in opportunities:
        row = st.columns([2.1, 2.3, 1.0, 1.9, 0.9, 1.5, 1.2, 0.9])
        with row[0]:
            st.markdown(f"**{opp['company_name']}**")
            meta = opp.get("industry") or "—"
            st.markdown(
                f"<span class='roi-meta'>{meta}</span>"
                + (" " + demo_badge() if opp.get("is_demo") else ""),
                unsafe_allow_html=True,
            )
        with row[1]:
            title = opp["job_title"] or "—"
            if opp.get("job_url"):
                st.markdown(f"[{title}]({opp['job_url']})")
            else:
                st.markdown(title)
            st.markdown(
                f"<span class='roi-meta'>Job relevance {opp['job_relevance']:.0f}/100</span>",
                unsafe_allow_html=True,
            )
        row[2].markdown(
            f"<span class='roi-meta'>{humanize_hours(opp.get('job_age_hours'))}</span>",
            unsafe_allow_html=True,
        )
        with row[3]:
            st.markdown(f"**{opp['recruiter_name']}**")
            st.markdown(
                f"<span class='roi-meta'>{opp.get('recruiter_title') or '—'}</span>",
                unsafe_allow_html=True,
            )
        row[4].markdown(f"**{opp['recruiter_score']:.0f}**")
        row[5].markdown(
            email_badge(
                opp.get("email"),
                verified=opp["email_verified"],
                status=opp["email_status"],
                is_inferred=opp["email_is_inferred"],
                confidence=opp["email_confidence"],
            ),
            unsafe_allow_html=True,
        )
        with row[6]:
            st.markdown(
                f"**{opp['outreach_priority']:.0f}** {priority_badge(opp['band'])}",
                unsafe_allow_html=True,
            )
        with row[7]:
            key = f"view_{opp['recruiter_id']}_{opp.get('job_id')}"
            if st.button("View", key=key, use_container_width=True):
                st.session_state["open_opportunity"] = key

        if st.session_state.get("open_opportunity") == f"view_{opp['recruiter_id']}_{opp.get('job_id')}":
            with st.container(border=True):
                left, right = st.columns([3, 2])
                with left:
                    st.markdown(f"### {opp['job_title']} · {opp['company_name']}")
                    st.markdown("**Why this is ranked here**")
                    reasons_list(opp["reasons"], limit=8)
                    if opp.get("gaps"):
                        st.markdown("**What is holding it back**")
                        for gap in opp["gaps"][:5]:
                            st.markdown(
                                f"<div class='roi-gap'>✗ {gap}</div>", unsafe_allow_html=True
                            )
                with right:
                    st.markdown(score_bar(opp["outreach_priority"], "Outreach priority"), unsafe_allow_html=True)
                    st.markdown(score_bar(opp["recruiter_score"], "Recruiter relevance"), unsafe_allow_html=True)
                    st.markdown(score_bar(opp["job_relevance"] or 0, "Job relevance"), unsafe_allow_html=True)
                    st.markdown("&nbsp;", unsafe_allow_html=True)
                    st.markdown(f"**Contact:** `{opp.get('email') or 'no address found'}`")
                    if opp["email_is_inferred"]:
                        st.warning(
                            "This address was **inferred from a name pattern**. It is a guess, "
                            "not something the company published. Verify it before use.",
                            icon="⚠️",
                        )
                    if opp.get("existing_lead_id"):
                        st.info(
                            f"Already in your queue as lead #{opp['existing_lead_id']} "
                            f"({opp['existing_lead_status']}).",
                            icon="📋",
                        )
                    elif st.button(
                        "Add to outreach queue",
                        key=f"add_{opp['recruiter_id']}_{opp.get('job_id')}",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            lead = client.create_lead(opp["recruiter_id"], opp.get("job_id"))
                            st.success(
                                f"Added as lead #{lead['id']}. Open the Outreach page to draft, "
                                "review and approve the email."
                            )
                        except Exception as exc:  # noqa: BLE001
                            show_error(exc)

                with st.expander("Full priority breakdown"):
                    render_breakdown(opp.get("breakdown", {}))
