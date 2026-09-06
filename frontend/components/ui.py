"""Shared UI vocabulary: theme, badges, job cards, empty states.

Keeping these in one place is what makes the app read as a product rather than
a pile of Streamlit widgets — a validation badge means the same thing on every
page, and a job card offers the same actions wherever it appears.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import streamlit as st

APP_NAME = "Job Search Workspace"

THEME_CSS = """
<style>
:root {
  --jw-ink: #10151f;
  --jw-muted: #5b6675;
  --jw-line: #e3e7ee;
  --jw-bg-soft: #f6f8fb;
  --jw-accent: #2f57d3;
  --jw-good: #0f7a45;
  --jw-warn: #9a6100;
  --jw-bad: #b3261e;
}
html, body, [class*="css"] { font-feature-settings: "tnum" 1, "cv05" 1; }
.block-container { padding-top: 2.2rem; max-width: 1400px; }
h1 { font-size: 1.85rem !important; font-weight: 700 !important; letter-spacing: -0.02em; }
h2 { font-size: 1.25rem !important; font-weight: 650 !important; letter-spacing: -0.01em;
     margin-top: 1.6rem !important; }
h3 { font-size: 1.02rem !important; font-weight: 620 !important; }

.jw-sub { color: var(--jw-muted); font-size: 0.94rem; margin-top: -0.5rem;
          margin-bottom: 1.2rem; }

.jw-badge { display: inline-block; padding: 0.13rem 0.55rem; border-radius: 999px;
            font-size: 0.72rem; font-weight: 650; letter-spacing: 0.02em;
            white-space: nowrap; border: 1px solid transparent; margin-right: 0.3rem; }
