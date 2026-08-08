"""Shared normalisation/validation for every job collector."""

from __future__ import annotations

import re

from app.collectors.base import BaseCollector
from app.collectors.types import NormalizedJob, RawJob
from app.security.url_guard import normalize_url
from app.utils.text import (
    content_fingerprint,
    normalize_location,
    normalize_title,
)

MIN_TITLE_LENGTH = 3
MAX_TITLE_LENGTH = 400

# Titles that are not really openings.
NON_JOB_TITLE_PATTERNS = (
    r"^\s*general application\s*$",
    r"^\s*talent (?:pool|community|network)\s*$",
    r"^\s*future opportunities\s*$",
    r"^\s*speculative\s*",
    r"^\s*don'?t see (?:a|your) (?:role|fit)",
)


class JobCollectorMixin(BaseCollector[RawJob, NormalizedJob]):
    """Normalisation shared by career-page and ATS collectors."""

    record_type = "job"

    def __init__(self, *, company_name: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.company_name = company_name

    def normalize(self, raw: RawJob) -> NormalizedJob | None:
        title = " ".join((raw.title or "").split()).strip()
        if not title:
            return None

        canonical = normalize_url(raw.url) if raw.url else ""
        if not canonical:
            return None

        normalized_title = normalize_title(title)
        normalized_location = normalize_location(raw.location)

        return NormalizedJob(
            title=title[:MAX_TITLE_LENGTH],
            normalized_title=normalized_title[:MAX_TITLE_LENGTH],
            url=raw.url,
            canonical_url=canonical,
            # Content hash intentionally excludes the URL: the same posting
            # surfaced by two different sources must collapse to one job.
            content_hash=content_fingerprint(
                self.company_name, normalized_title, normalized_location
            ),
            source=raw.source,
            source_url=raw.source_url,
            external_id=raw.external_id,
            description=raw.description,
            location=raw.location,
            normalized_location=normalized_location,
            employment_type=raw.employment_type,
            posted_at=raw.posted_at,
            payload=raw.payload,
        )

    def validate(self, record: NormalizedJob) -> tuple[bool, str | None]:
        if len(record.title) < MIN_TITLE_LENGTH:
            return False, "title too short"
        if not record.normalized_title:
            return False, "title normalised to nothing"
        for pattern in NON_JOB_TITLE_PATTERNS:
            if re.search(pattern, record.title, flags=re.IGNORECASE):
                return False, "not a specific opening (general/speculative posting)"
        if not record.canonical_url.startswith(("http://", "https://")):
            return False, "job URL is not http(s)"
        return True, None

    def dedupe_key(self, record: NormalizedJob) -> str:
        return record.content_hash
