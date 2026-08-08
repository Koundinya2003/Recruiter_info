"""Collectors for the public job-board APIs of common ATS platforms.

Greenhouse, Lever and Ashby each publish a documented, unauthenticated JSON
endpoint whose entire purpose is to let anyone render a company's open roles.
Using them is the *legitimate* path: no scraping, no session, no bypass, and
far better data quality than parsing rendered HTML.

If a company's board is not on one of these platforms the career-page collector
takes over.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.collectors.job_sources.base_job import JobCollectorMixin
from app.collectors.types import RawJob
from app.logging_config import get_logger
from app.models.enums import SourceType
from app.utils.dates import parse_datetime
from app.utils.html import html_to_text

log = get_logger(__name__)


class ATSDetection:
    """Which ATS a career-page URL belongs to, and the board identifier."""

    def __init__(self, platform: str, board_token: str) -> None:
        self.platform = platform
        self.board_token = board_token

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ATSDetection {self.platform}:{self.board_token}>"


_ATS_PATTERNS: tuple[tuple[str, re.Pattern[str], re.Pattern[str] | None], ...] = (
    (
        "greenhouse",
        re.compile(r"(?:^|\.)greenhouse\.io$|(?:^|\.)job-boards\.greenhouse\.io$"),
        re.compile(r"/(?:embed/job_board\?for=)?([A-Za-z0-9_\-]+)"),
    ),
    ("lever", re.compile(r"(?:^|\.)lever\.co$"), re.compile(r"/([A-Za-z0-9_\-]+)")),
    ("ashby", re.compile(r"(?:^|\.)ashbyhq\.com$"), re.compile(r"/([A-Za-z0-9_\-%. ]+)")),
)


def detect_ats(url: str | None) -> ATSDetection | None:
    """Identify a supported ATS board from a career-page URL."""
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"

    for platform, host_pattern, path_pattern in _ATS_PATTERNS:
        if not host_pattern.search(host):
            continue
        token: str | None = None
        if platform == "greenhouse" and "for=" in (parsed.query or ""):
            match = re.search(r"for=([A-Za-z0-9_\-]+)", parsed.query)
            token = match.group(1) if match else None
        if token is None and path_pattern is not None:
            match = path_pattern.search(path)
            token = match.group(1) if match else None
        if token and token.lower() not in {"embed", "jobs", "job", "boards"}:
            return ATSDetection(platform, token.strip("/ ").strip())
    return None


class GreenhouseCollector(JobCollectorMixin):
    """Greenhouse public job board API (`boards-api.greenhouse.io`)."""

    name = "greenhouse_public_api"
    source_type = SourceType.GREENHOUSE_PUBLIC_API

    API_TEMPLATE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

    def __init__(self, *, company_name: str, board_token: str, **kwargs: Any) -> None:
        super().__init__(company_name=company_name, **kwargs)
        self.board_token = board_token
        self.api_url = self.options.get("api_url") or self.API_TEMPLATE.format(token=board_token)

    def collect(self) -> list[RawJob]:
        response = self.client.fetch(self.api_url)
        data = response.json()
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        out: list[RawJob] = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            location = (item.get("location") or {}).get("name") if isinstance(item.get("location"), dict) else item.get("location")
            url = item.get("absolute_url") or ""
            out.append(
                RawJob(
                    title=str(item.get("title") or ""),
                    url=str(url),
                    source=self.source_type,
                    source_url=self.api_url,
                    external_id=str(item.get("id")) if item.get("id") is not None else None,
                    description=html_to_text(item.get("content")),
                    location=str(location) if location else None,
                    posted_at=parse_datetime(
                        item.get("first_published") or item.get("updated_at")
                    ),
                    department=", ".join(
                        d.get("name", "")
                        for d in (item.get("departments") or [])
                        if isinstance(d, dict)
                    )
                    or None,
                    payload={"id": item.get("id"), "requisition_id": item.get("requisition_id")},
                )
            )
        return out


class LeverCollector(JobCollectorMixin):
    """Lever public postings API (`api.lever.co/v0/postings`)."""

    name = "lever_public_api"
    source_type = SourceType.LEVER_PUBLIC_API

    API_TEMPLATE = "https://api.lever.co/v0/postings/{token}?mode=json"

    def __init__(self, *, company_name: str, board_token: str, **kwargs: Any) -> None:
        super().__init__(company_name=company_name, **kwargs)
        self.board_token = board_token
        self.api_url = self.options.get("api_url") or self.API_TEMPLATE.format(token=board_token)

    def collect(self) -> list[RawJob]:
        response = self.client.fetch(self.api_url)
        data = response.json()
        if not isinstance(data, list):
            return []
        out: list[RawJob] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            categories = item.get("categories") or {}
            description = item.get("descriptionPlain") or html_to_text(item.get("description"))
            out.append(
                RawJob(
                    title=str(item.get("text") or ""),
                    url=str(item.get("hostedUrl") or item.get("applyUrl") or ""),
                    source=self.source_type,
                    source_url=self.api_url,
                    external_id=str(item.get("id")) if item.get("id") else None,
                    description=description,
                    location=categories.get("location"),
                    employment_type=categories.get("commitment"),
                    posted_at=parse_datetime(item.get("createdAt")),
                    department=categories.get("team"),
                    payload={"id": item.get("id"), "team": categories.get("team")},
                )
            )
        return out


class AshbyCollector(JobCollectorMixin):
    """Ashby public job board API (`api.ashbyhq.com/posting-api/job-board`)."""

    name = "ashby_public_api"
    source_type = SourceType.ASHBY_PUBLIC_API

    API_TEMPLATE = "https://api.ashbyhq.com/posting-api/job-board/{token}"

    def __init__(self, *, company_name: str, board_token: str, **kwargs: Any) -> None:
        super().__init__(company_name=company_name, **kwargs)
        self.board_token = board_token
        self.api_url = self.options.get("api_url") or self.API_TEMPLATE.format(token=board_token)

    def collect(self) -> list[RawJob]:
        response = self.client.fetch(self.api_url)
        data = response.json()
        jobs = data.get("jobs", []) if isinstance(data, dict) else []
        out: list[RawJob] = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            out.append(
                RawJob(
                    title=str(item.get("title") or ""),
                    url=str(item.get("jobUrl") or item.get("applyUrl") or ""),
                    source=self.source_type,
                    source_url=self.api_url,
                    external_id=str(item.get("id")) if item.get("id") else None,
                    description=item.get("descriptionPlain")
                    or html_to_text(item.get("descriptionHtml")),
                    location=item.get("location"),
                    employment_type=item.get("employmentType"),
                    posted_at=parse_datetime(item.get("publishedAt") or item.get("updatedAt")),
                    department=item.get("department") or item.get("team"),
                    payload={"id": item.get("id"), "isRemote": item.get("isRemote")},
                )
            )
        return out


ATS_COLLECTORS: dict[str, type[JobCollectorMixin]] = {
    "greenhouse": GreenhouseCollector,
    "lever": LeverCollector,
    "ashby": AshbyCollector,
}


def build_ats_collector(
    detection: ATSDetection, *, company_name: str, **kwargs: Any
) -> JobCollectorMixin | None:
    collector_cls = ATS_COLLECTORS.get(detection.platform)
    if collector_cls is None:
        return None
    return collector_cls(  # type: ignore[call-arg]
        company_name=company_name, board_token=detection.board_token, **kwargs
    )
