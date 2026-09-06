"""Sources — which job sources are wired up, and your search defaults."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import APIError, get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    badge,
    escape,
    header,
    page_setup,
    show_error,
    sidebar_status,
)

page_setup("Sources", "🔌")
client = get_client()
health = sidebar_status(client)

header(
    "Job sources",
    "Where postings come from. A source that needs credentials is skipped and says so — "
    "it is never quietly replaced with made-up results.",
)

if health is None:
    st.stop()

try:
    sources = client.sources()
except APIError as exc:
    show_error(exc)
    st.stop()

usable = [s for s in sources if s["usable"]]
st.caption(f"{len(usable)} of {len(sources)} sources are ready to query.")

if not usable:
    st.warning(
        "No source is usable. The Muse, Remotive, Arbeitnow, Jobicy and company job "
        "boards all work with no credentials — check they are enabled below."
    )

for source in sources:
    with st.container(border=True):
        left, right = st.columns([3, 1])
        with left:
            st.markdown(f"**{escape(source['label'])}**")
            st.markdown(
                badge("Ready", "jw-b-good")
                if source["usable"]
                else badge(
                    "Needs credentials" if not source["configured"] else "Disabled",
                    "jw-b-warn",
                ),
                unsafe_allow_html=True,
            )
            st.caption(source["coverage"])
            if source["remote_only"]:
                st.caption("Remote roles only — skipped when a search names a city.")
        with right:
            if source["signup_url"]:
                st.link_button("Get credentials", source["signup_url"], use_container_width=True)

        if source["missing_settings"]:
            st.markdown("Set these in your `.env` and restart the API:")
            st.code(
                "\n".join(f"{name.upper()}=" for name in source["missing_settings"]),
                language="bash",
            )
        if not source["enabled"]:
            st.caption(
                f"Switched off. Set `ENABLE_{source['name'].upper()}=true` to turn it back on."
            )

st.markdown("## Company job boards")
st.caption(
    "Greenhouse, Lever and Ashby boards are the best source here: they are the employer's "
    "own live listings. Name companies in a search and their boards are found automatically; "
    "the boards in `app/data/company_boards.json` are used when a search names none."
)

st.markdown("## Your defaults")
st.caption("Used to pre-fill searches. Nothing here is sent anywhere.")

try:
    profile = client.profile()
except APIError as exc:
    show_error(exc)
    st.stop()

with st.form("profile"):
    columns = st.columns(2)
    with columns[0]:
        full_name = st.text_input("Name", value=profile.get("full_name") or "")
        headline = st.text_input("Headline", value=profile.get("headline") or "")
        years = st.number_input(
            "Years of experience",
            min_value=0.0,
            max_value=60.0,
            value=float(profile.get("years_experience") or 0.0),
            step=0.5,
        )
    with columns[1]:
        titles = st.text_input("Usual target roles", ", ".join(profile.get("default_titles") or []))
        locations = st.text_input(
            "Usual locations", ", ".join(profile.get("default_locations") or [])
        )
        skills = st.text_input("Skills", ", ".join(profile.get("skills") or []))
    linkedin = st.text_input("Your LinkedIn URL", value=profile.get("linkedin_url") or "")

    if st.form_submit_button("Save", use_container_width=False):
        def _split(value: str) -> list[str]:
            return [v.strip() for v in value.split(",") if v.strip()]

        try:
            client.save_profile(
                {
                    "full_name": full_name or None,
                    "headline": headline or None,
                    "years_experience": years or None,
                    "default_titles": _split(titles),
                    "default_locations": _split(locations),
                    "skills": _split(skills),
                    "linkedin_url": linkedin or None,
                }
            )
            st.success("Saved.")
        except APIError as exc:
            show_error(exc)
