"""Data transfer objects passed between collectors and the ingest services."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models.enums import EmailConfidence, SourceType


@dataclass
class RawJob:
    """A job exactly as the source presented it, before normalisation."""

    title: str
    url: str
    source: SourceType
    source_url: str
    external_id: str | None = None
    description: str | None = None
    location: str | None = None
    employment_type: str | None = None
    posted_at: datetime | None = None
    department: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedJob:
    """A canonicalised job, ready for duplicate detection and persistence."""

    title: str
    normalized_title: str
    url: str
    canonical_url: str
    content_hash: str
    source: SourceType
    source_url: str
    external_id: str | None = None
    description: str | None = None
    location: str | None = None
    normalized_location: str | None = None
    employment_type: str | None = None
    posted_at: datetime | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawRecruiter:
    """A publicly-listed talent contact as found on a public page."""

    name: str
    source: SourceType
    source_url: str
    title: str | None = None
    email: str | None = None
    profile_url: str | None = None
    company_name: str | None = None
    confidence: EmailConfidence = EmailConfidence.MEDIUM
    is_inferred: bool = False
    source_excerpt: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedRecruiter:
    name: str
    normalized_name: str
    source: SourceType
    # None for pattern-inferred records: there is no source page, by definition.
    source_url: str | None
    title: str | None = None
    email: str | None = None
    profile_url: str | None = None
    company_name: str | None = None
    confidence: EmailConfidence = EmailConfidence.MEDIUM
    is_inferred: bool = False
    source_excerpt: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectorError:
    stage: str
    message: str
    url: str | None = None
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "message": self.message,
            "url": self.url,
            "fatal": self.fatal,
        }


@dataclass
class CollectionOutcome:
    """What a single collector run produced. Never silently empty."""

    collector: str
    source: SourceType
    records: list[Any] = field(default_factory=list)
    rejected: list[tuple[Any, str]] = field(default_factory=list)
    duplicates_dropped: int = 0
    errors: list[CollectorError] = field(default_factory=list)
    pages_fetched: int = 0
    bytes_downloaded: int = 0
    rate_limit_waits: int = 0
    rate_limit_seconds: float = 0.0
    blocked_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def found(self) -> int:
        return len(self.records) + len(self.rejected) + self.duplicates_dropped

    @property
    def failed(self) -> bool:
        return any(e.fatal for e in self.errors)
