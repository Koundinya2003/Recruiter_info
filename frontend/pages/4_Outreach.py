"""Outreach workspace: queue, AI drafting, approval and recording."""

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
    kpi,
    page_setup,
    priority_badge,
    render_breakdown,
    show_error,
    sidebar_status,
    status_badge,
)

page_setup("Outreach", "✉️")
client = get_client()
if sidebar_status(client) is None:
    st.stop()

header(
    "Outreach workspace",
    "Draft, review, approve, send yourself, then record it. Nothing leaves this machine "
    "automatically — the application never sends email.",
)

STATUSES = [
    "NEW", "REVIEWED", "APPROVED", "CONTACTED", "REPLIED", "FOLLOW_UP", "ARCHIVED", "DO_NOT_CONTACT",
]

with st.sidebar:
    st.markdown("### Filters")
    status_filter = st.selectbox("Status", ["All", *STATUSES])
    query = st.text_input("Search", placeholder="recruiter or company")
    include_demo = st.toggle("Include demo data", value=True)

try:
    leads = client.leads(
        status=None if status_filter == "All" else status_filter,
        q=query or None,
        include_demo=include_demo,
        limit=100,
    )
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()

counts: dict[str, int] = {}
for lead in leads:
    counts[lead["status"]] = counts.get(lead["status"], 0) + 1

tiles = st.columns(5)
for col, key in zip(tiles, ["NEW", "REVIEWED", "APPROVED", "CONTACTED", "REPLIED"], strict=True):
    col.markdown(kpi(key.replace("_", " ").title(), counts.get(key, 0)), unsafe_allow_html=True)

if not leads:
    empty_state(
        "Your outreach queue is empty",
        "Leads come from the dashboard: open an opportunity and add it to the queue.",
        "Go to the Dashboard and pick the highest-priority opportunity.",
    )
    st.stop()

st.markdown("## Queue")

