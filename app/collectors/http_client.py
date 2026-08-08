"""The single guarded HTTP client used by every collector.

Nothing in this application fetches a remote URL except through
:class:`SafeHTTPClient`. It composes, in order:

1. SSRF validation of the URL (and of every redirect hop separately).
2. robots.txt compliance for the target path.
3. Per-domain rate limiting (politeness delay, honouring ``Crawl-delay``).
4. A hard request timeout.
5. Retry with exponential backoff on transient failures only — never on 403,
   401 or 429-with-Retry-After, because retrying those is exactly the
   "evade the block" behaviour this tool refuses to implement.
6. A response body size cap, enforced while streaming.

A 401/403/429 is reported as a terminal, logged outcome. That is the correct
response to a site telling us to stop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.logging_config import get_logger
from app.security.rate_limit import get_domain_rate_limiter
from app.security.robots import get_robots_checker
from app.security.url_guard import UnsafeURLError, ValidatedURL, validate_url

log = get_logger(__name__)

MAX_REDIRECTS = 4

# Statuses that mean "the site is refusing us". We stop, we do not retry, and
# we never attempt to disguise the request to get around them.
BLOCKING_STATUSES = frozenset({401, 402, 403, 407, 429, 451})
RETRYABLE_STATUSES = frozenset({408, 500, 502, 503, 504})


class FetchBlocked(Exception):
    """Raised when a fetch is refused by policy (robots, SSRF, site block)."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    text: str
    content_type: str
    elapsed_ms: int
    from_cache: bool = False
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        import json

        return json.loads(self.text)


@dataclass
class FetchStats:
    requests: int = 0
    blocked_by_robots: int = 0
    blocked_by_site: int = 0
    ssrf_rejected: int = 0
    errors: int = 0
    bytes_downloaded: int = 0
    rate_limit_waits: int = 0
    rate_limit_seconds: float = 0.0


