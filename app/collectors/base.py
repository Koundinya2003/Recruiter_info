"""The collector abstraction.

    BaseCollector
        ├── CareerPageCollector
        ├── PublicJobCollector  (Greenhouse / Lever / Ashby public APIs)
        └── PublicRecruiterSourceCollector

Every collector implements ``collect`` / ``normalize`` / ``deduplicate`` /
``validate``, and :meth:`BaseCollector.run` drives them in that order while
capturing errors, counts and rate-limit statistics into a
:class:`CollectionOutcome`.

Failures are never swallowed: a collector that fetched nothing because it was
blocked reports ``blocked_reason``, and one that threw reports a fatal error.
The caller persists both to `crawl_runs`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

import httpx

from app.collectors.http_client import FetchBlocked, SafeHTTPClient
from app.collectors.types import CollectionOutcome, CollectorError
from app.logging_config import get_logger
from app.models.enums import SourceType

log = get_logger(__name__)

TRaw = TypeVar("TRaw")
TNormalized = TypeVar("TNormalized")


class BaseCollector(ABC, Generic[TRaw, TNormalized]):
    """Template for every data collector in the system."""

    name: str = "base"
    source_type: SourceType = SourceType.MANUAL_ENTRY
    record_type: str = "job"

    def __init__(self, *, client: SafeHTTPClient | None = None, **options: Any) -> None:
        self._client = client
        self._owns_client = client is None
        self.options = options
        self.outcome = CollectionOutcome(collector=self.name, source=self.source_type)

    # -- client --------------------------------------------------------------
    @property
    def client(self) -> SafeHTTPClient:
        if self._client is None:
            self._client = SafeHTTPClient()
            self._owns_client = True
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    # -- required steps ------------------------------------------------------
    @abstractmethod
    def collect(self) -> list[TRaw]:
        """Fetch raw records from the source. May raise; ``run`` handles it."""

    @abstractmethod
    def normalize(self, raw: TRaw) -> TNormalized | None:
        """Canonicalise one raw record, or return None to drop it."""

    @abstractmethod
    def validate(self, record: TNormalized) -> tuple[bool, str | None]:
        """Return ``(is_valid, reason_if_not)`` for one normalised record."""

    @abstractmethod
    def dedupe_key(self, record: TNormalized) -> str:
        """Key used for within-run duplicate detection."""

    def deduplicate(self, records: list[TNormalized]) -> list[TNormalized]:
        """Drop within-run duplicates, keeping the first occurrence."""
        seen: set[str] = set()
        unique: list[TNormalized] = []
        for record in records:
            key = self.dedupe_key(record)
            if key in seen:
                self.outcome.duplicates_dropped += 1
                continue
            seen.add(key)
            unique.append(record)
        return unique

    # -- orchestration -------------------------------------------------------
    def run(self) -> CollectionOutcome:
        """Execute the full pipeline, capturing every failure mode."""
        log.info("collector.start", collector=self.name, source=self.source_type.value)
        raw_records: list[TRaw] = []
        try:
            raw_records = self.collect()
        except FetchBlocked as exc:
            self.outcome.blocked_reason = exc.reason
            self.outcome.errors.append(
                CollectorError(stage="collect", message=str(exc), fatal=False)
            )
            log.info("collector.blocked", collector=self.name, reason=exc.reason)
        except httpx.HTTPError as exc:
            self.outcome.errors.append(
                CollectorError(stage="collect", message=f"network error: {exc}", fatal=True)
            )
            log.warning("collector.network_error", collector=self.name, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - collectors must never crash the caller
            self.outcome.errors.append(
                CollectorError(stage="collect", message=f"{type(exc).__name__}: {exc}", fatal=True)
            )
            log.exception("collector.failed", collector=self.name)

        normalized: list[TNormalized] = []
        for raw in raw_records:
            try:
                record = self.normalize(raw)
            except Exception as exc:  # noqa: BLE001
                self.outcome.errors.append(
                    CollectorError(
                        stage="normalize", message=f"{type(exc).__name__}: {exc}", fatal=False
                    )
                )
                continue
            if record is None:
                self.outcome.rejected.append((raw, "could not be normalised"))
                continue
            normalized.append(record)

        unique = self.deduplicate(normalized)

        for record in unique:
            try:
                ok, reason = self.validate(record)
            except Exception as exc:  # noqa: BLE001
                ok, reason = False, f"validation error: {exc}"
            if ok:
                self.outcome.records.append(record)
            else:
                self.outcome.rejected.append((record, reason or "failed validation"))

        if self._client is not None:
            stats = self._client.stats
            self.outcome.pages_fetched = stats.requests
            self.outcome.bytes_downloaded = stats.bytes_downloaded
            self.outcome.rate_limit_waits = stats.rate_limit_waits
            self.outcome.rate_limit_seconds = stats.rate_limit_seconds

        log.info(
            "collector.done",
            collector=self.name,
            found=self.outcome.found,
            accepted=len(self.outcome.records),
            rejected=len(self.outcome.rejected),
            duplicates=self.outcome.duplicates_dropped,
            errors=len(self.outcome.errors),
            blocked=self.outcome.blocked_reason,
        )
        return self.outcome

    def __enter__(self) -> BaseCollector[TRaw, TNormalized]:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
