"""The contract every job source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.collectors.http_client import FetchBlocked, SafeHTTPClient
from app.logging_config import get_logger
from app.models.enums import SourceType
from app.providers.types import ProviderResult, RawJob
from app.search.query import JobQuery

log = get_logger(__name__)


class JobProvider(ABC):
    """A searchable, public source of real job postings.

    Implementations must never synthesise a posting. If a source returns
    nothing, the correct result is an empty list plus a note saying so.
    """

    #: Stable identifier used in configuration and in run records.
    name: str = "provider"
    #: Human-readable name shown in the UI.
    label: str = "Provider"
    source: SourceType
    #: Short sentence explaining what this source covers.
    coverage: str = ""
    #: Names of settings that must be non-empty for this provider to run.
    required_settings: tuple[str, ...] = ()
    #: Where to get credentials, shown when the provider is unconfigured.
    signup_url: str | None = None
    #: True when the source only carries remote roles.
    remote_only_source: bool = False

    def __init__(self, client: SafeHTTPClient) -> None:
        self.client = client
        self.result = ProviderResult(provider=self.name, source=self.source)

    # -- configuration -------------------------------------------------------
    @classmethod
    def missing_settings(cls) -> list[str]:
        from app.config import settings

        return [
            name
            for name in cls.required_settings
            if not str(getattr(settings, name, "") or "").strip()
        ]

    @classmethod
    def is_configured(cls) -> bool:
        return not cls.missing_settings()

    def supports(self, query: JobQuery) -> tuple[bool, str | None]:
        """Whether this provider can usefully answer ``query``.

        Returning ``(False, reason)`` is normal and gets recorded on the run,
        so the user can see why a source was not consulted.
        """
        if self.remote_only_source and query.locations and not query.remote_only:
            return False, "only carries remote roles; the search names specific locations"
        return True, None

    # -- searching -----------------------------------------------------------
    @abstractmethod
    def search(self, query: JobQuery) -> list[RawJob]:
        """Fetch postings matching ``query``. May raise; the runner catches."""

    def run(self, query: JobQuery) -> ProviderResult:
        """Search, converting every failure into a recorded outcome."""
        self.result = ProviderResult(provider=self.name, source=self.source)

        if not self.is_configured():
            missing = ", ".join(self.missing_settings())
            self.result.skipped_reason = f"not configured (set {missing})"
            return self.result

        ok, reason = self.supports(query)
        if not ok:
            self.result.skipped_reason = reason
            return self.result

        before = self.client.stats.requests
        try:
            jobs = self.search(query)
        except FetchBlocked as exc:
            self.result.errors.append(f"{self.label} declined the request: {exc}")
            log.info("provider.blocked", provider=self.name, reason=exc.reason)
        except Exception as exc:  # noqa: BLE001 - one bad source must not end the search
            self.result.errors.append(f"{self.label} failed: {type(exc).__name__}: {exc}")
            log.warning("provider.failed", provider=self.name, error=str(exc)[:300])
        else:
            self.result.jobs = [j for j in jobs if j.title and j.url and j.company_name]
            dropped = len(jobs) - len(self.result.jobs)
            if dropped:
                self.result.notes.append(
                    f"{dropped} record(s) from {self.label} lacked a title, company or URL"
                )
        self.result.pages_fetched = self.client.stats.requests - before
        return self.result

    # -- helpers -------------------------------------------------------------
    def fetch_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        self.result.query_urls.append(url)
        response = self.client.fetch(url, headers=headers)
        return response.json()

    @staticmethod
    def looks_remote(*values: str | None) -> bool:
        blob = " ".join(v for v in values if v).lower()
        return any(term in blob for term in ("remote", "work from home", "anywhere"))
