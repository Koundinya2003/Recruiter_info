"""Shared UI vocabulary: theme, badges, score displays, empty states.

Keeping these in one place is what makes the app read as a product rather than
a pile of Streamlit widgets — a status badge means the same thing on every page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import streamlit as st

THEME_CSS = """
<style>
:root {
  --roi-ink: #10151f;
  --roi-muted: #5b6675;
  --roi-line: #e3e7ee;
  --roi-bg-soft: #f6f8fb;
  --roi-accent: #2f57d3;
  --roi-good: #0f7a45;
  --roi-warn: #9a6100;
  --roi-bad: #b3261e;
}
html, body, [class*="css"] { font-feature-settings: "tnum" 1, "cv05" 1; }
.block-container { padding-top: 2.2rem; max-width: 1400px; }
h1 { font-size: 1.85rem !important; font-weight: 700 !important; letter-spacing: -0.02em; }
h2 { font-size: 1.25rem !important; font-weight: 650 !important; letter-spacing: -0.01em;
     margin-top: 1.6rem !important; }
h3 { font-size: 1.02rem !important; font-weight: 620 !important; }

.roi-sub { color: var(--roi-muted); font-size: 0.94rem; margin-top: -0.5rem;
           margin-bottom: 1.2rem; }

.roi-badge { display: inline-block; padding: 0.13rem 0.55rem; border-radius: 999px;
             font-size: 0.72rem; font-weight: 650; letter-spacing: 0.02em;
             white-space: nowrap; border: 1px solid transparent; }
