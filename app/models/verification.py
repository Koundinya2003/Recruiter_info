"""Email verification results, one row per check."""

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

from app.db.base import Base, utcnow
from app.models.enums import VerificationStatus

if TYPE_CHECKING:
    from app.models.recruiter import Recruiter


class EmailVerification(Base):
    __tablename__ = "email_verifications"
    __table_args__ = (Index("ix_verification_email_checked", "email", "checked_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recruiter_id: Mapped[int | None] = mapped_column(
        ForeignKey("recruiters.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)

    status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, native_enum=False, length=20), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    recruiter: Mapped[Recruiter | None] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EmailVerification {self.email} {self.status}>"
