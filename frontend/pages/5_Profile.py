"""Profile: the résumé context the AI drafter and scorers use."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import get_client  # noqa: E402
from frontend.components.ui import header, page_setup, show_error, sidebar_status  # noqa: E402

page_setup("Profile", "👤")
client = get_client()
if sidebar_status(client) is None:
    st.stop()

header(
    "Your profile",
    "Stored in the database and used for job matching and email drafting. None of it is "
    "hardcoded into the application, and the drafter may only use what you put here.",
)

try:
    profile = client.profile()
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()


def as_lines(values: list[str] | None) -> str:
    return "\n".join(values or [])


def to_list(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


with st.form("profile"):
    st.markdown("### Identity")
    cols = st.columns(2)
    full_name = cols[0].text_input("Full name", value=profile.get("full_name") or "")
    years = cols[1].number_input(
        "Years of experience",
        min_value=0.0,
        max_value=60.0,
        step=0.5,
        value=float(profile.get("years_experience") or 0.0),
        help="Used to judge whether a role's seniority fits you.",
    )
    headline = st.text_input(
        "Headline",
        value=profile.get("headline") or "",
        placeholder="product analyst with 2 years in payments analytics",
        help="One line, lowercase, factual. It appears near the top of generated emails.",
    )

    st.markdown("### Background")
    education = st.text_area("Education", value=profile.get("education") or "", height=90)
    experience = st.text_area(
        "Experience",
        value=profile.get("experience") or "",
        height=180,
        help=(
            "Concrete and specific. The drafter is forbidden from inventing anything, so "
            "whatever is missing here simply will not appear in your emails."
        ),
    )

    st.markdown("### Targeting")
    target_cols = st.columns(2)
    skills = target_cols[0].text_area(
        "Skills (one per line)", value=as_lines(profile.get("skills")), height=160
    )
    target_roles = target_cols[1].text_area(
        "Target roles (one per line)", value=as_lines(profile.get("target_roles")), height=160
    )
    target_cols2 = st.columns(2)
    target_industries = target_cols2[0].text_area(
        "Target industries (one per line)",
        value=as_lines(profile.get("target_industries")),
        height=130,
    )
    preferred_locations = target_cols2[1].text_area(
        "Preferred locations (one per line)",
        value=as_lines(profile.get("preferred_locations")),
        height=130,
    )
    st.caption(
        "These feed the matching engine alongside the editable taxonomy on the Settings page."
    )

    st.markdown("### Links")
    link_cols = st.columns(3)
    portfolio = link_cols[0].text_input("Portfolio URL", value=profile.get("portfolio_url") or "")
    github = link_cols[1].text_input("GitHub URL", value=profile.get("github_url") or "")
    linkedin = link_cols[2].text_input("LinkedIn URL", value=profile.get("linkedin_url") or "")

    st.markdown("### Résumé")
    resume_name = st.text_input("Résumé filename", value=profile.get("resume_filename") or "")
    resume_text = st.text_area(
        "Résumé text",
        value=profile.get("resume_text") or "",
        height=200,
        help="Paste the plain text of your résumé. It gives the drafter more real detail to use.",
    )

    if st.form_submit_button("Save profile", type="primary"):
        payload = {
            "full_name": full_name.strip() or None,
            "headline": headline.strip() or None,
            "education": education.strip() or None,
            "experience": experience.strip() or None,
            "years_experience": years or None,
            "skills": to_list(skills),
            "target_roles": to_list(target_roles),
            "target_industries": to_list(target_industries),
            "preferred_locations": to_list(preferred_locations),
            "portfolio_url": portfolio.strip() or None,
            "github_url": github.strip() or None,
            "linkedin_url": linkedin.strip() or None,
            "resume_filename": resume_name.strip() or None,
            "resume_text": resume_text.strip() or None,
        }
        try:
            client.save_profile(payload)
            st.success("Profile saved. Re-score jobs on the Settings page to apply it everywhere.")
        except Exception as exc:  # noqa: BLE001
            show_error(exc)

uploaded = st.file_uploader(
    "Or upload a plain-text résumé (.txt / .md) to fill the text field",
    type=["txt", "md"],
)
if uploaded is not None:
    content = uploaded.read().decode("utf-8", errors="replace")
    st.text_area("Extracted text — copy into the field above and save", value=content, height=200)
