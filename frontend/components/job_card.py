"""The job card: one validated posting, its contacts, and what you can do next.

This is the product's main object. Everything the workflow needs sits on one
card — the posting, whether it was verified, who to contact, and the actions
that move it along — so the user never has to hold state in their head between
pages.

The actions stop where the user's judgement starts. "Apply" opens the employer's
own page; "Copy email" puts an address on the clipboard. Nothing here submits an
application or sends a message.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from frontend.components.ui import (
    CONTACT_ROLE_STYLES,
    EMAIL_STYLES,
    application_badge,
    badge,
    escape,
    format_dt,
    humanize_days,
    score_bar,
    validation_badge,
)


def _meta_line(job: dict) -> str:
    bits: list[str] = []
    if job.get("location"):
        bits.append(escape(job["location"]))
    if job.get("is_remote"):
        bits.append("Remote")
    bits.append(f"Experience: <b>{escape(job.get('experience_label') or 'Not stated')}</b>")
    if job.get("employment_type"):
        bits.append(escape(job["employment_type"]))
    if job.get("salary_text"):
        bits.append(escape(job["salary_text"]))
    return " · ".join(bits)


def _provenance_line(job: dict) -> str:
    found = format_dt(job.get("discovered_at"))
    posted = job.get("posted_at")
    posted_text = (
        f"posted {humanize_days(job.get('age_days'))}" if posted else "posting date not stated"
    )
    return f"{escape(job.get('source_label') or job.get('source'))} · {posted_text} · found {found}"


def render_validation(job: dict) -> None:
    """The validation verdict, always with the reason behind it."""
    validation = job.get("validation") or {}
    reason = validation.get("reason")
    checks = validation.get("checks") or {}
    with st.expander("How this posting was checked", expanded=False):
        if reason:
            st.markdown(f"<div class='jw-note'>{escape(reason)}</div>", unsafe_allow_html=True)
        labels = {
            "URL_REACHABLE": "Job URL works",
            "COMPANY_MATCHES": "Belongs to the stated company",
            "STILL_ACTIVE": "Role still open",
        }
        symbols = {"PASS": "✅", "FAIL": "❌", "UNKNOWN": "❔"}
        for key, label in labels.items():
            outcome = checks.get(key, "UNKNOWN")
            st.markdown(
                f"{symbols.get(outcome, '❔')} {label} — "
                f"<span class='jw-note'>{outcome.lower()}</span>",
                unsafe_allow_html=True,
            )
        if not validation.get("confirmed"):
            st.caption(
                "Unconfirmed does not mean stale — it usually means the site blocks automated "
                "checks. Open the link to see for yourself."
            )
        if validation.get("checked_at"):
            st.caption(f"Checked {format_dt(validation['checked_at'], '%d %b %Y, %H:%M')}")


def render_contact(contact: dict, *, key_prefix: str) -> None:
    """One contact, with its provenance stated rather than implied."""
    role_style = CONTACT_ROLE_STYLES.get(contact.get("role", ""), "jw-b-mute")
    name = contact.get("display_name") or "Contact"
    header = f"<span class='jw-contact-name'>{escape(name)}</span> "
    header += badge(contact.get("role_label") or contact.get("role", ""), role_style)

    st.markdown(f"<div class='jw-contact'>{header}", unsafe_allow_html=True)
    if contact.get("title"):
        st.markdown(
            f"<div class='jw-contact-title'>{escape(contact['title'])}</div>",
            unsafe_allow_html=True,
        )

    email = contact.get("email")
    if email:
        st.markdown(
            f"<span class='jw-mono'>{escape(email)}</span> "
            + badge(
                contact.get("email_status_label") or "",
                EMAIL_STYLES.get(contact.get("email_status", "NONE"), "jw-b-mute"),
            ),
            unsafe_allow_html=True,
        )
    elif contact.get("is_person"):
        st.markdown(
            "<div class='jw-note'>No public address found for this person.</div>",
            unsafe_allow_html=True,
        )

    links: list[str] = []
    if contact.get("profile_url"):
        links.append(f"[Profile]({contact['profile_url']})")
    if contact.get("search_url"):
        links.append(f"[Search LinkedIn]({contact['search_url']})")
    if contact.get("source_url"):
        links.append(f"[Where this came from]({contact['source_url']})")
    if links:
        st.markdown(" · ".join(links))

    if email:
        with st.popover("Copy email", use_container_width=False):
            st.code(email, language=None)
            st.caption("Select and copy — the address is never sent from here.")

    if contact.get("rationale"):
        st.caption(contact["rationale"])
    st.markdown("</div>", unsafe_allow_html=True)


def render_contacts(job: dict, *, on_rediscover: Callable[[int], None] | None = None) -> dict | None:
    """Every contact for a job. Returns the best one with an address, if any."""
    contacts = job.get("contacts") or []
    people = [c for c in contacts if c.get("is_person")]
    inboxes = [c for c in contacts if not c.get("is_person")]

    st.markdown("**Who to contact**")
    if not people:
        st.markdown(
            "<div class='jw-note'>No named person is published for this company on the pages "
            "checked. Rather than guess an address, here is where to look.</div>",
            unsafe_allow_html=True,
        )
    for index, contact in enumerate(people):
        render_contact(contact, key_prefix=f"{job['id']}-p{index}")
    for index, contact in enumerate(inboxes):
        render_contact(contact, key_prefix=f"{job['id']}-i{index}")

    if on_rediscover is not None and st.button(
        "Look again for contacts", key=f"rediscover-{job['id']}", use_container_width=True
    ):
        on_rediscover(job["id"])

    with_email = [c for c in contacts if c.get("email")]
    return with_email[0] if with_email else (contacts[0] if contacts else None)


def render_job_card(
    job: dict,
    *,
    on_save: Callable[[dict, dict | None], None] | None = None,
    on_mark_applied: Callable[[dict, dict | None], None] | None = None,
    on_dismiss: Callable[[dict], None] | None = None,
    on_rediscover: Callable[[int], None] | None = None,
    show_contacts: bool = True,
) -> None:
    """Render one job with its contacts and actions side by side."""
    with st.container(border=True):
        left, right = st.columns([3, 2], gap="medium")

        with left:
            st.markdown(
                f"<div class='jw-title'>{escape(job['title'])}</div>"
                f"<div class='jw-company'>{escape(job['company_name'])}</div>"
                f"<div class='jw-meta'>{_meta_line(job)}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                validation_badge(job.get("validation") or {})
                + application_badge(job.get("application_status"))
                + badge(f"Match {job.get('relevance_score', 0):.0f}", "jw-b-info"),
                unsafe_allow_html=True,
            )
            st.markdown(score_bar(job.get("relevance_score", 0)), unsafe_allow_html=True)

            if job.get("summary"):
                st.markdown(
                    f"<div class='jw-summary'>{escape(job['summary'])}</div>",
                    unsafe_allow_html=True,
                )
            for reason in (job.get("match_reasons") or [])[:3]:
                st.markdown(f"<div class='jw-reason'>✓ {escape(reason)}</div>", unsafe_allow_html=True)

            st.markdown(
                f"<div class='jw-meta' style='margin-top:0.5rem'>{_provenance_line(job)}</div>",
                unsafe_allow_html=True,
            )
            render_validation(job)

        with right:
            best_contact: dict | None = None
            if show_contacts:
                best_contact = render_contacts(job, on_rediscover=on_rediscover)

            st.markdown("**Your next step**")
            apply_url = job.get("apply_url") or job.get("job_url")
            link_row = st.columns(2)
            with link_row[0]:
                st.link_button("View job", job["job_url"], use_container_width=True)
            with link_row[1]:
                st.link_button("Apply", apply_url, use_container_width=True, type="primary")

            action_row = st.columns(2)
            with action_row[0]:
                if on_save is not None and st.button(
                    "Save to tracker", key=f"save-{job['id']}", use_container_width=True
                ):
                    on_save(job, best_contact)
            with action_row[1]:
                if on_mark_applied is not None and st.button(
                    "Mark as applied", key=f"applied-{job['id']}", use_container_width=True
                ):
                    on_mark_applied(job, best_contact)

            if on_dismiss is not None and st.button(
                "Not relevant — hide", key=f"dismiss-{job['id']}", use_container_width=True
            ):
                on_dismiss(job)
