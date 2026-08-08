"""Lenient date parsing for the wide variety of formats job sources emit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%b %d, %Y",
    "%d %b %Y",
)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_datetime(value: Any) -> datetime | None:
    """Parse ISO-8601, epoch seconds/milliseconds, or common date strings."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)

    if isinstance(value, (int, float)):
        seconds = float(value)
        # Heuristic: anything past year ~2286 in seconds is milliseconds.
        if seconds > 1e11:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_datetime(int(text))

    iso_candidate = text.replace("Z", "+00:00")
    try:
        return ensure_utc(datetime.fromisoformat(iso_candidate))
    except ValueError:
        pass

    for fmt in _FORMATS:
        try:
            return ensure_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None
