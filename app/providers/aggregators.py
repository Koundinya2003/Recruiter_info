"""Providers backed by public job-search APIs.

Each of these is a documented, public endpoint whose purpose is to let third
parties search postings. Two need free credentials (Adzuna, USAJobs); the rest
run with no key at all, which is what keeps the product useful before the user
has signed up for anything.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from app.config import settings
from app.models.enums import SourceType
from app.providers.base import JobProvider
from app.providers.types import RawJob
from app.search.gazetteer import ADZUNA_COUNTRIES
from app.search.query import JobQuery
from app.utils.dates import parse_datetime
from app.utils.html import html_to_text
from app.utils.text import basic_normalize

MAX_PER_PROVIDER = 60


def _first(*values: Any) -> Any:
    for value in values:
        if value:
            return value
    return None


class AdzunaProvider(JobProvider):
    """Adzuna's public job search API.

    The broadest source here for non-remote roles, and the one that actually
    covers Indian metros properly. Free credentials at the URL below.
    """

    name = "adzuna"
    label = "Adzuna"
    source = SourceType.ADZUNA
    coverage = "Aggregated postings across 19 countries, including India, the US, the UK and the EU."
    required_settings = ("adzuna_app_id", "adzuna_app_key")
    signup_url = "https://developer.adzuna.com/signup"

    BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"

    def supports(self, query: JobQuery) -> tuple[bool, str | None]:
        if not self._countries(query):
            return False, "no supported country could be resolved from the search locations"
        return True, None

    def _countries(self, query: JobQuery) -> list[str]:
        codes = [c for c in query.countries if c in ADZUNA_COUNTRIES]
        if codes:
            return codes[:3]
        if not query.locations:
            # No location given: default to the country the user's profile
            # implies is not knowable here, so cover the largest index.
            return ["gb", "us"] if not query.remote_only else ["gb"]
        return []

    def search(self, query: JobQuery) -> list[RawJob]:
        out: list[RawJob] = []
        what = " ".join(query.titles[:2]) or " ".join(query.keywords[:3])
        wheres = query.locations or [""]
        per_call = max(10, min(MAX_PER_PROVIDER, query.limit))

        for country in self._countries(query):
            for where in wheres[:3]:
                params = {
                    "app_id": settings.adzuna_app_id,
                    "app_key": settings.adzuna_app_key,
                    "results_per_page": str(per_call),
                    "content-type": "application/json",
                    "max_days_old": str(query.max_age_days),
                    "sort_by": "date",
                }
                if what:
                    params["what"] = what
                if where:
                    params["where"] = where
                if query.keywords:
                    params["what_or"] = " ".join(query.keywords[:5])
                url = f"{self.BASE.format(country=country)}?{urlencode(params)}"
                payload = self.fetch_json(url)
                results = payload.get("results", []) if isinstance(payload, dict) else []
                for item in results:
                    job = self._to_job(item, url)
                    if job is not None:
                        out.append(job)
                if len(out) >= query.limit * 2:
                    return out
        return out

    def _to_job(self, item: Any, query_url: str) -> RawJob | None:
        if not isinstance(item, dict):
            return None
        company = (item.get("company") or {}).get("display_name") if isinstance(
            item.get("company"), dict
        ) else None
        location = (item.get("location") or {}).get("display_name") if isinstance(
            item.get("location"), dict
        ) else None
        salary_min, salary_max = item.get("salary_min"), item.get("salary_max")
        salary = None
        if salary_min or salary_max:
            currency = item.get("salary_currency") or ""
            salary = f"{currency} {salary_min or ''}–{salary_max or ''}".strip()
        return RawJob(
            title=str(item.get("title") or ""),
            company_name=str(company or ""),
            url=str(item.get("redirect_url") or ""),
            source=self.source,
            source_query_url=query_url,
            external_id=str(item.get("id")) if item.get("id") is not None else None,
            location=location,
            is_remote=self.looks_remote(location, item.get("title")),
            employment_type=item.get("contract_time"),
            department=(item.get("category") or {}).get("label")
            if isinstance(item.get("category"), dict)
            else None,
            salary_text=salary,
            description=html_to_text(item.get("description")),
            posted_at=parse_datetime(item.get("created")),
            payload={"adzuna_id": item.get("id")},
        )


class TheMuseProvider(JobProvider):
    """The Muse public jobs API — curated employer postings, no key required."""

    name = "themuse"
    label = "The Muse"
    source = SourceType.THE_MUSE
    coverage = "Curated postings from companies that publish through The Muse. No key required."
    signup_url = "https://www.themuse.com/developers/api/v2"

    BASE = "https://www.themuse.com/api/public/jobs"
    MAX_PAGES = 3

    _LEVELS = {
        "INTERNSHIP": "Internship",
        "ENTRY": "Entry Level",
        "MID": "Mid Level",
        "SENIOR": "Senior Level",
        "LEAD": "Management",
        "EXECUTIVE": "Senior Level",
    }

    def search(self, query: JobQuery) -> list[RawJob]:
        out: list[RawJob] = []
        params: list[tuple[str, str]] = [("page", "0")]
        if settings.the_muse_api_key:
            params.append(("api_key", settings.the_muse_api_key))
        for location in query.locations[:3]:
            params.append(("location", location))
        if query.remote_only:
            params.append(("location", "Flexible / Remote"))
        level = self._LEVELS.get(
            query.experience_level.value if query.experience_level else "", ""
        )
        if not level and query.max_years is not None and query.max_years <= 2:
            level = "Entry Level"
        if level:
            params.append(("level", level))

        wanted = [basic_normalize(t) for t in query.search_titles]
        for page in range(self.MAX_PAGES):
            paged = [("page", str(page))] + [p for p in params if p[0] != "page"]
            url = f"{self.BASE}?{urlencode(paged)}"
            payload = self.fetch_json(url)
            if not isinstance(payload, dict):
                break
            results = payload.get("results") or []
            for item in results:
                job = self._to_job(item, url)
                if job is None:
                    continue
                # The Muse has no free-text search, so titles are filtered here.
                if wanted and not self._title_matches(job.title, wanted):
                    continue
                out.append(job)
            if page + 1 >= int(payload.get("page_count") or 1):
                break
            if len(out) >= query.limit * 2:
                break
        return out

    @staticmethod
    def _title_matches(title: str, wanted: list[str]) -> bool:
        normalized = basic_normalize(title)
        tokens = set(normalized.split())
        for candidate in wanted:
            if candidate and candidate in normalized:
                return True
            parts = set(candidate.split())
            if parts and len(parts & tokens) >= max(1, len(parts) - 1):
                return True
        return False

    def _to_job(self, item: Any, query_url: str) -> RawJob | None:
        if not isinstance(item, dict):
            return None
        company = (item.get("company") or {}).get("name") if isinstance(
            item.get("company"), dict
        ) else None
        locations = [
            str(loc.get("name"))
            for loc in item.get("locations") or []
            if isinstance(loc, dict) and loc.get("name")
        ]
        levels = [
            str(lvl.get("name"))
            for lvl in item.get("levels") or []
            if isinstance(lvl, dict) and lvl.get("name")
        ]
        landing = (item.get("refs") or {}).get("landing_page") if isinstance(
            item.get("refs"), dict
        ) else None
        return RawJob(
            title=str(item.get("name") or ""),
            company_name=str(company or ""),
            url=str(landing or ""),
            source=self.source,
            source_query_url=query_url,
            external_id=str(item.get("id")) if item.get("id") is not None else None,
            location=", ".join(locations) or None,
            is_remote=self.looks_remote(*locations),
            employment_type=item.get("type"),
            department=", ".join(
                str(c.get("name")) for c in item.get("categories") or [] if isinstance(c, dict)
            )
            or None,
            description=html_to_text(item.get("contents")),
            posted_at=parse_datetime(item.get("publication_date")),
            payload={"levels": levels},
        )


class RemotiveProvider(JobProvider):
    """Remotive's public remote-jobs API. Remote roles only, no key required."""

    name = "remotive"
    label = "Remotive"
    source = SourceType.REMOTIVE
    coverage = "Remote-only postings. No key required."
    remote_only_source = True

    BASE = "https://remotive.com/api/remote-jobs"

    def search(self, query: JobQuery) -> list[RawJob]:
        term = query.primary_title or " ".join(query.keywords[:2])
        params = {"limit": str(min(MAX_PER_PROVIDER, max(20, query.limit)))}
        if term:
            params["search"] = term
        url = f"{self.BASE}?{urlencode(params)}"
        payload = self.fetch_json(url)
        results = payload.get("jobs", []) if isinstance(payload, dict) else []
        out: list[RawJob] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            out.append(
                RawJob(
                    title=str(item.get("title") or ""),
                    company_name=str(item.get("company_name") or ""),
                    url=str(item.get("url") or ""),
                    source=self.source,
                    source_query_url=url,
                    external_id=str(item.get("id")) if item.get("id") is not None else None,
                    location=item.get("candidate_required_location") or "Remote",
                    is_remote=True,
                    employment_type=item.get("job_type"),
                    department=item.get("category"),
                    salary_text=item.get("salary") or None,
                    description=html_to_text(item.get("description")),
                    posted_at=parse_datetime(item.get("publication_date")),
                    payload={"tags": item.get("tags") or []},
                )
            )
        return out