.jw-b-good { background: #e6f4ec; color: var(--jw-good); border-color: #bfe3cf; }
.jw-b-warn { background: #fdf2df; color: var(--jw-warn); border-color: #f3ddb0; }
.jw-b-bad  { background: #fdeceb; color: var(--jw-bad);  border-color: #f5c9c6; }
.jw-b-info { background: #eaeffc; color: var(--jw-accent); border-color: #c9d5f7; }
.jw-b-mute { background: #eef1f5; color: var(--jw-muted); border-color: var(--jw-line); }

.jw-card { border: 1px solid var(--jw-line); border-radius: 12px; padding: 1rem 1.15rem;
           background: #fff; margin-bottom: 0.85rem; }

.jw-title { font-size: 1.06rem; font-weight: 660; color: var(--jw-ink); line-height: 1.35; }
.jw-company { color: var(--jw-ink); font-weight: 600; }
.jw-meta { color: var(--jw-muted); font-size: 0.85rem; }
.jw-meta b { color: var(--jw-ink); font-weight: 600; }
.jw-summary { color: #33404f; font-size: 0.88rem; line-height: 1.5; margin: 0.5rem 0 0.2rem; }

.jw-kpi { border: 1px solid var(--jw-line); border-radius: 12px; padding: 0.85rem 1rem;
          background: #fff; height: 100%; }
.jw-kpi-label { color: var(--jw-muted); font-size: 0.76rem; text-transform: uppercase;
                letter-spacing: 0.07em; font-weight: 600; }
.jw-kpi-value { font-size: 1.85rem; font-weight: 700; line-height: 1.25; color: var(--jw-ink); }
.jw-kpi-hint { color: var(--jw-muted); font-size: 0.78rem; }

.jw-reason { color: var(--jw-good); font-size: 0.85rem; margin: 0.08rem 0; }
.jw-note { color: var(--jw-muted); font-size: 0.85rem; margin: 0.08rem 0; }
.jw-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem;
           word-break: break-all; }

.jw-contact { border-left: 3px solid var(--jw-line); padding: 0.35rem 0 0.35rem 0.7rem;
              margin: 0.35rem 0; }
.jw-contact-name { font-weight: 620; color: var(--jw-ink); font-size: 0.92rem; }
.jw-contact-title { color: var(--jw-muted); font-size: 0.83rem; }

.jw-empty { border: 1px dashed var(--jw-line); border-radius: 12px; padding: 2rem 1.5rem;
            text-align: center; color: var(--jw-muted); background: var(--jw-bg-soft); }
.jw-empty-title { font-weight: 650; color: var(--jw-ink); font-size: 1rem;
                  margin-bottom: 0.35rem; }

.jw-bar { height: 6px; background: #eef1f5; border-radius: 99px; overflow: hidden;
          margin-top: 0.25rem; }
.jw-bar > span { display: block; height: 100%; border-radius: 99px; }

div[data-testid="stMetricValue"] { font-size: 1.7rem; }
</style>
"""

VALIDATION_STYLES: dict[str, str] = {
    "VALID": "jw-b-good",
    "LIKELY_VALID": "jw-b-good",
    "UNVERIFIED": "jw-b-warn",
    "EXPIRED": "jw-b-bad",
    "BROKEN": "jw-b-bad",
    "MISMATCH": "jw-b-bad",
    "PENDING": "jw-b-mute",
}

APPLICATION_STYLES: dict[str, str] = {
    "SAVED": "jw-b-mute",
    "APPLIED": "jw-b-info",
    "OUTREACH_SENT": "jw-b-info",
    "INTERVIEW": "jw-b-good",
    "OFFER": "jw-b-good",
    "REJECTED": "jw-b-bad",
    "CLOSED": "jw-b-mute",
}

OUTREACH_STYLES: dict[str, str] = {
    "NOT_STARTED": "jw-b-mute",
    "EMAIL_SENT": "jw-b-info",
    "LINKEDIN_SENT": "jw-b-info",
    "REPLIED": "jw-b-good",
    "NO_RESPONSE": "jw-b-warn",
}

EMAIL_STYLES: dict[str, str] = {
    "PUBLISHED_ATTRIBUTED": "jw-b-good",
    "PUBLISHED_TEAM_ALIAS": "jw-b-info",
    "USER_PROVIDED": "jw-b-mute",
    "NONE": "jw-b-mute",
}

CONTACT_ROLE_STYLES: dict[str, str] = {
    "FUNCTION_RECRUITER": "jw-b-good",
    "TALENT_ACQUISITION": "jw-b-good",
    "HIRING_MANAGER": "jw-b-info",
    "TEAM_LEAD": "jw-b-info",
    "TALENT_ALIAS": "jw-b-mute",
    "SEARCH_LINK": "jw-b-mute",
}


# --- Page chrome --------------------------------------------------------------


def page_setup(title: str, icon: str = "🧭") -> None:
    st.set_page_config(page_title=f"{title} · {APP_NAME}", page_icon=icon, layout="wide")
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "") -> None:
    st.markdown(f"# {title}")
    if subtitle:
        st.markdown(f"<div class='jw-sub'>{subtitle}</div>", unsafe_allow_html=True)


def badge(text: str, style: str = "jw-b-mute") -> str:
    return f"<span class='jw-badge {style}'>{escape(text)}</span>"


def escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def validation_badge(validation: dict) -> str:
    status = validation.get("status", "PENDING")
    return badge(validation.get("label", status), VALIDATION_STYLES.get(status, "jw-b-mute"))


def application_badge(status: str | None, label: str | None = None) -> str:
    if not status:
        return ""
    return badge(label or status.replace("_", " ").title(), APPLICATION_STYLES.get(status, "jw-b-mute"))


def outreach_badge(status: str | None, label: str | None = None) -> str:
    if not status:
        return ""
    return badge(label or status.replace("_", " ").title(), OUTREACH_STYLES.get(status, "jw-b-mute"))


def kpi(label: str, value: Any, hint: str = "") -> str:
    return (
        f"<div class='jw-kpi'><div class='jw-kpi-label'>{escape(label)}</div>"
        f"<div class='jw-kpi-value'>{escape(value)}</div>"
        f"<div class='jw-kpi-hint'>{escape(hint)}</div></div>"
    )


def empty_state(title: str, body: str) -> None:
    st.markdown(
        f"<div class='jw-empty'><div class='jw-empty-title'>{escape(title)}</div>"
        f"<div>{escape(body)}</div></div>",
        unsafe_allow_html=True,
    )


def score_bar(score: float) -> str:
    colour = "#0f7a45" if score >= 75 else "#2f57d3" if score >= 55 else "#9a6100"
    width = max(0.0, min(100.0, float(score)))
    return f"<div class='jw-bar'><span style='width:{width:.0f}%;background:{colour}'></span></div>"


# --- Formatting ---------------------------------------------------------------


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def format_dt(value: str | None, fmt: str = "%d %b %Y") -> str:
    parsed = parse_dt(value)
    return parsed.strftime(fmt) if parsed else "—"


def humanize_days(days: float | None) -> str:
    if days is None:
        return "date unknown"
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 14:
        return f"{days:.0f} days ago"
    if days < 60:
        return f"{days / 7:.0f} weeks ago"
    return f"{days / 30:.0f} months ago"


def show_error(exc: Exception) -> None:
    st.error(str(exc))


def sidebar_status(client: Any) -> dict | None:
    """Backend health in the sidebar. Returns None when the API is unreachable."""
    with st.sidebar:
        st.markdown(f"### {APP_NAME}")
        try:
            health = client.health()
        except Exception as exc:  # noqa: BLE001
            st.error("Backend unreachable")
            st.caption(str(exc)[:300])
            st.code("uvicorn app.main:app --reload", language="bash")
            return None

        usable = health.get("sources_usable", 0)
        total = health.get("sources_total", 0)
        st.caption(
            f"{health.get('jobs', 0)} jobs · {health.get('applications', 0)} applications"
        )
        if usable:
            st.caption(f"{usable} of {total} job sources ready")
        else:
            st.warning("No job source is configured yet — open **Sources**.")
        return health
