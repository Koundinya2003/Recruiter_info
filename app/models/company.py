"""Companies being monitored."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.enums import CompanyPriority

if TYPE_CHECKING:
    from app.models.job import Job
    from app.models.recruiter import Recruiter
    from app.models.signal import HiringSignal
    from app.models.user import User


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("user_id", "normalized_name", name="uq_company_user_normalized_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    company_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    career_page_url: Mapped[str | None] = mapped_column(String(1000))
    industry: Mapped[str | None] = mapped_column(String(120), index=True)
    priority: Mapped[CompanyPriority] = mapped_column(
        Enum(CompanyPriority, native_enum=False, length=20),
        default=CompanyPriority.MEDIUM,
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_status: Mapped[str | None] = mapped_column(String(40))

    # Cached hiring activity score, recomputed on every scan.
    hiring_activity_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    hiring_activity_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="companies")
    jobs: Mapped[list[Job]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )
    recruiters: Mapped[list[Recruiter]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )
    signals: Mapped[list[HiringSignal]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Company {self.id} {self.company_name}>"