class ArbeitnowProvider(JobProvider):
    """Arbeitnow's public job board feed. No key required, no server-side search."""

    name = "arbeitnow"
    label = "Arbeitnow"
    source = SourceType.ARBEITNOW
    coverage = "European and remote postings from the Arbeitnow board. No key required."

    BASE = "https://www.arbeitnow.com/api/job-board-api"
    MAX_PAGES = 3

    def search(self, query: JobQuery) -> list[RawJob]:
        wanted = [basic_normalize(t) for t in query.search_titles]
        out: list[RawJob] = []
        for page in range(1, self.MAX_PAGES + 1):
            url = f"{self.BASE}?page={page}"
            payload = self.fetch_json(url)
            if not isinstance(payload, dict):
                break
            records = payload.get("data") or []
            for item in records:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "")
                if wanted and not TheMuseProvider._title_matches(title, wanted):
                    continue
                out.append(
                    RawJob(
                        title=title,
                        company_name=str(item.get("company_name") or ""),
                        url=str(item.get("url") or ""),
                        source=self.source,
                        source_query_url=url,
                        external_id=str(item.get("slug")) if item.get("slug") else None,
                        location=item.get("location"),
                        is_remote=bool(item.get("remote")),
                        employment_type=", ".join(item.get("job_types") or []) or None,
                        description=html_to_text(item.get("description")),
                        posted_at=parse_datetime(item.get("created_at")),
                        payload={"tags": item.get("tags") or []},
                    )
                )
            if not records or len(out) >= query.limit * 2:
                break
        return out