.roi-b-good { background: #e6f4ec; color: var(--roi-good); border-color: #bfe3cf; }
.roi-b-warn { background: #fdf2df; color: var(--roi-warn); border-color: #f3ddb0; }
.roi-b-bad  { background: #fdeceb; color: var(--roi-bad);  border-color: #f5c9c6; }
.roi-b-info { background: #eaeffc; color: var(--roi-accent); border-color: #c9d5f7; }
.roi-b-mute { background: #eef1f5; color: var(--roi-muted); border-color: var(--roi-line); }

.roi-card { border: 1px solid var(--roi-line); border-radius: 12px; padding: 1rem 1.15rem;
            background: #fff; margin-bottom: 0.85rem; }
.roi-card-hi { border-left: 4px solid var(--roi-accent); }

.roi-kpi { border: 1px solid var(--roi-line); border-radius: 12px; padding: 0.85rem 1rem;
           background: #fff; height: 100%; }
.roi-kpi-label { color: var(--roi-muted); font-size: 0.76rem; text-transform: uppercase;
                 letter-spacing: 0.07em; font-weight: 600; }
.roi-kpi-value { font-size: 1.85rem; font-weight: 700; line-height: 1.25; color: var(--roi-ink); }
.roi-kpi-hint { color: var(--roi-muted); font-size: 0.78rem; }

.roi-reason { color: var(--roi-good); font-size: 0.87rem; margin: 0.1rem 0; }
.roi-gap { color: var(--roi-muted); font-size: 0.87rem; margin: 0.1rem 0; }
.roi-meta { color: var(--roi-muted); font-size: 0.84rem; }
.roi-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; }

.roi-empty { border: 1px dashed var(--roi-line); border-radius: 12px; padding: 2rem 1.5rem;
             text-align: center; color: var(--roi-muted); background: var(--roi-bg-soft); }
.roi-empty-title { font-weight: 650; color: var(--roi-ink); font-size: 1rem;
                   margin-bottom: 0.35rem; }

.roi-bar { height: 7px; background: #eef1f5; border-radius: 99px; overflow: hidden; }
.roi-bar > span { display: block; height: 100%; border-radius: 99px; }

div[data-testid="stMetricValue"] { font-size: 1.7rem; }
</style>
"""

PRIORITY_STYLES: dict[str, str] = {
    "CONTACT NOW": "roi-b-good",
    "HIGH PRIORITY": "roi-b-info",
    "GOOD OPPORTUNITY": "roi-b-info",
    "REVIEW": "roi-b-warn",
    "LOW PRIORITY": "roi-b-mute",
}

STATUS_STYLES: dict[str, str] = {
    "NEW": "roi-b-info",
    "REVIEWED": "roi-b-info",
    "APPROVED": "roi-b-good",
    "CONTACTED": "roi-b-good",
    "REPLIED": "roi-b-good",
    "FOLLOW_UP": "roi-b-warn",
    "ARCHIVED": "roi-b-mute",
    "DO_NOT_CONTACT": "roi-b-bad",
}

VERIFICATION_STYLES: dict[str, str] = {
    "valid": "roi-b-good",
    "risky": "roi-b-warn",
    "invalid": "roi-b-bad",
    "unknown": "roi-b-mute",
    "not_checked": "roi-b-mute",
}

CRAWL_STYLES: dict[str, str] = {
    "SUCCESS": "roi-b-good",
    "PARTIAL": "roi-b-warn",
    "BLOCKED": "roi-b-warn",
    "FAILED": "roi-b-bad",
    "RUNNING": "roi-b-info",
    "SKIPPED": "roi-b-mute",
}


def page_setup(title: str, icon: str = "🎯") -> None:
    st.set_page_config(page_title=f"{title} · Recruiter Outreach Intelligence", page_icon=icon, layout="wide")
    st.markdown(THEME_CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "") -> None:
    st.markdown(f"# {title}")
    if subtitle:
        st.markdown(f'<div class="roi-sub">{subtitle}</div>', unsafe_allow_html=True)


def badge(text: str, style: str = "roi-b-mute") -> str:
    return f'<span class="roi-badge {style}">{text}</span>'


def priority_badge(band: str) -> str:
    return badge(band, PRIORITY_STYLES.get(band, "roi-b-mute"))


def status_badge(status: str) -> str:
    return badge(status.replace("_", " "), STATUS_STYLES.get(status, "roi-b-mute"))


def email_badge(
    email: str | None, *, verified: bool, status: str, is_inferred: bool, confidence: str
) -> str:
    """The one place that decides how an address is presented.

    A verified public address and a pattern-inferred guess must never look the
    same, so this function is the single source of that distinction.
    """
    if not email:
        return badge("No address", "roi-b-mute")
    if is_inferred:
        return badge("INFERRED — not published", "roi-b-bad")
    if verified and status == "valid":
        return badge("✓ Verified", "roi-b-good")
    label = {
        "risky": "Risky",
        "invalid": "Invalid",
        "unknown": "Unverified",
        "not_checked": "Not checked",
    }.get(status, "Unverified")
    prefix = "Public" if confidence == "HIGH" else "Company"
    return badge(f"{prefix} · {label}", VERIFICATION_STYLES.get(status, "roi-b-mute"))


def confidence_badge(confidence: str) -> str:
    styles = {
        "HIGH": ("HIGH confidence", "roi-b-good"),
        "MEDIUM": ("MEDIUM confidence", "roi-b-warn"),
        "LOW": ("LOW — inferred", "roi-b-bad"),
        "NONE": ("No address", "roi-b-mute"),
    }
    text, style = styles.get(confidence, ("Unknown", "roi-b-mute"))
    return badge(text, style)


def demo_badge() -> str:
    return badge("DEMO DATA", "roi-b-warn")


def score_color(score: float) -> str:
    if score >= 85:
        return "#0f7a45"
    if score >= 70:
        return "#2f57d3"
    if score >= 50:
        return "#9a6100"
    return "#9aa3af"


def score_bar(score: float, label: str = "") -> str:
    pct = max(0.0, min(100.0, float(score)))
    return (
        f'<div class="roi-meta">{label} <strong style="color:{score_color(pct)}">'
        f"{pct:.0f}</strong>/100</div>"
        f'<div class="roi-bar"><span style="width:{pct}%;background:{score_color(pct)}"></span></div>'
    )


def kpi(label: str, value: Any, hint: str = "") -> str:
    return (
        f'<div class="roi-kpi"><div class="roi-kpi-label">{label}</div>'
        f'<div class="roi-kpi-value">{value}</div>'
        f'<div class="roi-kpi-hint">{hint}</div></div>'
    )


def empty_state(title: str, body: str, action: str = "") -> None:
    action_html = f'<div style="margin-top:0.6rem"><strong>{action}</strong></div>' if action else ""
    st.markdown(
        f'<div class="roi-empty"><div class="roi-empty-title">{title}</div>'
        f"<div>{body}</div>{action_html}</div>",
        unsafe_allow_html=True,
    )


def render_breakdown(breakdown: dict[str, Any], *, show_gaps: bool = True) -> None:
    """Render a score explanation. Never show a number without its reasons."""
    if not breakdown or not breakdown.get("components"):
        st.caption("No score breakdown available yet.")
        return

    total = breakdown.get("total", 0)
    st.markdown(score_bar(total, "Score"), unsafe_allow_html=True)

    if breakdown.get("excluded"):
        st.error(f"Excluded: {breakdown.get('exclusion_reason', 'no reason recorded')}")

    for component in breakdown["components"]:
        points = component.get("points", 0)
        maximum = component.get("max_points", 0)
        pct = (points / maximum * 100) if maximum else 0
        cols = st.columns([3, 1, 6])
        cols[0].markdown(f"**{component['label']}**")
        cols[1].markdown(
            f'<span style="color:{score_color(pct)};font-weight:650">{points:g}</span>'
            f'<span class="roi-meta">/{maximum:g}</span>',
            unsafe_allow_html=True,
        )
        with cols[2]:
            for reason in component.get("reasons", []):
                mark = "✓" if component.get("matched") and points > 0 else "✗"
                css = "roi-reason" if mark == "✓" else "roi-gap"
                st.markdown(f'<div class="{css}">{mark} {reason}</div>', unsafe_allow_html=True)

    if show_gaps:
        for penalty in breakdown.get("penalties", []):
            for reason in penalty.get("reasons", []):
                st.markdown(f'<div class="roi-gap">− {reason}</div>', unsafe_allow_html=True)


def reasons_list(reasons: list[str], limit: int = 5) -> None:
    for reason in reasons[:limit]:
        st.markdown(f'<div class="roi-reason">✓ {reason}</div>', unsafe_allow_html=True)


def humanize_hours(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 1:
        return f"{max(1, int(hours * 60))}m ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def format_dt(value: str | None, fmt: str = "%d %b %Y, %H:%M") -> str:
    parsed = parse_dt(value)
    return parsed.strftime(fmt) if parsed else "—"


def show_error(exc: Exception) -> None:
    from frontend.api_client import APIError

    if isinstance(exc, APIError) and exc.status_code == 0:
        st.error(str(exc))
        st.info(
            "Start the backend with:\n\n```\nuvicorn app.main:app --reload\n```",
            icon="💡",
        )
    else:
        st.error(str(exc))


def sidebar_status(client: Any) -> dict | None:
    """Backend status panel shown on every page."""
    with st.sidebar:
        st.markdown("### System")
        try:
            health = client.health()
        except Exception as exc:  # noqa: BLE001
            st.error("Backend unreachable")
            st.caption(str(exc)[:160])
            return None

        st.markdown(
            badge("API online", "roi-b-good")
            + " "
            + badge(f"DB {health['database']}", "roi-b-good" if health["database"] == "ok" else "roi-b-bad"),
            unsafe_allow_html=True,
        )
        ai_style = "roi-b-good" if health["ai_configured"] else "roi-b-warn"
        ai_label = "AI: configured" if health["ai_configured"] else "AI: offline template"
        st.markdown(
            badge(ai_label, ai_style)
            + " "
            + badge(f"Verify: {health['email_verification_provider']}", "roi-b-info"),
            unsafe_allow_html=True,
        )
        if health.get("demo_records"):
            st.markdown(demo_badge(), unsafe_allow_html=True)
            st.caption(
                f"{health['demo_records']} demo companies are loaded. Synthetic records are "
                "marked [DEMO] and use unreachable `.example` addresses."
            )
        st.divider()
        st.caption(
            "This tool never sends email. It prepares drafts you approve and send yourself."
        )
        return health
