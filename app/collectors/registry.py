"""Chooses which collectors to run for a company.

Order of preference for jobs:

1. An official public ATS API, when the career page points at one. Best data,
   sanctioned access, one request.
2. The company's own career page (structured data first).

Recruiter discovery only ever visits URLs the user supplied — the career page
and any extra pages they add. We do not guess at ``/team`` style paths, because
probing URLs a site never advertised is neither polite nor productive.
"""

from __future__ import annotations

from typing import Any

from app.collectors.career_pages.career_page import CareerPageCollector
from app.collectors.job_sources.ats import build_ats_collector, detect_ats
from app.collectors.job_sources.base_job import JobCollectorMixin
from app.collectors.public_sources.recruiter_sources import PublicRecruiterSourceCollector
from app.models.company import Company


def build_job_collectors(company: Company, **kwargs: Any) -> list[JobCollectorMixin]:
    """Job collectors appropriate for ``company``, best source first."""
    collectors: list[JobCollectorMixin] = []
    if not company.career_page_url:
        return collectors

    detection = detect_ats(company.career_page_url)
    if detection is not None:
        collector = build_ats_collector(
            detection, company_name=company.company_name, **kwargs
        )
        if collector is not None:
            collectors.append(collector)
            return collectors

    collectors.append(
        CareerPageCollector(
            company_name=company.company_name,
            career_page_url=company.career_page_url,
            **kwargs,
        )
    )
    return collectors


def build_recruiter_collectors(
    company: Company, *, extra_urls: list[str] | None = None, **kwargs: Any
) -> list[PublicRecruiterSourceCollector]:
    urls = [u for u in [company.career_page_url, *(extra_urls or [])] if u]
    if not urls:
        return []
    return [
        PublicRecruiterSourceCollector(
            company_name=company.company_name,
            urls=urls,
            company_domain=company.company_domain,
            **kwargs,
        )
    ]


def is_authoritative_source(collector: JobCollectorMixin) -> bool:
    """True when the collector lists *all* current openings for the company."""
    return collector.name in {"greenhouse_public_api", "lever_public_api", "ashby_public_api"}
