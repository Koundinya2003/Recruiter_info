"""Profile and system response shapes."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ProfileOut(ORMModel):
    full_name: str | None = None
    headline: str | None = None
    years_experience: float | None = None
    default_titles: list[str] = Field(default_factory=list)
    default_locations: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    linkedin_url: str | None = None


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    headline: str | None = Field(default=None, max_length=300)
    years_experience: float | None = Field(default=None, ge=0, le=60)
    default_titles: list[str] = Field(default_factory=list)
    default_locations: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    linkedin_url: str | None = Field(default=None, max_length=500)


class HealthOut(BaseModel):
    status: str
    app_env: str
    database: str
    ai_configured: bool
    auth_enabled: bool
    sources_usable: int
    sources_total: int
    jobs: int
    applications: int
