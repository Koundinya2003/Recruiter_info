"""What a job provider hands back.

A ``RawJob`` is a posting exactly as a source described it. Nothing here is
derived, inferred or filled in with a plausible default — a field the source
did not give stays ``None`` all the way to the UI, which says "not stated".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models.enums import SourceType


@dataclass
class RawJob:
    title: str
    company_name: str
    url: str
    source: SourceType
    source_query_url: str

    external_id: str | None = None
    apply_url: str | None = None
    location: str | None = None
    is_remote: bool = False
    employment_type: str | None = None
    department: str | None = None
    salary_text: str | None = None
    description: str | None = None
    posted_at: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = " ".join((self.title or "").split())
        self.company_name = " ".join((self.company_name or "").split())
        self.url = (self.url or "").strip()


@dataclass
class ProviderResult:
    """One provider's answer to one query."""

    provider: str
    source: SourceType
    jobs: list[RawJob] = field(default_factory=list)
    query_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    pages_fetched: int = 0

    @property
    def ok(self) -> bool:
        return self.skipped_reason is None and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source": self.source.value,
            "found": len(self.jobs),
            "errors": self.errors,
            "notes": self.notes,
            "skipped_reason": self.skipped_reason,
            "pages_fetched": self.pages_fetched,
        }
