"""Which job sources exist, and which of them can run right now."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.collectors.http_client import SafeHTTPClient
from app.config import settings
from app.providers.aggregators import (
    AdzunaProvider,
    ArbeitnowProvider,
    JobicyProvider,
    RemotiveProvider,
    TheMuseProvider,
    USAJobsProvider,
)
from app.providers.base import JobProvider
from app.providers.company_boards import CompanyBoardProvider

PROVIDER_CLASSES: tuple[type[JobProvider], ...] = (
    CompanyBoardProvider,
    AdzunaProvider,
    TheMuseProvider,
    RemotiveProvider,
    ArbeitnowProvider,
    JobicyProvider,
    USAJobsProvider,
)

#: Settings toggle that switches a provider off entirely, where one exists.
_ENABLE_FLAGS: dict[str, str] = {
    "remotive": "enable_remotive",
    "arbeitnow": "enable_arbeitnow",
    "jobicy": "enable_jobicy",
    "themuse": "enable_the_muse",
    "company_boards": "enable_company_boards",
}


def is_enabled(provider_cls: type[JobProvider]) -> bool:
    flag = _ENABLE_FLAGS.get(provider_cls.name)
    return True if flag is None else bool(getattr(settings, flag, True))


@dataclass
class ProviderStatus:
    name: str
    label: str
    coverage: str
    enabled: bool
    configured: bool
    missing_settings: list[str]
    signup_url: str | None
    remote_only: bool

    @property
    def usable(self) -> bool:
        return self.enabled and self.configured

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "coverage": self.coverage,
            "enabled": self.enabled,
            "configured": self.configured,
            "usable": self.usable,
            "missing_settings": self.missing_settings,
            "signup_url": self.signup_url,
            "remote_only": self.remote_only,
        }


def provider_statuses() -> list[ProviderStatus]:
    """What every source's configuration looks like, for the Sources page."""
    return [
        ProviderStatus(
            name=cls.name,
            label=cls.label,
            coverage=cls.coverage,
            enabled=is_enabled(cls),
            configured=cls.is_configured(),
            missing_settings=cls.missing_settings(),
            signup_url=cls.signup_url,
            remote_only=cls.remote_only_source,
        )
        for cls in PROVIDER_CLASSES
    ]


def build_providers(
    client: SafeHTTPClient, *, only: list[str] | None = None, **kwargs: Any
) -> list[JobProvider]:
    """Instantiate the providers that should take part in a search."""
    wanted = {n.strip().lower() for n in only} if only else None
    providers: list[JobProvider] = []
    for cls in PROVIDER_CLASSES:
        if wanted is not None and cls.name not in wanted:
            continue
        if not is_enabled(cls):
            continue
        if cls is CompanyBoardProvider:
            providers.append(
                CompanyBoardProvider(client, known_boards=kwargs.get("known_boards"))
            )
        else:
            providers.append(cls(client))
    return providers


def any_provider_usable() -> bool:
    return any(status.usable for status in provider_statuses())
