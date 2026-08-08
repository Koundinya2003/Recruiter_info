"""Shared response shapes and validators."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.security.url_guard import UnsafeURLError, validate_url

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    detail: str


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ScoreComponentOut(BaseModel):
    key: str
    label: str
    points: float
    max_points: float
    matched: bool
    reasons: list[str] = Field(default_factory=list)


class ScoreBreakdownOut(BaseModel):
    total: float = 0.0
    max_total: float = 0.0
    excluded: bool = False
    exclusion_reason: str | None = None
    components: list[ScoreComponentOut] = Field(default_factory=list)
    penalties: list[ScoreComponentOut] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> ScoreBreakdownOut:
        return cls.model_validate(data or {})


def validate_public_url(value: str | None) -> str | None:
    """Reject anything the crawler would refuse to fetch, at the API boundary."""
    if value is None or not str(value).strip():
        return None
    candidate = str(value).strip()
    try:
        validate_url(candidate)
    except UnsafeURLError as exc:
        raise ValueError(str(exc)) from exc
    return candidate


class URLField(BaseModel):
    """Mixin providing a reusable URL validator."""

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value