class SafeHTTPClient:
    """Policy-enforcing HTTP client. Use as a context manager."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        max_pages: int | None = None,
        max_bytes: int | None = None,
        respect_robots: bool | None = None,
        allow_private: bool | None = None,
    ) -> None:
        self.user_agent = user_agent or settings.crawler_user_agent
        self.timeout = timeout if timeout is not None else settings.crawler_request_timeout
        self.max_retries = (
            max_retries if max_retries is not None else settings.crawler_max_retries
        )
        self.max_pages = (
            max_pages if max_pages is not None else settings.crawler_max_pages_per_run
        )
        self.max_bytes = max_bytes if max_bytes is not None else settings.crawler_max_response_bytes
        self.respect_robots = (
            respect_robots if respect_robots is not None else settings.crawler_respect_robots
        )
        self.allow_private = (
            allow_private
            if allow_private is not None
            else settings.crawler_allow_private_networks
        )
        self.stats = FetchStats()
        self._limiter = get_domain_rate_limiter()
        self._robots = get_robots_checker()
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=False,  # we re-validate each hop ourselves
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en",
            },
        )

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self) -> SafeHTTPClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    @property
    def budget_exhausted(self) -> bool:
        return self.stats.requests >= self.max_pages

    # -- internals -----------------------------------------------------------
    def _check_policy(self, url: str) -> ValidatedURL:
        try:
            validated = validate_url(url, allow_private=self.allow_private)
        except UnsafeURLError as exc:
            self.stats.ssrf_rejected += 1
            log.warning("fetch.rejected_unsafe_url", url=url, error=str(exc))
            raise FetchBlocked(str(exc), reason="unsafe_url") from exc

        if self.respect_robots:
            decision = self._robots.check(url)
            if not decision.allowed:
                self.stats.blocked_by_robots += 1
                log.info("fetch.blocked_by_robots", url=url, reason=decision.reason)
                raise FetchBlocked(
                    f"robots.txt does not permit fetching {url}: {decision.reason}",
                    reason="robots_disallowed",
                )
            if decision.crawl_delay and decision.crawl_delay > self._limiter.delay_seconds:
                # Honour a site asking us to slow down.
                self._limiter.delay_seconds = decision.crawl_delay
        return validated

    def _read_capped(self, response: httpx.Response) -> str:
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            raise FetchBlocked(
                f"Response advertises {declared} bytes, over the {self.max_bytes} cap",
                reason="response_too_large",
            )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self.max_bytes:
                raise FetchBlocked(
                    f"Response exceeded the {self.max_bytes} byte cap",
                    reason="response_too_large",
                )
            chunks.append(chunk)
        self.stats.bytes_downloaded += total
        body = b"".join(chunks)
        encoding = response.encoding or "utf-8"
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            return body.decode("utf-8", errors="replace")

    # -- public API ----------------------------------------------------------
    def fetch(self, url: str, *, headers: dict[str, str] | None = None) -> FetchResult:
        """Fetch ``url`` under full policy enforcement.

        :raises FetchBlocked: policy refusal (unsafe URL, robots, site block,
            oversized response, budget exhausted).
        :raises httpx.HTTPError: transient network failure that survived retries.
        """
        if self.budget_exhausted:
            raise FetchBlocked(
                f"Crawl budget of {self.max_pages} pages exhausted for this run",
                reason="budget_exhausted",
            )

        started = time.monotonic()
        current = url
        last_exc: Exception | None = None

        for hop in range(MAX_REDIRECTS + 1):
            validated = self._check_policy(current)

            waited = self._limiter.acquire(validated.domain)
            if waited:
                self.stats.rate_limit_waits += 1
                self.stats.rate_limit_seconds += waited

            response = None
            for attempt in range(self.max_retries + 1):
                try:
                    self.stats.requests += 1
                    request = self._client.build_request(
                        "GET", current, headers=headers or {}
                    )
                    response = self._client.send(request, stream=True)
                except httpx.HTTPError as exc:
                    last_exc = exc
                    self.stats.errors += 1
                    if attempt >= self.max_retries:
                        log.warning(
                            "fetch.network_error", url=current, error=str(exc), attempts=attempt + 1
                        )
                        raise
                    backoff = 2.0**attempt
                    log.info("fetch.retry", url=current, attempt=attempt + 1, backoff=backoff)
                    time.sleep(backoff)
                    continue

                if response.status_code in BLOCKING_STATUSES:
                    response.close()
                    self.stats.blocked_by_site += 1
                    log.info(
                        "fetch.blocked_by_site", url=current, status=response.status_code
                    )
                    raise FetchBlocked(
                        f"{current} returned HTTP {response.status_code}; the site is declining "
                        "automated access and we will not work around that",
                        reason=f"http_{response.status_code}",
                    )

                if response.status_code in RETRYABLE_STATUSES and attempt < self.max_retries:
                    response.close()
                    backoff = 2.0**attempt
                    log.info(
                        "fetch.retry_status",
                        url=current,
                        status=response.status_code,
                        backoff=backoff,
                    )
                    time.sleep(backoff)
                    continue

                break

            if response is None:  # pragma: no cover - defensive
                raise last_exc or httpx.HTTPError(f"No response for {current}")

            if response.is_redirect and hop < MAX_REDIRECTS:
                location = response.headers.get("location", "")
                response.close()
                if not location:
                    raise FetchBlocked(
                        f"{current} redirected without a Location header", reason="bad_redirect"
                    )
                current = str(httpx.URL(current).join(location))
                log.debug("fetch.redirect", to=current)
                continue

            try:
                text = self._read_capped(response)
            finally:
                response.close()

            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.debug(
                "fetch.ok", url=current, status=response.status_code, elapsed_ms=elapsed_ms
            )
            return FetchResult(
                url=url,
                final_url=current,
                status_code=response.status_code,
                text=text,
                content_type=response.headers.get("content-type", ""),
                elapsed_ms=elapsed_ms,
                headers=dict(response.headers),
            )

        raise FetchBlocked(f"Too many redirects starting at {url}", reason="too_many_redirects")
