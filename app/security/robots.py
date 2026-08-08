"""robots.txt compliance.

We fetch ``/robots.txt`` for every domain before crawling it and cache the
parsed result for the process lifetime. If a path is disallowed for our
User-Agent we do not fetch it — there is no override flag, and no "stealth"
mode. A site's crawl-delay directive, when present and larger than our
configured delay, wins.

Fetch failures are handled conservatively but pragmatically, mirroring the
robots exclusion standard: a 404 (no robots.txt) means "allowed", while a
network error or a 5xx means "we could not determine the policy" and we
decline to crawl.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.logging_config import get_logger

log = get_logger(__name__)

CACHE_TTL_SECONDS = 3600.0


@dataclass
class RobotsDecision:
    allowed: bool
    reason: str
    crawl_delay: float | None = None


@dataclass
class _CacheEntry:
    parser: RobotFileParser | None
    fetched_at: float
    available: bool
    reason: str


class RobotsChecker:
    """Caches and evaluates robots.txt policies per origin."""

    def __init__(self, user_agent: str, *, timeout: float = 10.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    def _origin(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _load(self, origin: str) -> _CacheEntry:
        with self._lock:
            entry = self._cache.get(origin)
            if entry and (time.monotonic() - entry.fetched_at) < CACHE_TTL_SECONDS:
                return entry

        robots_url = f"{origin}/robots.txt"
        try:
            response = httpx.get(
                robots_url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            log.warning("robots.fetch_failed", origin=origin, error=str(exc))
            entry = _CacheEntry(None, time.monotonic(), False, f"robots.txt unreachable: {exc}")
        else:
            if response.status_code == 404:
                parser = RobotFileParser()
                parser.parse([])
                entry = _CacheEntry(parser, time.monotonic(), True, "no robots.txt (404)")
            elif response.status_code >= 400:
                entry = _CacheEntry(
                    None,
                    time.monotonic(),
                    False,
                    f"robots.txt returned HTTP {response.status_code}",
                )
            else:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
                entry = _CacheEntry(parser, time.monotonic(), True, "robots.txt parsed")

        with self._lock:
            self._cache[origin] = entry
        return entry

    def check(self, url: str) -> RobotsDecision:
        origin = self._origin(url)
        entry = self._load(origin)
        if not entry.available or entry.parser is None:
            return RobotsDecision(False, entry.reason)

        allowed = entry.parser.can_fetch(self.user_agent, url)
        delay = entry.parser.crawl_delay(self.user_agent)
        try:
            crawl_delay = float(delay) if delay is not None else None
        except (TypeError, ValueError):
            crawl_delay = None

        if not allowed:
            return RobotsDecision(False, "disallowed by robots.txt", crawl_delay)
        return RobotsDecision(True, entry.reason, crawl_delay)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_checker: RobotsChecker | None = None
_checker_lock = threading.Lock()


def get_robots_checker() -> RobotsChecker:
    global _checker
    if _checker is None:
        with _checker_lock:
            if _checker is None:
                from app.config import settings

                _checker = RobotsChecker(settings.crawler_user_agent)
    return _checker
