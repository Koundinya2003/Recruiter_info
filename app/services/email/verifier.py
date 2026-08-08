"""Email verification behind a swappable provider interface.

    verify_email(email) -> {"status": "valid", "confidence": 0.96, "provider": "..."}

Three providers ship:

* ``dns``  (default) — syntax + domain MX checks performed locally. No third
  party, no cost, no data leaves the machine. It can prove an address is
  *undeliverable*; it cannot prove a specific mailbox exists, and it says so by
  returning ``risky``/``unknown`` rather than overclaiming ``valid``.
* ``null`` — never checks anything; everything stays ``not_checked``. Useful
  when you want zero network activity.
* ``http`` — a third-party verification API, configured by URL and key.

Choosing a provider is one environment variable. Adding one is one subclass.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from email_validator import EmailNotValidError, validate_email

from app.config import settings
from app.logging_config import get_logger
from app.models.enums import VerificationStatus
from app.utils.html import is_role_address

log = get_logger(__name__)

# Providers that accept mail for any local part; a syntax/MX check tells you
# almost nothing about whether the individual mailbox exists.
CATCH_ALL_HINTS = re.compile(r"catch.?all", re.IGNORECASE)


@dataclass
class VerificationResult:
    """The uniform result every provider returns."""

    email: str
    status: VerificationStatus
    confidence: float
    provider: str
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "status": self.status.value,
            "confidence": round(self.confidence, 4),
            "provider": self.provider,
            "reason": self.reason,
        }


class EmailVerifier(ABC):
    """Provider interface. Implement :meth:`verify` and set :attr:`name`."""

    name: str = "base"

    @abstractmethod
    def verify(self, email: str) -> VerificationResult:
        """Check one address. Must never raise: return ``unknown`` instead."""

    def available(self) -> bool:
        return True


class NullVerifier(EmailVerifier):
    """Performs no checks at all."""

    name = "null"

    def verify(self, email: str) -> VerificationResult:
        return VerificationResult(
            email=email,
            status=VerificationStatus.NOT_CHECKED,
            confidence=0.0,
            provider=self.name,
            reason="Verification is disabled (EMAIL_VERIFICATION_PROVIDER=null)",
        )


class DNSVerifier(EmailVerifier):
    """Local syntax + MX verification. The default; needs no API key."""

    name = "dns"

    def verify(self, email: str) -> VerificationResult:
        address = (email or "").strip().lower()
        if not address:
            return VerificationResult(
                email=email,
                status=VerificationStatus.INVALID,
                confidence=0.0,
                provider=self.name,
                reason="Empty address",
            )

        try:
            checked = validate_email(address, check_deliverability=True)
        except EmailNotValidError as exc:
            message = str(exc)
            # Distinguish "this can never work" from "we could not check".
            if "resolve" in message.lower() or "timeout" in message.lower():
                return VerificationResult(
                    email=address,
                    status=VerificationStatus.UNKNOWN,
                    confidence=0.0,
                    provider=self.name,
                    reason=f"Could not resolve the domain: {message}",
                )
            return VerificationResult(
                email=address,
                status=VerificationStatus.INVALID,
                confidence=0.0,
                provider=self.name,
                reason=message,
            )
        except Exception as exc:  # noqa: BLE001 - never propagate to the caller
            log.warning("verify.dns_error", email=address, error=str(exc))
            return VerificationResult(
                email=address,
                status=VerificationStatus.UNKNOWN,
                confidence=0.0,
                provider=self.name,
                reason=f"Verification error: {exc}",
            )

        normalized = checked.normalized.lower()
        # A shared role address is deliverable but is not a person; flag it as
        # risky so the UI never implies we confirmed an individual's mailbox.
        if is_role_address(normalized):
            return VerificationResult(
                email=normalized,
                status=VerificationStatus.RISKY,
                confidence=0.55,
                provider=self.name,
                reason=(
                    "Domain accepts mail, but this is a shared role address rather than "
                    "an individual mailbox"
                ),
                raw={"domain": checked.domain, "role_address": True},
            )

        return VerificationResult(
            email=normalized,
            status=VerificationStatus.VALID,
            confidence=0.8,
            provider=self.name,
            reason=(
                "Syntax is valid and the domain publishes mail servers. Local checks "
                "cannot confirm the individual mailbox exists."
            ),
            raw={"domain": checked.domain, "mx_checked": True},
        )


class HTTPAPIVerifier(EmailVerifier):
    """A generic third-party verification API.

    Configure with ``EMAIL_VERIFICATION_API_URL`` containing ``{email}`` and
    optionally ``{api_key}`` placeholders. The response is mapped leniently, so
    most vendors work without a bespoke adapter.
    """

    name = "http"

    STATUS_MAP = {
        "valid": VerificationStatus.VALID,
        "deliverable": VerificationStatus.VALID,
        "ok": VerificationStatus.VALID,
        "invalid": VerificationStatus.INVALID,
        "undeliverable": VerificationStatus.INVALID,
        "risky": VerificationStatus.RISKY,
        "accept_all": VerificationStatus.RISKY,
        "accept-all": VerificationStatus.RISKY,
        "catch_all": VerificationStatus.RISKY,
        "unknown": VerificationStatus.UNKNOWN,
        "error": VerificationStatus.UNKNOWN,
    }

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_url = api_url if api_url is not None else settings.email_verification_api_url
        self.api_key = api_key if api_key is not None else settings.email_verification_api_key
        self.timeout = timeout if timeout is not None else settings.email_verification_timeout

    def available(self) -> bool:
        return bool(self.api_url and self.api_key)

    def verify(self, email: str) -> VerificationResult:
        if not self.available():
            return VerificationResult(
                email=email,
                status=VerificationStatus.NOT_CHECKED,
                confidence=0.0,
                provider=self.name,
                reason="No verification API URL/key configured",
            )

        url = self.api_url.replace("{email}", quote(email, safe="@"))
        url = url.replace("{api_key}", quote(self.api_key, safe=""))
        try:
            response = httpx.get(
                url,
                timeout=self.timeout,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": settings.crawler_user_agent,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("verify.http_error", error=str(exc))
            return VerificationResult(
                email=email,
                status=VerificationStatus.UNKNOWN,
                confidence=0.0,
                provider=self.name,
                reason=f"Verification provider unavailable: {type(exc).__name__}",
            )

        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        raw_status = str(
            data.get("status") or data.get("result") or data.get("state") or "unknown"
        ).lower()
        status = self.STATUS_MAP.get(raw_status, VerificationStatus.UNKNOWN)

        raw_confidence = data.get("confidence", data.get("score"))
        try:
            confidence = float(raw_confidence)  # type: ignore[arg-type]
            if confidence > 1:
                confidence /= 100.0
        except (TypeError, ValueError):
            confidence = {
                VerificationStatus.VALID: 0.9,
                VerificationStatus.RISKY: 0.5,
                VerificationStatus.INVALID: 0.0,
                VerificationStatus.UNKNOWN: 0.0,
                VerificationStatus.NOT_CHECKED: 0.0,
            }[status]

        return VerificationResult(
            email=email,
            status=status,
            confidence=max(0.0, min(1.0, confidence)),
            provider=self.name,
            reason=str(data.get("reason") or raw_status),
            raw=data if isinstance(data, dict) else {},
        )


_PROVIDERS: dict[str, type[EmailVerifier]] = {
    "null": NullVerifier,
    "dns": DNSVerifier,
    "http": HTTPAPIVerifier,
}


def get_verifier(name: str | None = None) -> EmailVerifier:
    """Return the configured verifier, falling back safely."""
    key = (name or settings.email_verification_provider or "dns").lower()
    provider_cls = _PROVIDERS.get(key)
    if provider_cls is None:
        log.warning("verify.unknown_provider", requested=key)
        return DNSVerifier()
    provider = provider_cls()
    if not provider.available():
        log.info("verify.provider_unavailable", provider=key, fallback="dns")
        return DNSVerifier()
    return provider


def register_provider(name: str, provider_cls: type[EmailVerifier]) -> None:
    """Register a custom provider (used by tests and future integrations)."""
    _PROVIDERS[name.lower()] = provider_cls
