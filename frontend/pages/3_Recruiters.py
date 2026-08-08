"""Recruiters: relevance, contact provenance, verification and DO NOT CONTACT."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    badge,
    confidence_badge,
    demo_badge,
    email_badge,
    empty_state,
    format_dt,
    header,
    page_setup,
    render_breakdown,
    show_error,
    sidebar_status,
)

page_setup("Recruiters", "🧑‍💼")
client = get_client()
if sidebar_status(client) is None:
    st.stop()

header(
    "Recruiters",
    "Talent contacts discovered from public sources. Every contact detail keeps the URL it "
    "came from, and an inferred address is never shown as if it were published.",
)

try:
    companies = client.companies(limit=200)
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()
company_names = {c["id"]: c["company_name"] for c in companies}

with st.sidebar:
    st.markdown("### Filters")
    query = st.text_input("Search", placeholder="name, title or email")
    company_choice = st.selectbox("Company", ["All companies", *company_names.values()])
    min_score = st.slider("Minimum recruiter score", 0, 100, 0, step=5)
    confidence = st.selectbox("Email confidence", ["Any", "HIGH", "MEDIUM", "LOW", "NONE"])
    verification = st.selectbox(
        "Verification", ["Any", "valid", "risky", "invalid", "unknown", "not_checked"]
    )
    has_email = st.selectbox("Has an address", ["Any", "Yes", "No"])
    show_dnc = st.toggle("Show DO NOT CONTACT", value=False)
    include_demo = st.toggle("Include demo data", value=True)

company_id = None
if company_choice != "All companies":
    company_id = next((cid for cid, n in company_names.items() if n == company_choice), None)

try:
    recruiters = client.recruiters(
        q=query or None,
        company_id=company_id,
        min_score=min_score or None,
        email_confidence=None if confidence == "Any" else confidence,
        verification_status=None if verification == "Any" else verification,
        has_email={"Any": None, "Yes": True, "No": False}[has_email],
        exclude_do_not_contact=not show_dnc,
        include_demo=include_demo,
        limit=100,
    )
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()

# --- Manual entry ---------------------------------------------------------------
with st.expander("Add a contact you found yourself"):
    st.caption(
        "Use this for a contact you located on a public page. The source URL is required — "
        "provenance is not optional in this system."
    )
    with st.form("add_recruiter"):
        cols = st.columns(2)
        target_company = cols[0].selectbox("Company *", list(company_names.values()))
        name = cols[1].text_input("Name *", placeholder="Full name as published")
        cols2 = st.columns(2)
        title = cols2[0].text_input("Title", placeholder="Talent Acquisition Partner")
        email = cols2[1].text_input("Public professional email", placeholder="name@company.com")
        source_url = st.text_input(
            "Source URL (required if an email is given) *", placeholder="https://company.com/careers"
        )
        conf = st.selectbox(
            "Confidence",
            ["HIGH", "MEDIUM", "LOW"],
            index=1,
            help=(
                "HIGH = published and attributed to this person. "
                "MEDIUM = published by the company but not tied to them. "
                "LOW = you inferred it — it will be labelled INFERRED everywhere."
            ),
        )
        if st.form_submit_button("Add contact", type="primary"):
            cid = next((c for c, n in company_names.items() if n == target_company), None)
            try:
                client.create_recruiter(
                    {
                        "company_id": cid,
                        "name": name.strip(),
                        "title": title.strip() or None,
                        "public_professional_email": email.strip() or None,
                        "email_source_url": source_url.strip() or None,
                        "email_source_type": "PATTERN_INFERENCE" if conf == "LOW" else "MANUAL_ENTRY",
                        "email_confidence": conf,
                    }
                )
                st.success(f"Added {name}.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                show_error(exc)

if not recruiters:
    empty_state(
        "No recruiters match",
        "Most companies do not publish recruiter addresses, and this tool will not guess "
        "them unless you explicitly ask for pattern inference during a scan.",
        "Scan a company with recruiter discovery on, or add a contact you found yourself.",
    )
    st.stop()

st.caption(
    f"{len(recruiters)} contacts · "
    f"{sum(1 for r in recruiters if r['email_verified'])} verified · "
    f"{sum(1 for r in recruiters if r['email_is_inferred'])} inferred (unverified by definition)"
)

for rec in recruiters:
    with st.container(border=True):
        cols = st.columns([2.4, 2.2, 1.9, 1.0, 1.1])
        with cols[0]:
            demo = " " + demo_badge() if rec["is_demo"] else ""
            dnc = " " + badge("DO NOT CONTACT", "roi-b-bad") if rec["do_not_contact"] else ""
            st.markdown(f"**{rec['name']}**{demo}{dnc}", unsafe_allow_html=True)
            st.markdown(
                f"<span class='roi-meta'>{rec.get('title') or 'Title unknown'}<br>"
                f"{rec['company_name']}</span>",
                unsafe_allow_html=True,
            )
        with cols[1]:
            st.markdown(
                f"<span class='roi-mono'>{rec.get('public_professional_email') or 'no address'}</span>",
                unsafe_allow_html=True,
            )
            badges = email_badge(
                rec.get("public_professional_email"),
                verified=rec["email_verified"],
                status=rec["email_verification_status"],
                is_inferred=rec["email_is_inferred"],
                confidence=rec["email_confidence"],
            )
            # The confidence badge only adds information when there is an address.
            if rec.get("public_professional_email"):
                badges += " " + confidence_badge(rec["email_confidence"])
            st.markdown(badges, unsafe_allow_html=True)
        with cols[2]:
            st.markdown(
                f"<span class='roi-meta'>Source: {rec.get('email_source_type') or '—'}<br>"
                f"Last seen {format_dt(rec['last_seen'], '%d %b %Y')}</span>",
                unsafe_allow_html=True,
            )
        cols[3].markdown(f"**{rec['relevance_score']:.0f}**/100")
        with cols[4]:
            if st.button("Details", key=f"rd_{rec['id']}", use_container_width=True):
                st.session_state["open_recruiter"] = (
                    None if st.session_state.get("open_recruiter") == rec["id"] else rec["id"]
                )

        if st.session_state.get("open_recruiter") == rec["id"]:
            try:
                detail = client.recruiter(rec["id"])
            except Exception as exc:  # noqa: BLE001
                show_error(exc)
                continue

            st.divider()
            left, right = st.columns([3, 2])
            with left:
                st.markdown("#### Why this recruiter matters")
                render_breakdown(detail["relevance"])

                st.markdown("#### Associated jobs")
                if not detail["associated_jobs"]:
                    st.caption("No jobs linked to this contact yet.")
                for job in detail["associated_jobs"]:
                    st.markdown(f"[{job['title']}]({job['job_url']}) — {job['relevance_score']:.0f}/100")
                    st.markdown(
                        f"<span class='roi-meta'>{job['rationale'] or job['relation']}</span>",
                        unsafe_allow_html=True,
                    )

            with right:
                st.markdown("#### Contact details and their sources")
                if not detail["contacts"]:
                    st.caption("No contact details recorded.")
                for contact in detail["contacts"]:
                    with st.container(border=True):
                        st.markdown(
                            f"<span class='roi-mono'>{contact['value']}</span>",
                            unsafe_allow_html=True,
                        )
                        if contact["is_inferred"]:
                            st.markdown(
                                badge("INFERRED — not published anywhere", "roi-b-bad"),
                                unsafe_allow_html=True,
                            )
                        else:
                            st.markdown(
                                confidence_badge(contact["confidence"]), unsafe_allow_html=True
                            )
                        st.markdown(
                            f"<span class='roi-meta'>Type: {contact['contact_type']} · "
                            f"Source type: {contact['source_type']}</span>",
                            unsafe_allow_html=True,
                        )
                        if contact.get("source_url"):
                            st.markdown(
                                f"<span class='roi-meta'>From: "
                                f"<a href='{contact['source_url']}'>{contact['source_url'][:70]}</a></span>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.markdown(
                                "<span class='roi-meta'>No source URL — this was not published.</span>",
                                unsafe_allow_html=True,
                            )
                        if contact.get("source_excerpt"):
                            st.caption(contact["source_excerpt"][:220])

                st.markdown("#### Verification")
                if detail["email_is_inferred"]:
                    st.warning(
                        "This address is inferred. Checking it can only tell you whether the "
                        "domain accepts mail — it can never confirm this person's mailbox, so "
                        "it will not be marked verified.",
                        icon="⚠️",
                    )
                if detail.get("public_professional_email") and st.button(
                    "Verify email now", key=f"verify_{rec['id']}", use_container_width=True
                ):
                    try:
                        result = client.verify_recruiter_email(rec["id"])
                        st.success(
                            f"{result['status']} (confidence {result['confidence']:.0%}, "
                            f"provider {result['provider']})"
                        )
                        st.caption(result.get("reason", ""))
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_error(exc)
                for entry in detail["verification_history"][:5]:
                    st.markdown(
                        f"<span class='roi-meta'>{format_dt(entry['checked_at'])} · "
                        f"{entry['status']} via {entry['provider']} — {entry['reason'][:90]}</span>",
                        unsafe_allow_html=True,
                    )

                st.markdown("#### Actions")
                if detail["do_not_contact"]:
                    st.error(
                        f"Marked DO NOT CONTACT. Reason: {detail.get('do_not_contact_reason') or 'not given'}"
                    )
                    if st.button("Lift DO NOT CONTACT", key=f"undnc_{rec['id']}"):
                        try:
                            client.clear_do_not_contact(rec["id"])
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            show_error(exc)
                else:
                    reason = st.text_input(
                        "Reason (optional)", key=f"dncreason_{rec['id']}",
                        placeholder="e.g. asked not to be contacted",
                    )
                    if st.button("Mark DO NOT CONTACT", key=f"dnc_{rec['id']}"):
                        try:
                            msg = client.do_not_contact(rec["id"], reason or None)
                            st.success(msg["detail"])
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            show_error(exc)
