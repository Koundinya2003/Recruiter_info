"""Companies: tracking, scanning, and the company detail view."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    CRAWL_STYLES,
    badge,
    demo_badge,
    email_badge,
    empty_state,
    format_dt,
    header,
    kpi,
    page_setup,
    render_breakdown,
    score_bar,
    show_error,
    sidebar_status,
)

page_setup("Companies", "🏢")
client = get_client()
if sidebar_status(client) is None:
    st.stop()

header(
    "Companies",
    "Track a small, deliberate set of companies. Each scan reads their official job source "
    "and any publicly published talent contacts.",
)

# --- Add a company --------------------------------------------------------------
with st.expander("Add a company", expanded=False), st.form("add_company"):
    cols = st.columns(2)
    name = cols[0].text_input("Company name *", placeholder="Acme Analytics")
    domain = cols[1].text_input("Company domain", placeholder="acme.com")
    career_url = st.text_input(
        "Career page or ATS board URL",
        placeholder="https://boards.greenhouse.io/acme",
        help=(
            "A Greenhouse / Lever / Ashby board URL is best — those have official public "
            "APIs. Otherwise use the company's own careers page."
        ),
    )
    cols2 = st.columns(3)
    industry = cols2[0].text_input("Industry", placeholder="Fintech")
    priority = cols2[1].selectbox("Priority", ["CRITICAL", "HIGH", "MEDIUM", "LOW"], index=1)
    active = cols2[2].toggle("Monitor actively", value=True)
    notes = st.text_area("Notes", placeholder="Why this company matters to you", height=68)

    if st.form_submit_button("Add company", type="primary"):
        if not name.strip():
            st.error("Company name is required.")
        else:
            try:
                created = client.create_company(
                    {
                        "company_name": name.strip(),
                        "company_domain": domain.strip() or None,
                        "career_page_url": career_url.strip() or None,
                        "industry": industry.strip() or None,
                        "priority": priority,
                        "active": active,
                        "notes": notes.strip() or None,
                    }
                )
                st.success(f"Added {created['company_name']}. Run a scan to discover roles.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                show_error(exc)

# --- Filters --------------------------------------------------------------------
filters = st.columns([2, 1, 1, 1])
query = filters[0].text_input("Search companies", placeholder="name, domain or industry")
state = filters[1].selectbox("Status", ["All", "Active", "Paused"])
industry_filter = filters[2].text_input("Industry filter", placeholder="e.g. Fintech")
include_demo = filters[3].toggle("Include demo", value=True)

try:
    companies = client.companies(
        q=query or None,
        active={"All": None, "Active": True, "Paused": False}[state],
        industry=industry_filter or None,
        include_demo=include_demo,
    )
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()

if not companies:
    empty_state(
        "No companies yet",
        "Start with three to five companies you actually want to work at. "
        "Quality of targeting beats breadth every time.",
        "Use “Add a company” above, or run <code>python scripts/seed_demo.py</code> to explore with demo data.",
    )
    st.stop()

st.markdown(f"### {len(companies)} compan{'y' if len(companies) == 1 else 'ies'}")

for company in companies:
    with st.container(border=True):
        cols = st.columns([3, 1.4, 1.4, 1.5, 1.5])
        with cols[0]:
            demo = " " + demo_badge() if company["is_demo"] else ""
            paused = "" if company["active"] else " " + badge("PAUSED", "roi-b-mute")
            st.markdown(f"**{company['company_name']}**{demo}{paused}", unsafe_allow_html=True)
            st.markdown(
                f"<span class='roi-meta'>{company.get('industry') or 'Industry not set'} · "
                f"{company.get('company_domain') or 'no domain'} · {company['priority']} priority</span>",
                unsafe_allow_html=True,
            )
            if company.get("career_page_url"):
                st.markdown(
                    f"<span class='roi-meta'>Source: "
                    f"<a href='{company['career_page_url']}'>{company['career_page_url'][:60]}</a></span>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<span class='roi-meta'>⚠ No career page URL — jobs cannot be discovered</span>",
                    unsafe_allow_html=True,
                )
        with cols[1]:
            st.markdown(score_bar(company["hiring_activity_score"], "Hiring activity"), unsafe_allow_html=True)
        with cols[2]:
            st.markdown(
                f"<span class='roi-meta'>Last checked<br>{format_dt(company.get('last_checked_at'))}</span>",
                unsafe_allow_html=True,
            )
            if company.get("last_scan_status"):
                st.markdown(
                    badge(company["last_scan_status"], CRAWL_STYLES.get(company["last_scan_status"], "roi-b-mute")),
                    unsafe_allow_html=True,
                )
        with cols[3]:
            if st.button("Scan now", key=f"scan_{company['id']}", type="primary", use_container_width=True):
                with st.spinner(f"Scanning {company['company_name']} — respecting robots.txt and rate limits…"):
                    try:
                        result = client.scan_company(company["id"], {"discover_recruiters": True})
                        st.session_state[f"scan_result_{company['id']}"] = result
                    except Exception as exc:  # noqa: BLE001
                        show_error(exc)
                st.rerun()
            if st.button(
                "Pause" if company["active"] else "Resume",
                key=f"toggle_{company['id']}",
                use_container_width=True,
            ):
                try:
                    if company["active"]:
                        client.pause_company(company["id"])
                    else:
                        client.resume_company(company["id"])
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)
        with cols[4]:
            if st.button("Details", key=f"detail_{company['id']}", use_container_width=True):
                st.session_state["open_company"] = (
                    None if st.session_state.get("open_company") == company["id"] else company["id"]
                )
            if st.button("Remove", key=f"del_{company['id']}", use_container_width=True):
                st.session_state["confirm_delete"] = company["id"]

        if st.session_state.get("confirm_delete") == company["id"]:
            st.warning(
                f"Remove **{company['company_name']}** and all its jobs, recruiters and leads?"
            )
            confirm = st.columns([1, 1, 4])
            if confirm[0].button("Yes, remove", key=f"yes_{company['id']}", type="primary"):
                try:
                    client.delete_company(company["id"])
                    st.session_state.pop("confirm_delete", None)
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)
            if confirm[1].button("Cancel", key=f"no_{company['id']}"):
                st.session_state.pop("confirm_delete", None)
                st.rerun()

        result = st.session_state.get(f"scan_result_{company['id']}")
        if result:
            summary = (
                f"Found {result['jobs_found']} job records "
                f"(+{result['jobs_added']} new, {result['jobs_updated']} updated, "
                f"{result['jobs_closed']} closed) · "
                f"{result['recruiters_added']} new recruiter(s), {result['emails_found']} address(es)"
            )
            if result["ok"] and result["jobs_added"]:
                st.success(summary)
            elif result["ok"]:
                st.info(summary)
            else:
                st.error(summary)
            for note in result["notes"][:4]:
                st.caption(f"• {note}")
            for err in result["errors"][:3]:
                st.caption(f"⚠ {err}")
            st.caption(f"Crawl runs: {result['crawl_run_ids']} — see the Admin page for full detail.")

        # --- Detail view ------------------------------------------------------
        if st.session_state.get("open_company") == company["id"]:
            try:
                detail = client.company(company["id"])
            except Exception as exc:  # noqa: BLE001
                show_error(exc)
                continue

            st.divider()
            stats = detail["stats"]
            tiles = st.columns(6)
            for col, (label, value) in zip(
                tiles,
                [
                    ("Open jobs", stats["open_jobs"]),
                    ("Relevant jobs", stats["relevant_jobs"]),
                    ("Recruiters", stats["recruiters"]),
                    ("Contactable", stats["contactable_recruiters"]),
                    ("Verified emails", stats["verified_emails"]),
                    ("Signals (7d)", stats["signals_7d"]),
                ],
                strict=True,
            ):
                col.markdown(kpi(label, value), unsafe_allow_html=True)

            tabs = st.tabs(
                ["Hiring activity", "Recent jobs", "Recruiters", "Hiring timeline", "Recommended contacts"]
            )

            with tabs[0]:
                render_breakdown(detail.get("hiring_activity_breakdown", {}))
                st.markdown("#### Signals detected")
                if not detail["recent_signals"]:
                    st.caption("No hiring signals recorded yet. Run a scan.")
                for signal in detail["recent_signals"][:10]:
                    st.markdown(
                        f"<div class='roi-meta'>{format_dt(signal['detected_at'])} · "
                        f"<strong>{signal['signal_type']}</strong> (+{signal['points']:g}) — "
                        f"{signal['description']}</div>",
                        unsafe_allow_html=True,
                    )

            with tabs[1]:
                try:
                    jobs = client.jobs(company_id=company["id"], limit=25, sort="relevance")
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)
                    jobs = []
                if not jobs:
                    st.caption("No jobs discovered yet.")
                for job in jobs:
                    line = st.columns([4, 1.2, 1.4, 1.2])
                    line[0].markdown(f"[{job['title']}]({job['job_url']})")
                    line[1].markdown(
                        f"<span class='roi-meta'>{job.get('location') or '—'}</span>",
                        unsafe_allow_html=True,
                    )
                    line[2].markdown(
                        f"<span class='roi-meta'>{format_dt(job.get('posted_at'), '%d %b %Y')}</span>",
                        unsafe_allow_html=True,
                    )
                    line[3].markdown(f"**{job['relevance_score']:.0f}**/100")

            with tabs[2]:
                try:
                    recruiters = client.recruiters(
                        company_id=company["id"], exclude_do_not_contact=False, limit=25
                    )
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)
                    recruiters = []
                if not recruiters:
                    st.caption(
                        "No recruiters found. Most companies do not publish recruiter addresses — "
                        "that is expected, and we do not guess them unless you ask."
                    )
                for rec in recruiters:
                    line = st.columns([2, 2, 2, 1])
                    line[0].markdown(f"**{rec['name']}**")
                    line[1].markdown(
                        f"<span class='roi-meta'>{rec.get('title') or '—'}</span>",
                        unsafe_allow_html=True,
                    )
                    line[2].markdown(
                        email_badge(
                            rec.get("public_professional_email"),
                            verified=rec["email_verified"],
                            status=rec["email_verification_status"],
                            is_inferred=rec["email_is_inferred"],
                            confidence=rec["email_confidence"],
                        ),
                        unsafe_allow_html=True,
                    )
                    line[3].markdown(f"**{rec['relevance_score']:.0f}**")

            with tabs[3]:
                timeline = detail.get("timeline", [])
                if not timeline:
                    st.caption("No postings discovered in the last 45 days.")
                else:
                    counts: dict[str, int] = {}
                    for entry in timeline:
                        counts[entry["date"]] = counts.get(entry["date"], 0) + 1
                    st.bar_chart(counts, height=220, color="#2f57d3")
                    st.caption("Jobs first discovered per day — a proxy for hiring bursts.")
                    for entry in timeline[:12]:
                        st.markdown(
                            f"<div class='roi-meta'>{entry['date']} · {entry['title']} "
                            f"({entry['relevance_score']:.0f}/100)</div>",
                            unsafe_allow_html=True,
                        )

            with tabs[4]:
                try:
                    recs = client.contact_today(company_id=company["id"], limit=8)
                except Exception as exc:  # noqa: BLE001
                    show_error(exc)
                    recs = []
                if not recs:
                    st.caption("No recommended contacts for this company right now.")
                for opp in recs:
                    st.markdown(
                        f"**{opp['outreach_priority']:.0f}** · {opp['recruiter_name']} → "
                        f"{opp['job_title']}"
                    )
                    for reason in opp["reasons"][:3]:
                        st.markdown(f"<div class='roi-reason'>✓ {reason}</div>", unsafe_allow_html=True)