class JobicyProvider(JobProvider):
    """Jobicy's public remote-jobs API. Remote roles only, no key required."""

    name = "jobicy"
    label = "Jobicy"
    source = SourceType.JOBICY
    coverage = "Remote-only postings, filterable by region. No key required."
    remote_only_source = True

    BASE = "https://jobicy.com/api/v2/remote-jobs"

    def search(self, query: JobQuery) -> list[RawJob]:
        params = {"count": str(min(50, max(20, query.limit)))}
        if query.primary_title:
            params["tag"] = query.primary_title
        url = f"{self.BASE}?{urlencode(params)}"
        payload = self.fetch_json(url)
        results = payload.get("jobs", []) if isinstance(payload, dict) else []
        out: list[RawJob] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            out.append(
                RawJob(
                    title=str(item.get("jobTitle") or ""),
                    company_name=str(item.get("companyName") or ""),
                    url=str(item.get("url") or ""),
                    source=self.source,
                    source_query_url=url,
                    external_id=str(item.get("id")) if item.get("id") is not None else None,
                    location=item.get("jobGeo") or "Remote",
                    is_remote=True,
                    employment_type=", ".join(item.get("jobType") or []) or None,
                    department=", ".join(item.get("jobIndustry") or []) or None,
                    salary_text=_first(item.get("annualSalaryMin"), item.get("salaryCurrency")),
                    description=html_to_text(
                        _first(item.get("jobDescription"), item.get("jobExcerpt"))
                    ),
                    posted_at=parse_datetime(item.get("pubDate")),
                    payload={"level": item.get("jobLevel")},
                )
            )
        return out


