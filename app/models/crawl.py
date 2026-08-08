"""Crawl observability.

`CrawlRun` is opened *before* any network call and always closed with a final
status, so a crawl that dies mid-flight is visible as RUNNING/FAILED rather
than silently absent. `SourceRecord` keeps one row per raw item seen, including
the ones we rejected and why.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
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

from app.db.base import Base, utcnow
from app.models.enums import CrawlStatus, CrawlTrigger, SourceType

if TYPE_CHECKING:
    from app.models.company import Company


class CrawlRun(Base):
    __tablename__ = "crawl_runs"
    __table_args__ = (Index("ix_crawl_started", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )

    collector: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, length=40), nullable=False
    )
    target_url: Mapped[str | None] = mapped_column(String(1000))
    trigger: Mapped[CrawlTrigger] = mapped_column(
        Enum(CrawlTrigger, native_enum=False, length=20),
        default=CrawlTrigger.MANUAL,
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CrawlStatus] = mapped_column(
        Enum(CrawlStatus, native_enum=False, length=20),
        default=CrawlStatus.RUNNING,
        nullable=False,
        index=True,
    )

    records_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_added: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bytes_downloaded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    rate_limit_waits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_limit_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company | None] = relationship()
    source_records: Mapped[list[SourceRecord]] = relationship(
        back_populates="crawl_run", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CrawlRun {self.id} {self.collector} {self.status}>"


class SourceRecord(Base):
    """A single raw item observed during a crawl, kept for auditability."""

    __tablename__ = "source_records"
    __table_args__ = (Index("ix_source_record_hash", "content_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    crawl_run_id: Mapped[int] = mapped_column(
        ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    record_type: Mapped[str] = mapped_column(String(40), nullable=False)  # job | recruiter | email
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, native_enum=False, length=40), nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(1000))
    external_id: Mapped[str | None] = mapped_column(String(200))
    content_hash: Mapped[str | None] = mapped_column(String(64))

    accepted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(String(300))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    crawl_run: Mapped[CrawlRun] = relationship(back_populates="source_records")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SourceRecord {self.record_type} accepted={self.accepted}>"
