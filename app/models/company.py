"""Companies, derived from the job postings that were found.

This is not a watchlist. A row appears here because a validated posting named
the company, and it exists so contacts and postings for the same employer can
be grouped and cached across searches.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.job import Job


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_company_normalized_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), nullable=False, index=True)

    # Confirmed by following a posting URL, never guessed from the name.
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    website_url: Mapped[str | None] = mapped_column(String(1000))
    careers_url: Mapped[str | None] = mapped_column(String(1000))

    # When contact discovery last ran for this company, so repeat searches do
    # not re-crawl the same pages.
    contacts_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    jobs: Mapped[list[Job]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )
    contacts: Mapped[list[Contact]] = relationship(
        back_populates="company", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Company {self.id} {self.name!r}>"
