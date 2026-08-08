"""Settings: role taxonomy and scoring weights, both editable from the UI."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    badge,
    header,
    page_setup,
    show_error,
    sidebar_status,
)

page_setup("Settings", "⚙️")
client = get_client()
if sidebar_status(client) is None:
    st.stop()

header(
    "Settings",
    "The role taxonomy and every scoring weight live in the database, not in code. "
    "Change them here and re-score.",
)

tab_taxonomy, tab_scoring = st.tabs(["Role taxonomy", "Scoring weights"])

KINDS = ["ROLE", "INDUSTRY", "SKILL", "LOCATION", "SENIORITY", "EXCLUDE"]
KIND_HELP = {
    "ROLE": "Job titles you want. Drives the 0-30 role-match component.",
    "INDUSTRY": "Domains you prefer. Drives the 0-20 industry component.",
    "SKILL": "Skills to look for in a description. Drives the 0-15 skill component.",
    "LOCATION": "Places you would work, including Remote. Drives the 0-10 location component.",
    "SENIORITY": "Optional seniority hints.",
    "EXCLUDE": "Terms that disqualify a job outright, with a stated reason.",
}

with tab_taxonomy:
    try:
        terms = client.taxonomy()
    except Exception as exc:  # noqa: BLE001
        show_error(exc)
        st.stop()

    cols = st.columns([1, 1])
    with cols[0]:
        st.markdown("### Add a term")
        with st.form("add_term"):
            kind = st.selectbox("Kind", KINDS)
            st.caption(KIND_HELP[kind])
            term = st.text_input("Term *", placeholder="Product Analyst")
            aliases = st.text_input(
                "Aliases (comma separated)", placeholder="product data analyst, analyst product"
            )
            weight = st.slider(
                "Weight", 0.0, 1.0, 1.0, step=0.05,
                help="1.0 counts as a perfect match; lower values are partial credit.",
            )
            primary = st.toggle("Primary target", value=True)
            if st.form_submit_button("Add term", type="primary"):
                if not term.strip():
                    st.error("Term is required.")
                else:
                    try:
                        client.add_term(
                            {
                                "kind": kind,
                                "term": term.strip(),
                                "aliases": [a.strip() for a in aliases.split(",") if a.strip()],
                                "weight": weight,
                                "is_primary": primary,
                                "active": True,
                            }
                        )
                        st.success(f"Added '{term}'.")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_error(exc)

    with cols[1]:
        st.markdown("### Restore defaults")
        st.caption(
            "Adds back any missing default terms (Associate Product Manager, Product Analyst, "
            "Fintech, SaaS, and so on). Your custom terms are left alone."
        )
        if st.button("Restore default taxonomy"):
            try:
                result = client.reset_taxonomy()
                st.success(result["detail"])
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                show_error(exc)

    st.markdown("### Current taxonomy")
    for kind in KINDS:
        subset = [t for t in terms if t["kind"] == kind]
        if not subset:
            continue
        st.markdown(f"#### {kind.title()} ({len(subset)})")
        st.caption(KIND_HELP[kind])
        for entry in subset:
            row = st.columns([2.5, 3.5, 1, 1])
            row[0].markdown(f"**{entry['term']}**")
            row[1].markdown(
                f"<span class='roi-meta'>{', '.join(entry['aliases']) or 'no aliases'}</span>",
                unsafe_allow_html=True,
            )
            row[2].markdown(
                badge(f"w {entry['weight']:.2f}", "roi-b-info" if entry["is_primary"] else "roi-b-mute"),
                unsafe_allow_html=True,
            )
            if row[3].button("Delete", key=f"delterm_{entry['id']}", use_container_width=True):
                try:
                    client.delete_term(entry["id"])
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)

with tab_scoring:
    try:
        scoring = client.scoring()
    except Exception as exc:  # noqa: BLE001
        show_error(exc)
        st.stop()

    st.caption(
        "Each group is normalised to 0-100, so what matters is the balance between the "
        "components rather than the absolute totals."
    )

    groups = [
        ("job", "Job relevance", "How well a role matches your profile."),
        ("hiring", "Hiring activity", "How strongly a company looks like it is hiring now."),
        ("recruiter", "Recruiter relevance", "How likely this person is the right contact."),
        ("outreach", "Outreach priority", "The final ranking on the dashboard."),
    ]

    payload: dict[str, dict] = {}
    for key, title, description in groups:
        st.markdown(f"### {title}")
        st.caption(description)
        weights = scoring[key]
        cols = st.columns(min(3, len(weights)) or 1)
        updated: dict[str, float] = {}
        for index, (name, value) in enumerate(sorted(weights.items())):
            column = cols[index % len(cols)]
            updated[name] = column.number_input(
                name.replace("_", " ").title(),
                min_value=0.0,
                max_value=100.0,
                value=float(value),
                step=1.0,
                key=f"{key}_{name}",
            )
        total = sum(updated.values())
        st.markdown(
            f"<span class='roi-meta'>Total weight: <strong>{total:g}</strong> "
            f"(normalised to 100 when scoring)</span>",
            unsafe_allow_html=True,
        )
        payload[f"{key}_weights"] = updated

    st.markdown("### Thresholds and windows")
    options = dict(scoring["options"])
    option_cols = st.columns(3)
    editable = [
        ("relevance_threshold", "Relevant job threshold", "A job at or above this counts as relevant."),
        ("fresh_job_window_hours", "“Fresh” window (hours)", "Full freshness credit inside this window."),
        ("new_job_window_hours", "“New” window (hours)", "How long a discovery counts as a new signal."),
        ("freshness_full_hours", "Job freshness full credit (hours)", ""),
        ("freshness_zero_hours", "Job freshness zero credit (hours)", ""),
        ("contact_today_min_job_relevance", "Contact Today: min job relevance", ""),
        ("contact_today_min_priority", "Contact Today: min priority", ""),
        ("multiple_openings_target", "Openings for full “multiple” credit", ""),
        ("recruiter_recency_zero_days", "Recruiter recency zero (days)", ""),
    ]
    for index, (key, label, helptext) in enumerate(editable):
        column = option_cols[index % 3]
        options[key] = column.number_input(
            label,
            min_value=0.0,
            value=float(options.get(key, 0)),
            step=1.0,
            help=helptext or None,
            key=f"opt_{key}",
        )
    payload["options"] = options

    action_cols = st.columns([1.2, 1.2, 1.6, 4])
    if action_cols[0].button("Save weights", type="primary", use_container_width=True):
        try:
            client.save_scoring(payload)
            st.success("Saved. Re-score to apply them to existing jobs.")
        except Exception as exc:  # noqa: BLE001
            show_error(exc)
    if action_cols[1].button("Reset to defaults", use_container_width=True):
        try:
            client.reset_scoring()
            st.success("Reset to the defaults from the specification.")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            show_error(exc)
    if action_cols[2].button("Re-score all jobs", use_container_width=True):
        try:
            result = client.rescore_jobs()
            st.success(f"Re-scored {result['jobs_rescored']} jobs.")
        except Exception as exc:  # noqa: BLE001
            show_error(exc)
