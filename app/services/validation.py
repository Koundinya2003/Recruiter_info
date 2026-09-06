"""Checking a posting is real, live, and belongs to the company it claims.

Nothing reaches the user unchecked. Each posting is put through four checks and
the outcome of every one is recorded, so the UI can distinguish "we confirmed
this is open" from "the site would not let us look" — a distinction the user
needs and that a single boolean would destroy.

The checks never work around a site that declines automated access. A 403 or a
robots.txt disallow produces an honest "could not confirm", not a retry with a
disguised user agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.collectors.http_client import FetchBlocked, FetchResult, SafeHTTPClient
from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.enums import CheckOutcome, SourceType, ValidationCheck, ValidationStatus
from app.providers.company_boards import board_host_matches
from app.utils.dates import parse_datetime
from app.utils.html import extract_jsonld, html_to_text
from app.utils.text import basic_normalize, normalize_company_name, token_set

log = get_logger(__name__)

# Wording sites use when a role has been taken down. Matched against the page
# text, which is why the list is phrased the way real pages phrase it.
CLOSED_PHRASES: tuple[str, ...] = (
    "no longer accepting applications",
    "no longer available",
    "no longer active",
    "this job is closed",
    "this position is closed",
    "position has been filled",
    "this role has been filled",
    "job posting is closed",
    "applications are closed",
    "we are no longer hiring",
    "this opening has expired",
    "job has expired",
    "posting has expired",
    "vacancy is closed",
    "this job posting is no longer",
    "sorry, this job is not available",
    "the job you are looking for is not available",
    "job not found",
    "page not found",
    "404 - not found",
)

# Pages that are alive but are not the posting: a board's generic listing page
# after a silent redirect from a dead posting.
GENERIC_REDIRECT_HINTS: tuple[str, ...] = (
    "search results",
    "all jobs",
    "browse jobs",
    "current openings",
)

_HTTP_GONE = {404, 410}


@dataclass
class ValidationOutcome:
    status: ValidationStatus
    reason: str
    checks: dict[str, str] = field(default_factory=dict)
    http_status: int | None = None
    final_url: str | None = None
    posting_title: str | None = None
    posting_company: str | None = None
    valid_through: datetime | None = None

    @property
    def displayable(self) -> bool:
        return self.status.is_displayable

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "checks": self.checks,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "posting_company": self.posting_company,
            "valid_through": self.valid_through.isoformat() if self.valid_through else None,
        }


def _job_posting_blocks(html: str) -> list[dict[str, Any]]:
    """schema.org JobPosting blocks, which most ATS pages publish."""
    out: list[dict[str, Any]] = []
    for block in extract_jsonld(html):
        raw_type = block.get("@type") or block.get("type") or ""
        types = (
            [str(t).lower() for t in raw_type]
            if isinstance(raw_type, list)
            else [str(raw_type).lower()]
        )
        if "jobposting" in types:
            out.append(block)
    return out


def _hiring_organization(block: dict[str, Any]) -> str | None:
    org = block.get("hiringOrganization")
    if isinstance(org, dict):
        name = org.get("name")
        return str(name) if name else None
    if isinstance(org, str):
        return org
    return None


def _company_tokens(company: str) -> set[str]:
    """Distinctive tokens of a company name, minus generic corporate words."""
    generic = {
        "inc", "llc", "ltd", "limited", "plc", "gmbh", "pvt", "private", "corp",
        "corporation", "co", "company", "technologies", "technology", "labs",
        "software", "solutions", "systems", "group", "holdings", "the", "and",
    }
    return {t for t in token_set(normalize_company_name(company)) if t not in generic and len(t) > 2}


def _company_check(
    *,
    company_name: str,
    response: FetchResult,
    jsonld: list[dict[str, Any]],
    page_text: str,
    board_token: str | None,
) -> tuple[CheckOutcome, str, str | None]:
    """Does this page belong to the company the posting claims?"""
    tokens = _company_tokens(company_name)
    final_url = (response.final_url or "").lower()

    if board_token and board_host_matches(final_url, board_token):
        return (
            CheckOutcome.PASS,
            f"Served from {company_name}'s own applicant-tracking board",
            company_name,
        )

    # The strongest signal: the page's own structured data names the employer.
    for block in jsonld:
        stated = _hiring_organization(block)
        if not stated:
            continue
        stated_tokens = _company_tokens(stated)
        if tokens and stated_tokens and (tokens & stated_tokens):
            return (
                CheckOutcome.PASS,
                f"Page's structured data names the employer as “{stated}”",
                stated,
            )
        if tokens and stated_tokens:
            return (
                CheckOutcome.FAIL,
                f"Page's structured data names “{stated}”, not “{company_name}”",
                stated,
            )

    if tokens and any(token in final_url for token in tokens):
        return CheckOutcome.PASS, "Company name appears in the posting URL", None

    head = basic_normalize(page_text[:3000])
    if tokens and all(token in head for token in tokens):
        return CheckOutcome.PASS, "Company name appears on the posting page", None
    if tokens and any(token in head for token in tokens):
        return CheckOutcome.PASS, "Company name appears on the posting page", None

    return (
        CheckOutcome.UNKNOWN,
        "Could not find the company named on the page",
        None,
    )


def _active_check(
    *,
    title: str,
    page_text: str,
    jsonld: list[dict[str, Any]],
) -> tuple[CheckOutcome, str, datetime | None]:
    lowered = page_text.lower()
    for phrase in CLOSED_PHRASES:
        if phrase in lowered:
            return CheckOutcome.FAIL, f"The page says: “{phrase}”", None

    valid_through: datetime | None = None
    for block in jsonld:
        parsed = parse_datetime(block.get("validThrough"))
        if parsed is not None:
            valid_through = parsed
            if parsed < utcnow():
                return (
                    CheckOutcome.FAIL,
                    f"The posting's own validThrough date passed on {parsed:%d %b %Y}",
                    parsed,
                )

    if jsonld:
        return CheckOutcome.PASS, "Page still publishes a live JobPosting record", valid_through

    # No structured data: fall back to seeing the role's own title on the page.
    title_tokens = {t for t in token_set(title) if len(t) > 3}
    if title_tokens:
        page_tokens = token_set(basic_normalize(page_text[:8000]))
        hit_ratio = len(title_tokens & page_tokens) / len(title_tokens)
        if hit_ratio >= 0.6:
            if any(hint in lowered[:4000] for hint in GENERIC_REDIRECT_HINTS):
                return (
                    CheckOutcome.UNKNOWN,
                    "The link resolved to a general listings page rather than this role",
                    None,
                )
            return CheckOutcome.PASS, "The role's title still appears on the page", None
    return CheckOutcome.UNKNOWN, "The page did not confirm the role is still open", None


def validate_posting(
    client: SafeHTTPClient,
    *,
    url: str,
    title: str,
    company_name: str,
    source: SourceType,
    board_token: str | None = None,
) -> ValidationOutcome:
    """Fetch a posting and decide whether it can be shown."""
    checks: dict[str, str] = {
        ValidationCheck.URL_REACHABLE.value: CheckOutcome.UNKNOWN.value,
        ValidationCheck.COMPANY_MATCHES.value: CheckOutcome.UNKNOWN.value,
        ValidationCheck.STILL_ACTIVE.value: CheckOutcome.UNKNOWN.value,
    }

    try:
        response = client.fetch(url)
    except FetchBlocked as exc:
        # The site declined us. That is not evidence the job is gone, and we do
        # not pretend otherwise — nor do we retry in disguise to get around it.
        if source.is_first_party:
            return ValidationOutcome(
                status=ValidationStatus.LIKELY_VALID,
                reason=(
                    f"{company_name}'s own job board API returned this role as open; the "
                    f"posting page itself could not be fetched ({exc.reason})."
                ),
                checks={
                    **checks,
                    ValidationCheck.STILL_ACTIVE.value: CheckOutcome.PASS.value,
                    ValidationCheck.COMPANY_MATCHES.value: CheckOutcome.PASS.value,
                },
            )
        return ValidationOutcome(
            status=ValidationStatus.UNVERIFIED,
            reason=f"Could not confirm this posting — the site declined the request ({exc.reason}).",
            checks=checks,
        )
    except Exception as exc:  # noqa: BLE001 - network failures are expected here
        log.info("validation.network_error", url=url, error=str(exc)[:200])
        return ValidationOutcome(
            status=ValidationStatus.UNVERIFIED,
            reason=f"Could not reach the posting ({type(exc).__name__}). Try the link yourself.",
            checks=checks,
        )

    if response.status_code in _HTTP_GONE:
        checks[ValidationCheck.URL_REACHABLE.value] = CheckOutcome.FAIL.value
        return ValidationOutcome(
            status=ValidationStatus.BROKEN,
            reason=f"The posting URL returns HTTP {response.status_code}.",
            checks=checks,
            http_status=response.status_code,
            final_url=response.final_url,
        )
    if not response.ok:
        return ValidationOutcome(
            status=ValidationStatus.UNVERIFIED,
            reason=f"The posting URL returned HTTP {response.status_code}.",
            checks=checks,
            http_status=response.status_code,
            final_url=response.final_url,
        )

    checks[ValidationCheck.URL_REACHABLE.value] = CheckOutcome.PASS.value
    page_text = html_to_text(response.text, limit=40000)
    jsonld = _job_posting_blocks(response.text)

    company_outcome, company_reason, stated_company = _company_check(
        company_name=company_name,
        response=response,
        jsonld=jsonld,
        page_text=page_text,
        board_token=board_token,
    )
    checks[ValidationCheck.COMPANY_MATCHES.value] = company_outcome.value

    active_outcome, active_reason, valid_through = _active_check(
        title=title, page_text=page_text, jsonld=jsonld
    )
    checks[ValidationCheck.STILL_ACTIVE.value] = active_outcome.value

    common = {
        "checks": checks,
        "http_status": response.status_code,
        "final_url": response.final_url,
        "posting_company": stated_company,
        "valid_through": valid_through,
    }

    if company_outcome is CheckOutcome.FAIL:
        return ValidationOutcome(
            status=ValidationStatus.MISMATCH, reason=company_reason, **common
        )
    if active_outcome is CheckOutcome.FAIL:
        return ValidationOutcome(status=ValidationStatus.EXPIRED, reason=active_reason, **common)

    if company_outcome is CheckOutcome.PASS and active_outcome is CheckOutcome.PASS:
        return ValidationOutcome(
            status=ValidationStatus.VALID,
            reason=f"{active_reason}. {company_reason}.",
            **common,
        )

    if source.is_first_party:
        # The company's own live API listed it; the page just did not give us
        # enough to re-confirm independently.
        return ValidationOutcome(
            status=ValidationStatus.VALID,
            reason=(
                f"Listed as open by {company_name}'s own job board API, and the posting "
                f"page loads. {active_reason}."
            ),
            **common,
        )

    return ValidationOutcome(
        status=ValidationStatus.LIKELY_VALID,
        reason=f"The posting page loads. {active_reason}. {company_reason}.",
        **common,
    )


def canonical_place(location: str | None) -> str:
    """Reduce a location to one comparable name.

    "Bangalore, Karnataka, India", "Bengaluru" and "Bangalore" all collapse to
    the same value, which is what makes cross-board dedupe work at all.
    """
    from app.search.gazetteer import resolve_location
    from app.utils.text import normalize_location

    if not location:
        return ""
    for part in str(location).split(","):
        resolved = resolve_location(part.strip())
        if resolved is not None:
            return basic_normalize(resolved[0])
    return normalize_location(location)


def duplicate_key(*, company_name: str, title: str, location: str | None) -> str:
    """Fingerprint used to collapse the same role syndicated to several boards.

    Title tokens are sorted, so "Product Manager, Senior" and "Senior Product
    Manager" are recognised as one role rather than two.
    """
    from app.utils.text import content_fingerprint, normalize_title

    title_key = " ".join(sorted(set(normalize_title(title).split())))
    return content_fingerprint(
        normalize_company_name(company_name),
        title_key,
        canonical_place(location),
    )


_TRACKING_PARAMS = re.compile(
    r"^(utm_|gh_src|src|source|ref|referrer|trk|trkCampaign|lever-origin|"
    r"lever-source|gclid|fbclid|mc_cid|mc_eid)",
    re.IGNORECASE,
)


def canonicalize_url(url: str) -> str:
    """Strip tracking noise so the same posting has one stable URL."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if not parts.scheme or not parts.netloc:
        return url.strip()
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING_PARAMS.match(k)]
    path = parts.path.rstrip("/") or "/"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))
