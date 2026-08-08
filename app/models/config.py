"""User-editable scoring weights and role taxonomy.

Both live in the database, not in code, because section 3 of the brief requires
the taxonomy to be configurable from the UI and section 6 requires the scoring
weights to be adjustable.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import TaxonomyKind


class ScoringConfig(Base, TimestampMixin):
    """A named set of scoring weights. Exactly one row per user is active."""

    __tablename__ = "scoring_configs"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_scoring_config_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), default="default", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    job_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    hiring_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    recruiter_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    outreach_weights: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ScoringConfig {self.name} active={self.is_active}>"


class TaxonomyTerm(Base, TimestampMixin):
    """One configurable matching term (a target role, industry, skill, ...)."""

    __tablename__ = "taxonomy_terms"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "term", name="uq_taxonomy_user_kind_term"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[TaxonomyKind] = mapped_column(
        Enum(TaxonomyKind, native_enum=False, length=20), nullable=False, index=True
    )
    term: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    # 0..1 — how much a match on this term counts relative to a perfect match.
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def all_terms(self) -> list[str]:
        return [self.term, *(self.aliases or [])]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaxonomyTerm {self.kind}:{self.term}>"
