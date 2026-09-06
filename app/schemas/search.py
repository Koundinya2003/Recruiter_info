"""Request and response shapes for searching."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.search.query import MAX_LIMIT


class SearchRequest(BaseModel):
    """A free-text search request, optionally with corrections applied."""

    query: str = Field(min_length=2, max_length=500)
    limit: int | None = Field(default=None, ge=1, le=MAX_LIMIT)
    find_contacts: bool = True
    use_llm: bool = True
    providers: list[str] | None = None
    # Any of these overrides the value the parser produced, so the user can
    # correct a misreading without rephrasing the whole sentence.
    titles: list[str] | None = None
    locations: list[str] | None = None
    companies: list[str] | None = None
    industries: list[str] | None = None
    keywords: list[str] | None = None
    exclusions: list[str] | None = None
    min_years: float | None = Field(default=None, ge=0, le=40)
    max_years: float | None = Field(default=None, ge=0, le=40)
    remote_only: bool | None = None
    max_age_days: int | None = Field(default=None, ge=1, le=365)

    @field_validator("titles", "locations", "companies", "industries", "keywords", "exclusions")
    @classmethod
    def _clean(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [v.strip()[:120] for v in value if v and v.strip()][:12]


class ParseRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    use_llm: bool = True


class ParsedQueryOut(BaseModel):
    raw: str
    summary: str
    titles: list[str]
    locations: list[str]
    companies: list[str]
    industries: list[str]
    keywords: list[str]
    exclusions: list[str]
    min_years: float | None
    max_years: float | None
    experience_level: str | None
    experience_text: str | None
    remote_only: bool
    max_age_days: int
    limit: int
    parse_method: str
    parse_notes: list[str]


class FunnelOut(BaseModel):
    raw_found: int = 0
    duplicates_dropped: int = 0
    irrelevant_dropped: int = 0
    validated: int = 0
    unverified: int = 0
    rejected: int = 0
    contacts_found: int = 0


class SearchResultOut(BaseModel):
    search_id: int
    run_id: int
    status: str
    query: ParsedQueryOut
    job_ids: list[int]
    funnel: FunnelOut
    providers_queried: list[str]
    providers_skipped: dict[str, str]
    errors: list[str]
    notes: list[str]


class SearchRunOut(ORMModel):
    id: int
    search_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    providers_queried: list[str]
    providers_skipped: dict[str, Any]
    raw_found: int
    duplicates_dropped: int
    irrelevant_dropped: int
    validated: int
    unverified: int
    rejected: int
    contacts_found: int
    errors: list[str]
    notes: list[str]


class SavedSearchOut(ORMModel):
    id: int
    raw_query: str
    label: str | None
    titles: list[str]
    locations: list[str]
    companies: list[str]
    industries: list[str]
    keywords: list[str]
    min_years: float | None
    max_years: float | None
    remote_only: bool
    parse_method: str
    parse_notes: list[str]
    last_run_at: datetime | None
    created_at: datetime


class ProviderStatusOut(BaseModel):
    name: str
    label: str
    coverage: str
    enabled: bool
    configured: bool
    usable: bool
    missing_settings: list[str]
    signup_url: str | None
    remote_only: bool
