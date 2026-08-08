"""Career-page collector.

Preference order, highest quality first:

1. ``schema.org/JobPosting`` JSON-LD embedded in the career page. This is
   structured data the company published *specifically* so that job aggregators
   can read it — the intended, sanctioned way to read a career page.
2. Job-detail links discovered on the page, each fetched (within a strict
   budget) to read its own JSON-LD.
3. Anchor text heuristics, used only when no structured data exists at all, and
   marked as the weaker ``CAREER_PAGE`` source so the provenance is honest.

Pages that require a login, execute their listings purely in client-side
JavaScript, or disallow us in robots.txt simply yield nothing. That is a
reported outcome, not a problem to engineer around.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.collectors.http_client import FetchBlocked
from app.collectors.job_sources.base_job import JobCollectorMixin
from app.collectors.types import CollectorError, RawJob
from app.logging_config import get_logger
from app.models.enums import SourceType
from app.utils.dates import parse_datetime
from app.utils.html import absolute_url, extract_jsonld, html_to_text, make_soup

log = get_logger(__name__)

# Link paths that plausibly point at an individual posting.
JOB_LINK_PATTERNS = (
    re.compile(r"/jobs?/[\w\-]+", re.IGNORECASE),
    re.compile(r"/careers?/[\w\-]+", re.IGNORECASE),
    re.compile(r"/openings?/[\w\-]+", re.IGNORECASE),
    re.compile(r"/positions?/[\w\-]+", re.IGNORECASE),
    re.compile(r"/vacanc(?:y|ies)/[\w\-]+", re.IGNORECASE),
    re.compile(r"/opportunit(?:y|ies)/[\w\-]+", re.IGNORECASE),
)

# Anchors that are navigation, not postings.
LINK_STOPWORDS = (
    "privacy", "cookie", "terms", "login", "sign in", "sign up", "about",
    "contact", "blog", "press", "life at", "culture", "benefits", "our team",
    "why join", "faq", "apply now", "search", "all jobs", "view all",
)


class CareerPageCollector(JobCollectorMixin):
    """Reads a company's own career page, structured data first."""

    name = "career_page"
    source_type = SourceType.CAREER_PAGE_JSONLD

    def __init__(
        self,
        *,
        company_name: str,
        career_page_url: str,
        max_detail_pages: int = 8,
        **kwargs: Any,
    ) -> None:
        super().__init__(company_name=company_name, **kwargs)
        self.career_page_url = career_page_url
        self.max_detail_pages = max_detail_pages

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _is_job_posting(block: dict[str, Any]) -> bool:
        type_field = block.get("@type") or block.get("type")
        if isinstance(type_field, list):
            return any(str(t).lower() == "jobposting" for t in type_field)
        return str(type_field).lower() == "jobposting"

    @staticmethod
    def _location_from_jsonld(block: dict[str, Any]) -> str | None:
        location = block.get("jobLocation")
        if isinstance(location, list):
            location = location[0] if location else None
        if isinstance(location, dict):
            address = location.get("address")
            if isinstance(address, dict):
                parts = [
                    address.get("addressLocality"),
                    address.get("addressRegion"),
                    address.get("addressCountry")
                    if isinstance(address.get("addressCountry"), str)
                    else (address.get("addressCountry") or {}).get("name"),
                ]
                joined = ", ".join(str(p) for p in parts if p)
                if joined:
                    return joined
            if location.get("name"):
                return str(location["name"])
        if isinstance(location, str):
            return location
        if str(block.get("jobLocationType", "")).upper() == "TELECOMMUTE":
            return "Remote"
        return None

    def _job_from_jsonld(self, block: dict[str, Any], page_url: str) -> RawJob | None:
        title = block.get("title") or block.get("name")
        if not title:
            return None
        url = block.get("url") or block.get("sameAs") or page_url
        employment = block.get("employmentType")
        if isinstance(employment, list):
            employment = ", ".join(str(e) for e in employment)
        hiring_org = block.get("hiringOrganization")
        org_name = (
            hiring_org.get("name") if isinstance(hiring_org, dict) else hiring_org
        )
        return RawJob(
            title=str(title),
            url=str(url),
            source=SourceType.CAREER_PAGE_JSONLD,
            source_url=page_url,
            external_id=str(block.get("identifier"))
            if isinstance(block.get("identifier"), (str, int))
            else None,
            description=html_to_text(block.get("description")),
            location=self._location_from_jsonld(block),
            employment_type=str(employment) if employment else None,
            posted_at=parse_datetime(block.get("datePosted")),
            payload={
                "hiring_organization": org_name,
                "valid_through": block.get("validThrough"),
                "structured_data": True,
            },
        )

    def _discover_job_links(self, html: str, page_url: str) -> list[tuple[str, str]]:
        """Return ``(url, anchor_text)`` for plausible job-detail links."""
        soup = make_soup(html)
        base_host = (urlparse(page_url).hostname or "").lower()
        found: dict[str, str] = {}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"]).strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            target = absolute_url(page_url, href)
            parsed = urlparse(target)
            if parsed.scheme not in {"http", "https"}:
                continue
            # Stay on the company's own site or a known ATS host.
            host = (parsed.hostname or "").lower()
            if host != base_host and not any(
                platform in host for platform in ("greenhouse.io", "lever.co", "ashbyhq.com")
            ):
                continue
            if not any(pattern.search(parsed.path) for pattern in JOB_LINK_PATTERNS):
                continue
            text = anchor.get_text(" ", strip=True)
            if text and any(stop in text.lower() for stop in LINK_STOPWORDS):
                continue
            if target not in found:
                found[target] = text
        return list(found.items())

    # -- pipeline ------------------------------------------------------------
    def collect(self) -> list[RawJob]:
        response = self.client.fetch(self.career_page_url)
        html = response.text
        page_url = response.final_url

        jobs: list[RawJob] = []
        for block in extract_jsonld(html):
            if self._is_job_posting(block):
                job = self._job_from_jsonld(block, page_url)
                if job is not None:
                    jobs.append(job)

        if jobs:
            self.outcome.notes.append(
                f"{len(jobs)} posting(s) read from schema.org JSON-LD on the career page"
            )
            return jobs

        links = self._discover_job_links(html, page_url)
        if not links:
            self.outcome.notes.append(
                "No structured job data and no job-detail links found. The listings are "
                "probably rendered client-side; add the company's ATS board URL instead."
            )
            return []

        self.outcome.notes.append(f"{len(links)} candidate job link(s) found on the career page")

        budget = min(self.max_detail_pages, len(links))
        fallback: list[RawJob] = []
        for url, anchor_text in links[:budget]:
            if self.client.budget_exhausted:
                self.outcome.notes.append("Stopped early: crawl page budget exhausted")
                break
            try:
                detail = self.client.fetch(url)
            except FetchBlocked as exc:
                self.outcome.errors.append(
                    CollectorError(stage="collect", message=str(exc), url=url)
                )
                continue
            except Exception as exc:  # noqa: BLE001
                self.outcome.errors.append(
                    CollectorError(
                        stage="collect", message=f"{type(exc).__name__}: {exc}", url=url
                    )
                )
                continue

            detail_jobs = [
                job
                for block in extract_jsonld(detail.text)
                if self._is_job_posting(block)
                for job in [self._job_from_jsonld(block, detail.final_url)]
                if job is not None
            ]
            if detail_jobs:
                jobs.extend(detail_jobs)
            elif anchor_text:
                # Weakest path: the anchor text is the only title we have.
                soup = make_soup(detail.text)
                heading = soup.find(["h1", "h2"])
                title = (heading.get_text(" ", strip=True) if heading else "") or anchor_text
                fallback.append(
                    RawJob(
                        title=title,
                        url=detail.final_url,
                        source=SourceType.CAREER_PAGE,
                        source_url=page_url,
                        description=html_to_text(detail.text, limit=8000),
                        payload={"structured_data": False, "anchor_text": anchor_text},
                    )
                )

        if jobs:
            self.outcome.notes.append(
                f"{len(jobs)} posting(s) read from JSON-LD on individual job pages"
            )
        if fallback:
            self.outcome.notes.append(
                f"{len(fallback)} posting(s) inferred from page headings — lower confidence, "
                "no structured data was published"
            )
        return jobs + fallback
