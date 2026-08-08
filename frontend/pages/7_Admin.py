"""Admin & observability: what ran, what it found, and what went wrong."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from frontend.api_client import get_client  # noqa: E402
from frontend.components.ui import (  # noqa: E402
    CRAWL_STYLES,
    VERIFICATION_STYLES,
    badge,
    empty_state,
    format_dt,
    header,
    kpi,
    page_setup,
    show_error,
    sidebar_status,
)

page_setup("Admin", "🛠️")
client = get_client()
health = sidebar_status(client)
if health is None:
    st.stop()

header(
    "Admin & observability",
    "Collectors fail quietly by nature. Every crawl is recorded with its status, counts, "
    "errors and rate-limit waits, so nothing can look fine while doing nothing.",
)

try:
    overview = client.admin_overview()
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    st.stop()

# --- Configuration --------------------------------------------------------------
st.markdown("## Configuration")
cfg = st.columns(5)
cfg[0].markdown(kpi("Environment", health["environment"]), unsafe_allow_html=True)
cfg[1].markdown(kpi("Database", health["database"]), unsafe_allow_html=True)
cfg[2].markdown(
    kpi("AI provider", "configured" if health["ai_configured"] else "offline", health["ai_provider"]),
    unsafe_allow_html=True,
)
cfg[3].markdown(kpi("Verification", health["email_verification_provider"]), unsafe_allow_html=True)
cfg[4].markdown(
    kpi("API auth", "on" if health["auth_enabled"] else "off", "set API_KEY to enable"),
    unsafe_allow_html=True,
)

# --- Discovery ------------------------------------------------------------------
st.markdown("## Discovery (last 7 days)")
disc = st.columns(5)
disc[0].markdown(kpi("Jobs discovered", overview["jobs_discovered_7d"]), unsafe_allow_html=True)
disc[1].markdown(kpi("Recruiters discovered", overview["recruiters_discovered_7d"]), unsafe_allow_html=True)
disc[2].markdown(kpi("Emails discovered", overview["emails_discovered"]), unsafe_allow_html=True)
disc[3].markdown(
    kpi("Inferred addresses", overview["inferred_emails"], "guesses — never verified"),
    unsafe_allow_html=True,
)
disc[4].markdown(
    kpi("Rate-limit waits", len(overview["rate_limit_events"]), "politeness delays observed"),
    unsafe_allow_html=True,
)

cols = st.columns(3)
with cols[0]:
    st.markdown("### Crawl outcomes")
    if not overview["crawl_status_counts"]:
        st.caption("No crawls yet.")
    for status, count in sorted(overview["crawl_status_counts"].items()):
        st.markdown(
            f"{badge(status, CRAWL_STYLES.get(status, 'roi-b-mute'))} &nbsp; **{count}**",
            unsafe_allow_html=True,
        )
with cols[1]:
    st.markdown("### Sources checked")
    if not overview["sources_checked"]:
        st.caption("No sources checked yet.")
    for source, count in sorted(overview["sources_checked"].items(), key=lambda kv: -kv[1]):
        st.markdown(f"<div class='roi-meta'>{source} — <strong>{count}</strong></div>", unsafe_allow_html=True)
with cols[2]:
    st.markdown("### Verification results")
    if not overview["verification_results"]:
        st.caption("No verifications run yet.")
    for status, count in sorted(overview["verification_results"].items()):
        st.markdown(
            f"{badge(status, VERIFICATION_STYLES.get(status, 'roi-b-mute'))} &nbsp; **{count}**",
            unsafe_allow_html=True,
        )

# --- Last crawl -----------------------------------------------------------------
st.markdown("## Last crawl")
last = overview.get("last_crawl")
if not last:
    empty_state(
        "Nothing has been crawled yet",
        "Run a scan from the Companies page and this fills with the full record of what happened.",
    )
else:
    summary = st.columns(6)
    summary[0].markdown(kpi("Collector", last["collector"]), unsafe_allow_html=True)
    summary[1].markdown(kpi("Status", last["status"]), unsafe_allow_html=True)
    summary[2].markdown(kpi("Found", last["records_found"]), unsafe_allow_html=True)
    summary[3].markdown(kpi("Added", last["records_added"]), unsafe_allow_html=True)
    summary[4].markdown(kpi("Rejected", last["records_rejected"]), unsafe_allow_html=True)
    summary[5].markdown(kpi("Pages fetched", last["pages_fetched"]), unsafe_allow_html=True)
    if last.get("notes"):
        st.info(last["notes"])

# --- Crawl history --------------------------------------------------------------
st.markdown("## Crawl history")
crawls = overview.get("recent_crawls", [])
if not crawls:
    st.caption("No crawl runs recorded.")
for run in crawls:
    with st.container(border=True):
        row = st.columns([1.6, 2.6, 1.1, 1.6, 1.4, 1.0])
        row[0].markdown(
            badge(run["status"], CRAWL_STYLES.get(run["status"], "roi-b-mute")),
            unsafe_allow_html=True,
        )
        with row[1]:
            st.markdown(f"**{run['collector']}**")
            st.markdown(
                f"<span class='roi-meta'>{(run.get('target_url') or run['source'])[:70]}</span>",
                unsafe_allow_html=True,
            )
        row[2].markdown(
            f"<span class='roi-meta'>{format_dt(run['started_at'], '%d %b %H:%M')}</span>",
            unsafe_allow_html=True,
        )
        row[3].markdown(
            f"<span class='roi-meta'>found {run['records_found']} · added {run['records_added']} · "
            f"rejected {run['records_rejected']}</span>",
            unsafe_allow_html=True,
        )
        row[4].markdown(
            f"<span class='roi-meta'>{run['pages_fetched']} pages · "
            f"{run['rate_limit_waits']} waits ({run['rate_limit_seconds']:.1f}s)</span>",
            unsafe_allow_html=True,
        )
        with row[5]:
            if st.button("Records", key=f"recs_{run['id']}", use_container_width=True):
                st.session_state["open_crawl"] = (
                    None if st.session_state.get("open_crawl") == run["id"] else run["id"]
                )

        if run.get("errors"):
            for error in run["errors"][:4]:
                st.markdown(
                    f"<div class='roi-gap'>⚠ [{error.get('stage')}] {error.get('message', '')[:220]}</div>",
                    unsafe_allow_html=True,
                )
        if run.get("notes"):
            st.caption(run["notes"][:400])

        if st.session_state.get("open_crawl") == run["id"]:
            try:
                records = client.crawl_records(run["id"])
            except Exception as exc:  # noqa: BLE001
                show_error(exc)
                records = []
            if not records:
                st.caption("No individual records were stored for this run.")
            for record in records[:60]:
                mark = "✓" if record["accepted"] else "✗"
                css = "roi-reason" if record["accepted"] else "roi-gap"
                label = record["raw_payload"].get("title") or record["raw_payload"].get("name") or record["record_type"]
                reason = "" if record["accepted"] else f" — rejected: {record['reject_reason']}"
                st.markdown(
                    f"<div class='{css}'>{mark} [{record['record_type']}] {label}{reason}</div>",
                    unsafe_allow_html=True,
                )

# --- Errors ---------------------------------------------------------------------
st.markdown("## Recent errors and blocks")
if not overview["recent_errors"]:
    st.success("No errors recorded in recent crawls.")
for error in overview["recent_errors"][:25]:
    st.markdown(
        f"<div class='roi-meta'>{format_dt(error.get('started_at'), '%d %b %H:%M')} · "
        f"<strong>{error.get('collector')}</strong> (crawl #{error.get('crawl_id')}) · "
        f"{error.get('stage')}<br>{error.get('message', '')[:260]}</div>",
        unsafe_allow_html=True,
    )

# --- Rate limiting --------------------------------------------------------------
st.markdown("## Rate-limit events")
st.caption(
    "Politeness delays this process has observed. Waiting is the mechanism — there is no "
    "proxy rotation or evasion anywhere in this system."
)
if not overview["rate_limit_events"]:
    st.caption("No rate-limit waits recorded in this process yet.")
for event in overview["rate_limit_events"][:20]:
    st.markdown(
        f"<div class='roi-meta'>{event['domain']} — waited {event['waited_seconds']:.2f}s</div>",
        unsafe_allow_html=True,
    )

# --- Verifications --------------------------------------------------------------
st.markdown("## Recent email verifications")
try:
    verifications = client.verifications(limit=25)
except Exception as exc:  # noqa: BLE001
    show_error(exc)
    verifications = []
if not verifications:
    st.caption("No verifications recorded yet.")
for entry in verifications:
    st.markdown(
        f"{badge(entry['status'], VERIFICATION_STYLES.get(entry['status'], 'roi-b-mute'))} "
        f"<span class='roi-mono'>{entry['email']}</span> "
        f"<span class='roi-meta'>· {entry['provider']} · {format_dt(entry['checked_at'])} · "
        f"{entry['reason'][:90]}</span>",
        unsafe_allow_html=True,
    )
