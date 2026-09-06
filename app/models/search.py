"""Saved searches and the runs that executed them.

A ``JobSearch`` is what the user typed, plus the structured criteria parsed out
of it. A ``SearchRun`` is one execution: which providers answered, how many
postings came back, and how many survived validation. The run record is what
lets the UI say honestly "3 of 41 postings could not be confirmed".
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.enums import SearchRunStatus

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.user import User


class JobSearch(Base, TimestampMixin):
    __tablename__ = "job_searches"
    __table_args__ = (Index("ix_search_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Exactly what the user typed, kept verbatim so the parse can be re-run.
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(String(300))

    # --- Parsed criteria ----------------------------------------------------
    titles: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    locations: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    companies: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    industries: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    exclusions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    min_years: Mapped[float | None] = mapped_column(Float)
    max_years: Mapped[float | None] = mapped_column(Float)
    experience_level: Mapped[str | None] = mapped_column(String(30))
    remote_only: Mapped[bool] = mapped_column(default=False, nullable=False)

    # How the criteria above were produced ("rules" or "rules+llm"), so the UI
    # can show the user what it understood and let them correct it.
    parse_method: Mapped[str] = mapped_column(String(30), default="rules", nullable=False)
    parse_notes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="searches")
    runs: Mapped[list[SearchRun]] = relationship(
        back_populates="search",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SearchRun.started_at.desc()",
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="search", passive_deletes=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<JobSearch {self.id} {self.raw_query[:40]!r}>"


class SearchRun(Base):
    """One execution of a search. Append-only; never edited after it finishes."""

    __tablename__ = "search_runs"
    __table_args__ = (Index("ix_run_search_started", "search_id", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_id: Mapped[int] = mapped_column(
        ForeignKey("job_searches.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[SearchRunStatus] = mapped_column(
        Enum(SearchRunStatus, native_enum=False, length=20),
        default=SearchRunStatus.RUNNING,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Funnel counters ----------------------------------------------------
    providers_queried: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    providers_skipped: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    raw_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicates_dropped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    irrelevant_dropped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    validated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unverified: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    contacts_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    errors: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    notes: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    search: Mapped[JobSearch] = relationship(back_populates="runs")

    @property
    def elapsed_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SearchRun {self.id} {self.status} kept={self.validated}>"
