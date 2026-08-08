"""Rate limiting.

Two independent limiters live here:

* :class:`DomainRateLimiter` — politeness towards sites we crawl. It enforces a
  minimum delay between requests to the same domain by *waiting*, never by
  rotating identities or addresses. Every wait is recorded so the admin page
  can show rate-limit events.
* :class:`FixedWindowRateLimiter` — protects this application's own API from
  runaway clients.

Both are in-process. That is deliberate: a single-user tool does not need a
Redis cluster to be polite.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class RateLimitEvent:
    domain: str
    waited_seconds: float
    at: float = field(default_factory=time.time)


class DomainRateLimiter:
    """Enforces a minimum interval between requests to the same domain."""

    def __init__(self, delay_seconds: float = 2.0, *, max_events: int = 500) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()
        self._events: deque[RateLimitEvent] = deque(maxlen=max_events)

    def acquire(self, domain: str) -> float:
        """Block until it is polite to call ``domain``. Returns seconds waited."""
        domain = domain.lower()
        with self._lock:
            now = time.monotonic()
            last = self._last_request.get(domain)
            wait = 0.0
            if last is not None:
                elapsed = now - last
                if elapsed < self.delay_seconds:
                    wait = self.delay_seconds - elapsed
            # Reserve the slot before releasing the lock so concurrent callers
            # queue up behind each other instead of all sleeping the same wait.
            self._last_request[domain] = now + wait
        if wait > 0:
            self._events.append(RateLimitEvent(domain=domain, waited_seconds=wait))
            time.sleep(wait)
        return wait

    @property
    def events(self) -> list[RateLimitEvent]:
        return list(self._events)

    def recent_events(self, limit: int = 50) -> list[RateLimitEvent]:
        return list(self._events)[-limit:][::-1]

    def reset(self) -> None:
        with self._lock:
            self._last_request.clear()
        self._events.clear()


class FixedWindowRateLimiter:
    """Simple per-client fixed-window limiter for the HTTP API."""

    def __init__(self, limit_per_minute: int = 240) -> None:
        self.limit = max(1, limit_per_minute)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_id: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            bucket = self._hits[client_id]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# Shared instance used by every collector so politeness is global per process.
_domain_limiter: DomainRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_domain_rate_limiter() -> DomainRateLimiter:
    global _domain_limiter
    if _domain_limiter is None:
        with _limiter_lock:
            if _domain_limiter is None:
                from app.config import settings

                _domain_limiter = DomainRateLimiter(settings.crawler_domain_delay_seconds)
    return _domain_limiter
