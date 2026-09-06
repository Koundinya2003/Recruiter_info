"""The structured form of a search request."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import ExperienceLevel
from app.search.experience import ExperienceRange
from app.search.gazetteer import country_for, expand_title, industry_keywords
from app.utils.text import basic_normalize

DEFAULT_LIMIT = 40
MAX_LIMIT = 120


@dataclass
class JobQuery:
    """What to look for. Produced by the parser, consumed by every provider."""

    raw: str = ""
    titles: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)

    min_years: float | None = None
    max_years: float | None = None
    experience_level: ExperienceLevel | None = None
    experience_text: str | None = None

    remote_only: bool = False
    max_age_days: int = 45
    limit: int = DEFAULT_LIMIT

    parse_method: str = "rules"
    parse_notes: list[str] = field(default_factory=list)

    # -- derived -------------------------------------------------------------
    @property
    def primary_title(self) -> str:
        return self.titles[0] if self.titles else ""

    @property
    def search_titles(self) -> list[str]:
        """Titles plus close variants, deduped, best-first."""
        out: list[str] = []
        seen: set[str] = set()
        for title in self.titles:
            for variant in expand_title(title):
                key = basic_normalize(variant)
                if key and key not in seen:
                    seen.add(key)
                    out.append(variant)
        return out

    @property
    def countries(self) -> list[str]:
        return country_for(self.locations)

    @property
    def experience(self) -> ExperienceRange:
        return ExperienceRange(
            min_years=self.min_years,
            max_years=self.max_years,
            level=self.experience_level,
            text=self.experience_text,
        )

    @property
    def industry_keywords(self) -> list[str]:
        out: list[str] = []
        for industry in self.industries:
            out.extend(industry_keywords(industry))
        return out

    @property
    def is_empty(self) -> bool:
        return not (self.titles or self.keywords or self.companies)

    def describe(self) -> str:
        """A one-line restatement of what was understood, for the UI to echo."""
        parts: list[str] = []
        if self.titles:
            parts.append(" / ".join(self.titles))
        else:
            parts.append("any role")
        if self.experience.is_stated or self.experience_level:
            from app.search.experience import describe as describe_experience

            parts.append(describe_experience(self.experience))
        if self.remote_only:
            parts.append("remote")
        if self.locations:
            parts.append("in " + ", ".join(self.locations))
        if self.companies:
            parts.append("at " + ", ".join(self.companies))
        if self.industries:
            parts.append("(" + ", ".join(self.industries) + ")")
        if self.keywords:
            parts.append("with " + ", ".join(self.keywords))
        return " · ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "titles": self.titles,
            "locations": self.locations,
            "companies": self.companies,
            "industries": self.industries,
            "keywords": self.keywords,
            "exclusions": self.exclusions,
            "min_years": self.min_years,
            "max_years": self.max_years,
            "experience_level": self.experience_level.value if self.experience_level else None,
            "experience_text": self.experience_text,
            "remote_only": self.remote_only,
            "max_age_days": self.max_age_days,
            "limit": self.limit,
            "parse_method": self.parse_method,
            "parse_notes": self.parse_notes,
            "summary": self.describe(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobQuery:
        level_raw = data.get("experience_level")
        level: ExperienceLevel | None = None
        if level_raw:
            try:
                level = ExperienceLevel(level_raw)
            except ValueError:
                level = None
        limit = int(data.get("limit") or DEFAULT_LIMIT)
        return cls(
            raw=str(data.get("raw") or ""),
            titles=[str(t) for t in data.get("titles") or []],
            locations=[str(t) for t in data.get("locations") or []],
            companies=[str(t) for t in data.get("companies") or []],
            industries=[str(t) for t in data.get("industries") or []],
            keywords=[str(t) for t in data.get("keywords") or []],
            exclusions=[str(t) for t in data.get("exclusions") or []],
            min_years=_as_float(data.get("min_years")),
            max_years=_as_float(data.get("max_years")),
            experience_level=level,
            experience_text=data.get("experience_text") or None,
            remote_only=bool(data.get("remote_only")),
            max_age_days=int(data.get("max_age_days") or 45),
            limit=max(1, min(limit, MAX_LIMIT)),
            parse_method=str(data.get("parse_method") or "rules"),
            parse_notes=[str(n) for n in data.get("parse_notes") or []],
        )


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
