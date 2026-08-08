"""Hiring signals — the evidence behind a company's hiring activity score."""

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow
from app.models.enums import SignalType

if TYPE_CHECKING:
    from app.models.company import Company


class HiringSignal(Base, TimestampMixin):
    __tablename__ = "hiring_signals"
    __table_args__ = (Index("ix_signal_company_detected", "company_id", "detected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )

    signal_type: Mapped[SignalType] = mapped_column(
        Enum(SignalType, native_enum=False, length=40), nullable=False
    )
    points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company: Mapped[Company] = relationship(back_populates="signals")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HiringSignal {self.signal_type} +{self.points}>"
