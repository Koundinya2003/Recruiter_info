"""SSRF and malicious-URL handling.

These are the tests that must never be relaxed: they encode the promise that a
user-supplied URL cannot be turned into a request against internal
infrastructure or cloud metadata.
"""

from __future__ import annotations

import pytest

from app.security.url_guard import (
    MAX_URL_LENGTH,
    UnsafeURLError,
    extract_domain,
    is_safe_url,
    validate_url,
)

SSRF_PAYLOADS = [
    # Loopback and internal addressing
    "http://127.0.0.1/admin",
    "http://127.000.000.1/admin",
    "http://localhost:8000/api",
    "http://[::1]/",
    "http://0.0.0.0:8000/",
    "http://10.0.0.5/internal",
    "http://172.16.4.4/internal",
    "http://192.168.1.1/router",
    "http://100.64.0.1/cgnat",
    # Cloud metadata services
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://100.100.100.200/latest/meta-data",
    # Internal DNS names
    "http://db.internal/",
    "http://service.cluster.local/",
    "http://printer.local/",
    # Non-HTTP schemes
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/_INFO",
    "ftp://internal.example/",
    "dict://127.0.0.1:11211/",
    "jar:http://127.0.0.1!/",
    "data:text/html,<script>alert(1)</script>",
    "javascript:alert(1)",
    # Credential smuggling
    "http://user:password@example.com/",
    "https://attacker%40evil.com@127.0.0.1/",
]


@pytest.mark.parametrize("payload", SSRF_PAYLOADS)
def test_ssrf_payloads_are_rejected(payload: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(payload, allow_private=False)
    assert is_safe_url(payload, allow_private=False) is False


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "   ",
        "not a url",
        "http://",
        "https://",
        "http://exa mple.com/",
        "http://example.com/\nHost: evil.com",
        "http://example.com/\r\nX-Injected: 1",
        "http://example.com:99999/",
        "https://" + "a" * (MAX_URL_LENGTH + 10),
    ],
)
def test_malformed_urls_are_rejected(payload: str) -> None:
    with pytest.raises(UnsafeURLError):
        validate_url(payload, allow_private=False)


def test_non_standard_port_is_rejected() -> None:
    with pytest.raises(UnsafeURLError, match="Port"):
        validate_url("http://example.com:6379/", allow_private=False)


def test_public_https_url_is_accepted() -> None:
    validated = validate_url("https://example.com/careers", allow_private=False)
    assert validated.host == "example.com"
    assert validated.port == 443
    assert validated.resolved_ips


def test_domain_helper_strips_www_and_scheme() -> None:
    assert extract_domain("https://www.Example.com/careers") == "example.com"
    assert extract_domain("example.com") == "example.com"
    assert extract_domain("HTTP://SUB.EXAMPLE.CO.UK/x") == "sub.example.co.uk"


def test_allow_private_is_opt_in_only() -> None:
    """The escape hatch exists for the test mock server and nothing else."""
    with pytest.raises(UnsafeURLError):
        validate_url("http://127.0.0.1:8501/", allow_private=False)
    assert validate_url("http://127.0.0.1:8501/", allow_private=True).host == "127.0.0.1"


def test_unresolvable_hostname_is_rejected() -> None:
    with pytest.raises(UnsafeURLError, match="could not be resolved"):
        validate_url("https://this-host-does-not-exist-xyz987654.example/", allow_private=False)
