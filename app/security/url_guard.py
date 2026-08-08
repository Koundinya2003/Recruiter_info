"""URL validation and SSRF protection.

Any URL that reaches the network originates from user input somewhere (a
career page URL typed into the UI, a link discovered on a page, a redirect
target). All of them pass through :func:`validate_url` first.

The guard enforces, in order:

1. Scheme is ``http`` or ``https`` — no ``file://``, ``gopher://``, ``ftp://``.
2. No embedded credentials (``http://user:pass@host``).
3. Hostname is present and is not a blocked name (``localhost``, ``*.local``,
   cloud metadata hostnames).
4. Port is on the allowlist.
5. Every IP address the hostname resolves to is globally routable — this is the
   check that actually stops SSRF, because ``evil.com`` can resolve to
   ``169.254.169.254`` just as easily as a literal can.

Redirects are *not* trusted: the HTTP client re-validates every hop.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from app.config import settings

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443, 8000, 8080, 8443})

# Hostnames that must never be fetched, regardless of what they resolve to.
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
        "metadata.goog",
    }
)

BLOCKED_HOST_SUFFIXES = (".local", ".localdomain", ".internal", ".cluster.local")

# Link-local addresses used by cloud instance metadata services.
METADATA_ADDRESSES = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})

# Ranges that are not routable on the public internet but that `ipaddress` does
# not consistently report as private across Python versions. Listing them
# explicitly means the guard does not depend on stdlib version behaviour.
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "100.64.0.0/10",     # RFC 6598 carrier-grade NAT
        "198.18.0.0/15",     # RFC 2544 benchmarking
        "192.0.0.0/24",      # IETF protocol assignments
        "192.0.2.0/24",      # TEST-NET-1
        "198.51.100.0/24",   # TEST-NET-2
        "203.0.113.0/24",    # TEST-NET-3
        "240.0.0.0/4",       # reserved for future use
        "64:ff9b::/96",      # IPv4/IPv6 translation
        "100::/64",          # IPv6 discard-only
        "2001:db8::/32",     # IPv6 documentation
    )
)

MAX_URL_LENGTH = 2048


class UnsafeURLError(ValueError):
    """Raised when a URL is rejected by the guard."""


@dataclass(frozen=True)
class ValidatedURL:
    """A URL that passed every guard check."""

    url: str
    scheme: str
    host: str
    port: int
    resolved_ips: tuple[str, ...]

    @property
    def domain(self) -> str:
        """Host without a leading ``www.``, lowercased — the rate-limit key."""
        return self.host[4:] if self.host.startswith("www.") else self.host


def _is_public_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if raw in METADATA_ADDRESSES:
        return False
    if any(ip in network for network in BLOCKED_NETWORKS if ip.version == network.version):
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Hostname could not be resolved: {host}") from exc
    ips = {str(info[4][0]) for info in infos}
    if not ips:
        raise UnsafeURLError(f"Hostname resolved to no addresses: {host}")
    return sorted(ips)


def normalize_url(raw: str) -> str:
    """Canonicalise a URL for storage and duplicate detection.

    Lowercases scheme/host, drops the fragment, drops a default port and
    removes a trailing slash on the path. Query strings are preserved because
    many job boards encode the posting id there.
    """
    parsed = urlparse(raw.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return raw.strip()
    port = parsed.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def validate_url(raw: str, *, allow_private: bool | None = None) -> ValidatedURL:
    """Validate ``raw`` and return its resolved, safe form.

    :param allow_private: overrides the ``CRAWLER_ALLOW_PRIVATE_NETWORKS``
        setting. Used by the test-suite to point collectors at a local mock
        server; must never be enabled for user-supplied URLs in production.
    :raises UnsafeURLError: if any check fails.
    """
    if allow_private is None:
        allow_private = settings.crawler_allow_private_networks

    if not raw or not raw.strip():
        raise UnsafeURLError("URL is empty")
    raw = raw.strip()
    if len(raw) > MAX_URL_LENGTH:
        raise UnsafeURLError(f"URL exceeds {MAX_URL_LENGTH} characters")
    if any(ch in raw for ch in ("\n", "\r", "\t", " ")):
        raise UnsafeURLError("URL contains whitespace or control characters")

    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise UnsafeURLError(f"URL could not be parsed: {exc}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Scheme {scheme or '(none)'!r} is not allowed; use http or https")

    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")

    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no hostname")

    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:  # malformed port
        raise UnsafeURLError("URL has an invalid port") from exc

    if not allow_private:
        if host in BLOCKED_HOSTNAMES:
            raise UnsafeURLError(f"Hostname {host!r} is blocked")
        if any(host.endswith(suffix) for suffix in BLOCKED_HOST_SUFFIXES):
            raise UnsafeURLError(f"Hostname {host!r} is on a blocked internal domain")
        if port not in ALLOWED_PORTS:
            raise UnsafeURLError(f"Port {port} is not allowed")

    resolved = _resolve(host)
    if not allow_private:
        for ip in resolved:
            if not _is_public_ip(ip):
                raise UnsafeURLError(
                    f"Hostname {host!r} resolves to non-public address {ip}; refusing to fetch"
                )

    return ValidatedURL(
        url=raw,
        scheme=scheme,
        host=host,
        port=port,
        resolved_ips=tuple(resolved),
    )


def is_safe_url(raw: str, *, allow_private: bool | None = None) -> bool:
    """Boolean form of :func:`validate_url`, for UI-side checks."""
    try:
        validate_url(raw, allow_private=allow_private)
    except UnsafeURLError:
        return False
    return True


def extract_domain(raw: str) -> str:
    """Best-effort registrable-ish domain from a URL or bare domain string."""
    candidate = raw.strip().lower()
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    host = (urlparse(candidate).hostname or "").rstrip(".")
    return host[4:] if host.startswith("www.") else host
