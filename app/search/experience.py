"""Reading experience requirements out of free text.

Used twice: on the user's request ("0–2 years") and on a posting's own wording
("Minimum 3 years of experience"). Both go through the same code so the two
sides are compared on the same terms.

When nothing is stated the result is ``(None, None)`` — never a default range.
A posting that does not state a requirement is reported as "Not stated", which
is the truth and lets the user decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.enums import ExperienceLevel

# 0-2 years / 0 – 2 yrs / 1 to 3 years
_RANGE = re.compile(
    r"\b(\d{1,2})\s*(?:-|–|—|to|~)\s*(\d{1,2})\s*\+?\s*(?:\+)?\s*(?:years?|yrs?|y)\b",
    re.IGNORECASE,
)
# 3+ years / 5 or more years / at least 2 years / minimum of 4 years
_MINIMUM = re.compile(
    r"\b(?:(?:at\s+least|minimum(?:\s+of)?|min\.?|more\s+than|over)\s*)?"
    r"(\d{1,2})\s*(?:\+|plus|or\s+more)?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_HAS_PLUS = re.compile(r"\b(\d{1,2})\s*(?:\+|plus|or\s+more)\s*(?:years?|yrs?)\b", re.IGNORECASE)
# up to 3 years / less than 2 years / under 4 years
_MAXIMUM = re.compile(
    r"\b(?:up\s+to|under|less\s+than|below|max(?:imum)?(?:\s+of)?)\s*(\d{1,2})\s*"
    r"(?:years?|yrs?)\b",
    re.IGNORECASE,
)

# Phrases that state a level rather than a number, most specific first.
LEVEL_PATTERNS: tuple[tuple[re.Pattern[str], ExperienceLevel], ...] = (
    (re.compile(r"\b(interns?|internships?|trainees?|apprentices?)\b", re.I), ExperienceLevel.INTERNSHIP),
    (
        re.compile(
            r"\b(freshers?|fresh\s+graduates?|new\s+grads?|"
            r"graduate\s+(?:roles?|programmes?|programs?)|"
            r"entry[\s\-]?level|campus\s+hires?|0\s*years?)\b",
            re.I,
        ),
        ExperienceLevel.ENTRY,
    ),
    (
        re.compile(r"\b(junior|associate|early\s+career|jr\.?)\b", re.I),
        ExperienceLevel.ENTRY,
    ),
    (re.compile(r"\b(mid[\s\-]?level|intermediate)\b", re.I), ExperienceLevel.MID),
    (re.compile(r"\b(senior|sr\.?|staff)\b", re.I), ExperienceLevel.SENIOR),
    (
        re.compile(r"\b(lead|principal|head\s+of|manager\s+of\s+managers)\b", re.I),
        ExperienceLevel.LEAD,
    ),
    (
        re.compile(r"\b(director|vp|vice\s+president|chief|c-level|cxo)\b", re.I),
        ExperienceLevel.EXECUTIVE,
    ),
)

MAX_PLAUSIBLE_YEARS = 40.0


@dataclass(frozen=True)
class ExperienceRange:
    """A parsed experience requirement. ``text`` is the phrase it came from."""

    min_years: float | None = None
    max_years: float | None = None
    level: ExperienceLevel | None = None
    text: str | None = None

    @property
    def is_stated(self) -> bool:
        return self.min_years is not None or self.max_years is not None

    @property
    def bounds(self) -> tuple[float, float] | None:
        """Concrete bounds, falling back to the level's band when needed."""
        if self.min_years is not None or self.max_years is not None:
            low = self.min_years if self.min_years is not None else 0.0
            high = self.max_years if self.max_years is not None else MAX_PLAUSIBLE_YEARS
            return (low, high)
        if self.level is not None:
            return self.level.years
        return None

    def overlaps(self, other: ExperienceRange) -> bool | None:
        """Whether two ranges are compatible. ``None`` when either is unstated."""
        mine, theirs = self.bounds, other.bounds
        if mine is None or theirs is None:
            return None
        return mine[0] <= theirs[1] and theirs[0] <= mine[1]


def _clamp(value: float) -> float:
    return max(0.0, min(value, MAX_PLAUSIBLE_YEARS))


def parse_experience(text: str | None, *, search_window: int = 4000) -> ExperienceRange:
    """Extract an experience requirement from free text.

    Only the first ``search_window`` characters are scanned: requirements are
    stated near the top of a posting, while the tail is usually boilerplate
    that mentions unrelated numbers of years.
    """
    if not text:
        return ExperienceRange()
    haystack = text[:search_window]

    level: ExperienceLevel | None = None
    for pattern, candidate in LEVEL_PATTERNS:
        if pattern.search(haystack):
            level = candidate
            break

    match = _RANGE.search(haystack)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        if low > high:
            low, high = high, low
        return ExperienceRange(
            min_years=_clamp(low),
            max_years=_clamp(high),
            level=level,
            text=match.group(0).strip(),
        )

    match = _MAXIMUM.search(haystack)
    if match:
        return ExperienceRange(
            min_years=0.0,
            max_years=_clamp(float(match.group(1))),
            level=level,
            text=match.group(0).strip(),
        )

    match = _HAS_PLUS.search(haystack)
    if match:
        return ExperienceRange(
            min_years=_clamp(float(match.group(1))),
            max_years=None,
            level=level,
            text=match.group(0).strip(),
        )

    match = _MINIMUM.search(haystack)
    if match:
        years = _clamp(float(match.group(1)))
        # A bare "2 years" is a floor, not an exact requirement.
        return ExperienceRange(
            min_years=years, max_years=None, level=level, text=match.group(0).strip()
        )

    if level is not None:
        return ExperienceRange(level=level, text=None)
    return ExperienceRange()


def describe(range_: ExperienceRange) -> str:
    """A short human phrase for a range, honest when nothing was stated."""
    if range_.text:
        return range_.text
    if range_.min_years is None and range_.max_years is None:
        return range_.level.label if range_.level else "Not stated"
    if range_.max_years is None:
        return f"{range_.min_years:g}+ years"
    if range_.min_years is None:
        return f"Up to {range_.max_years:g} years"
    return f"{range_.min_years:g}–{range_.max_years:g} years"
