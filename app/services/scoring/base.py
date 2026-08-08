"""Explainable score primitives.

Every score in this application is a sum of named components, and every
component carries the human-readable reasons that produced it. Nothing returns
a bare number — section 6 of the brief ("do not make the score a black box")
is enforced structurally rather than by convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScoreComponent:
    key: str
    label: str
    points: float
    max_points: float
    reasons: list[str] = field(default_factory=list)
    matched: bool = False

    def __post_init__(self) -> None:
        self.points = round(max(0.0, min(float(self.points), float(self.max_points))), 2)

    @property
    def ratio(self) -> float:
        return self.points / self.max_points if self.max_points else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "points": self.points,
            "max_points": self.max_points,
            "matched": self.matched,
            "reasons": self.reasons,
        }


@dataclass
class ScoreResult:
    """A total out of 100 plus the components and reasons that produced it."""

    components: list[ScoreComponent] = field(default_factory=list)
    penalties: list[ScoreComponent] = field(default_factory=list)
    excluded: bool = False
    exclusion_reason: str | None = None

    def add(
        self,
        key: str,
        label: str,
        points: float,
        max_points: float,
        reasons: list[str] | None = None,
        *,
        matched: bool | None = None,
    ) -> ScoreComponent:
        component = ScoreComponent(
            key=key,
            label=label,
            points=points,
            max_points=max_points,
            reasons=reasons or [],
            matched=matched if matched is not None else points > 0,
        )
        self.components.append(component)
        return component

    def penalise(self, key: str, label: str, points: float, reason: str) -> None:
        self.penalties.append(
            ScoreComponent(
                key=key, label=label, points=points, max_points=points, reasons=[reason], matched=True
            )
        )

    @property
    def max_total(self) -> float:
        return round(sum(c.max_points for c in self.components), 2)

    @property
    def raw_total(self) -> float:
        return sum(c.points for c in self.components) - sum(p.points for p in self.penalties)

    @property
    def total(self) -> float:
        """Normalised to 0..100 against the configured maximum."""
        if self.excluded:
            return 0.0
        maximum = self.max_total
        if maximum <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (self.raw_total / maximum) * 100.0)), 1)

    @property
    def reasons(self) -> list[str]:
        """Positive reasons, best component first — what the UI shows as ✓."""
        out: list[str] = []
        for component in sorted(self.components, key=lambda c: -c.points):
            if component.matched and component.points > 0:
                out.extend(component.reasons)
        return out

    @property
    def gaps(self) -> list[str]:
        """Reasons a score is not higher — shown as ✗."""
        out: list[str] = []
        for component in self.components:
            if not component.matched or component.points <= 0:
                out.extend(component.reasons)
        for penalty in self.penalties:
            out.extend(penalty.reasons)
        if self.exclusion_reason:
            out.append(self.exclusion_reason)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "max_total": self.max_total,
            "excluded": self.excluded,
            "exclusion_reason": self.exclusion_reason,
            "components": [c.to_dict() for c in self.components],
            "penalties": [p.to_dict() for p in self.penalties],
            "reasons": self.reasons,
            "gaps": self.gaps,
        }


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def decay(age_hours: float | None, full_hours: float, zero_hours: float) -> float:
    """1.0 up to ``full_hours``, then linear down to 0.0 at ``zero_hours``."""
    if age_hours is None:
        return 0.0
    if age_hours <= full_hours:
        return 1.0
    if age_hours >= zero_hours:
        return 0.0
    span = zero_hours - full_hours
    if span <= 0:
        return 0.0
    return clamp(1.0 - (age_hours - full_hours) / span)


def humanize_age(age_hours: float | None) -> str:
    if age_hours is None:
        return "unknown age"
    if age_hours < 1:
        minutes = max(1, int(age_hours * 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if age_hours < 48:
        hours = int(age_hours)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(age_hours / 24)
    return f"{days} day{'s' if days != 1 else ''} ago"