class USAJobsProvider(JobProvider):
    """The US federal government's official job search API."""

    name = "usajobs"
    label = "USAJobs"
    source = SourceType.USAJOBS
    coverage = "US federal government postings. Free credentials required."
    required_settings = ("usajobs_api_key", "usajobs_user_agent")
    signup_url = "https://developer.usajobs.gov/apirequest/"

    BASE = "https://data.usajobs.gov/api/search"

    def supports(self, query: JobQuery) -> tuple[bool, str | None]:
        if query.countries and "us" not in query.countries:
            return False, "covers United States postings only"
        return True, None

    def search(self, query: JobQuery) -> list[RawJob]:
        params = {"ResultsPerPage": str(min(50, max(20, query.limit)))}
        if query.primary_title:
            params["Keyword"] = query.primary_title
        if query.locations:
            params["LocationName"] = query.locations[0]
        url = f"{self.BASE}?{urlencode(params)}"
        payload = self.fetch_json(
            url,
            headers={
                "Authorization-Key": settings.usajobs_api_key,
                "User-Agent": settings.usajobs_user_agent,
                "Host": "data.usajobs.gov",
            },
        )
        items = (
            (payload.get("SearchResult") or {}).get("SearchResultItems") or []
            if isinstance(payload, dict)
            else []
        )
        out: list[RawJob] = []
        for entry in items:
            descriptor = (entry or {}).get("MatchedObjectDescriptor") or {}
            if not isinstance(descriptor, dict):
                continue
            locations = [
                str(loc.get("LocationName"))
                for loc in descriptor.get("PositionLocation") or []
                if isinstance(loc, dict) and loc.get("LocationName")
            ]
            summary = (descriptor.get("UserArea") or {}).get("Details") or {}
            out.append(
                RawJob(
                    title=str(descriptor.get("PositionTitle") or ""),
                    company_name=str(
                        _first(descriptor.get("OrganizationName"), descriptor.get("DepartmentName"))
                        or ""
                    ),
                    url=str(descriptor.get("PositionURI") or ""),
                    source=self.source,
                    source_query_url=url,
                    external_id=str(descriptor.get("PositionID") or "") or None,
                    apply_url=(descriptor.get("ApplyURI") or [None])[0],
                    location=", ".join(locations[:3]) or None,
                    employment_type=", ".join(
                        str(s.get("Name"))
                        for s in descriptor.get("PositionSchedule") or []
                        if isinstance(s, dict)
                    )
                    or None,
                    description=html_to_text(
                        _first(summary.get("JobSummary"), descriptor.get("QualificationSummary"))
                    ),
                    posted_at=parse_datetime(descriptor.get("PublicationStartDate")),
                    payload={"grade": descriptor.get("JobGrade")},
                )
            )
        return out


def linkedin_people_search_url(company: str, role_terms: str = "recruiter") -> str:
    """A LinkedIn people-search deep link for a company.

    This is a search the user runs themselves in their own logged-in session.
    It is not a profile, and nothing about it asserts that a particular person
    exists — which is precisely why it is safe to offer.
    """
    keywords = f"{company} {role_terms}".strip()
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(keywords)}"