for lead in leads:
    try:
        detail = client.lead(lead["id"])
    except Exception as exc:  # noqa: BLE001
        show_error(exc)
        continue

    with st.container(border=True):
        cols = st.columns([2.4, 2.4, 1.4, 1.4, 1.1])
        with cols[0]:
            demo = " " + demo_badge() if detail.get("is_demo") else ""
            st.markdown(f"**{detail.get('recruiter_name') or '—'}**{demo}", unsafe_allow_html=True)
            st.markdown(
                f"<span class='roi-meta'>{detail.get('company_name') or '—'}</span>",
                unsafe_allow_html=True,
            )
        with cols[1]:
            if detail.get("job_url"):
                st.markdown(f"[{detail.get('job_title') or 'Role'}]({detail['job_url']})")
            else:
                st.markdown(detail.get("job_title") or "No specific role")
            st.markdown(
                f"<span class='roi-mono'>{detail.get('recruiter_email') or 'no address'}</span>",
                unsafe_allow_html=True,
            )
        with cols[2]:
            st.markdown(
                f"**{detail['outreach_priority']:.0f}** {priority_badge(detail.get('priority_band') or '')}",
                unsafe_allow_html=True,
            )
        with cols[3]:
            st.markdown(status_badge(detail["status"]), unsafe_allow_html=True)
            if detail["draft_approved"]:
                st.markdown(badge("Draft approved", "roi-b-good"), unsafe_allow_html=True)
            elif detail["draft_body"]:
                st.markdown(badge("Draft not approved", "roi-b-warn"), unsafe_allow_html=True)
            if detail["contacted_at"]:
                st.markdown(
                    f"<span class='roi-meta'>Contacted {format_dt(detail['contacted_at'], '%d %b')}"
                    f" ({detail['contact_count']}×)</span>",
                    unsafe_allow_html=True,
                )
        with cols[4]:
            if st.button("Open", key=f"open_{lead['id']}", use_container_width=True):
                st.session_state["open_lead"] = (
                    None if st.session_state.get("open_lead") == lead["id"] else lead["id"]
                )

        if st.session_state.get("open_lead") != lead["id"]:
            continue

        st.divider()
        if detail["recruiter_do_not_contact"]:
            st.error(
                "This recruiter is marked DO NOT CONTACT. Drafting and outreach are blocked.",
                icon="🚫",
            )
        if detail["recruiter_email_is_inferred"]:
            st.warning(
                "The address on this lead was **inferred from a name pattern** — it was not "
                "published by the company. Verify it, or find a published address, before sending.",
                icon="⚠️",
            )

        tabs = st.tabs(["Draft & approve", "Why this priority", "History", "Notes"])

        # --- Draft & approve --------------------------------------------------
        with tabs[0]:
            controls = st.columns([3, 1.3, 1.3])
            reason = controls[0].text_input(
                "Why are you reaching out? (optional, feeds the draft)",
                key=f"reason_{lead['id']}",
                placeholder="e.g. my analytics work lines up with this role",
            )
            offline = controls[1].toggle("Offline template", key=f"off_{lead['id']}", value=False)
            if controls[2].button(
                "Generate draft", key=f"gen_{lead['id']}", type="primary", use_container_width=True
            ):
                with st.spinner("Drafting…"):
                    try:
                        draft = client.generate_draft(lead["id"], reason or None, offline)
                        st.session_state[f"warn_{lead['id']}"] = draft["warnings"]
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_error(exc)

            for warning in st.session_state.get(f"warn_{lead['id']}", []):
                st.warning(warning, icon="📝")

            if not detail["draft_body"]:
                st.info(
                    "No draft yet. Generate one, or write your own below — either way it stays "
                    "a draft until you approve it.",
                    icon="✍️",
                )

            subject = st.text_input(
                "Subject", value=detail.get("draft_subject") or "", key=f"subj_{lead['id']}"
            )
            body = st.text_area(
                "Body", value=detail.get("draft_body") or "", height=320, key=f"body_{lead['id']}"
            )
            if detail.get("draft_provider"):
                st.caption(
                    f"Generated by {detail['draft_provider']} ({detail.get('draft_model')}) "
                    f"on {format_dt(detail.get('draft_generated_at'))}. Always your words to send."
                )

            actions = st.columns([1.2, 1.2, 1.4, 1.6])
            if actions[0].button("Save edits", key=f"save_{lead['id']}", use_container_width=True):
                try:
                    client.edit_draft(lead["id"], subject, body)
                    st.success("Saved. Editing resets approval, so you always approve what you send.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)

            if actions[1].button(
                "Approve", key=f"appr_{lead['id']}", type="primary", use_container_width=True,
                disabled=detail["recruiter_do_not_contact"],
            ):
                try:
                    if subject != (detail.get("draft_subject") or "") or body != (detail.get("draft_body") or ""):
                        client.edit_draft(lead["id"], subject, body)
                    client.approve_lead(lead["id"])
                    st.success("Approved. You can now record the outreach once you have sent it.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)

            can_record = detail["draft_approved"] and not detail["contacted_at"]
            if actions[2].button(
                "I sent this — record it",
                key=f"rec_{lead['id']}",
                use_container_width=True,
                disabled=not can_record,
                help=None if can_record else "Approve the draft first. Already contacted leads use Follow-up.",
            ):
                try:
                    client.record_outreach(lead["id"])
                    st.success("Recorded. This pair will not be recommended again.")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)

            if detail["contacted_at"]:
                with actions[3]:
                    if st.button("Record a follow-up", key=f"fu_{lead['id']}", use_container_width=True):
                        try:
                            client.follow_up(lead["id"])
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            show_error(exc)
                    response = st.selectbox(
                        "Their response",
                        ["AWAITING", "POSITIVE", "NEGATIVE", "NO_RESPONSE"],
                        index=["AWAITING", "POSITIVE", "NEGATIVE", "NO_RESPONSE"].index(
                            detail["response_status"]
                        ),
                        key=f"resp_{lead['id']}",
                    )
                    if response != detail["response_status"] and st.button(
                        "Save response", key=f"saveresp_{lead['id']}", use_container_width=True
                    ):
                        try:
                            client.record_response(lead["id"], response)
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            show_error(exc)

            if detail["allowed_transitions"]:
                with st.expander("Change status manually"):
                    st.caption(
                        "Only transitions valid from the current state are offered. "
                        "CONTACTED is deliberately not settable by hand — use “record it” "
                        "so the contact is logged."
                    )
                    choice = st.selectbox(
                        "Move to", detail["allowed_transitions"], key=f"trans_{lead['id']}"
                    )
                    if st.button("Apply", key=f"applytrans_{lead['id']}"):
                        try:
                            client.change_status(lead["id"], choice)
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            show_error(exc)

        # --- Priority ---------------------------------------------------------
        with tabs[1]:
            render_breakdown(detail.get("priority", {}))
            st.caption(f"Computed {format_dt(detail.get('priority_computed_at'))}")

        # --- History ----------------------------------------------------------
        with tabs[2]:
            events = detail.get("events", [])
            if not events:
                st.caption("No events recorded yet.")
            for event in reversed(events):
                movement = ""
                if event.get("from_status") or event.get("to_status"):
                    movement = f" · {event.get('from_status') or '—'} → {event.get('to_status') or '—'}"
                st.markdown(
                    f"<div class='roi-meta'>{format_dt(event['created_at'])} · "
                    f"<strong>{event['event_type']}</strong>{movement} · by {event['actor']}</div>",
                    unsafe_allow_html=True,
                )
                if event.get("note"):
                    st.caption(event["note"])

        # --- Notes ------------------------------------------------------------
        with tabs[3]:
            if detail.get("notes"):
                st.text(detail["notes"])
            new_note = st.text_area("Add a note", key=f"note_{lead['id']}", height=90)
            if st.button("Save note", key=f"savenote_{lead['id']}"):
                if not new_note.strip():
                    st.error("Note is empty.")
                else:
                    try:
                        client.add_note(lead["id"], new_note.strip())
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        show_error(exc)
