"""SQLAlchemy models.

Importing this package registers every model on the shared ``Base.metadata``,
which is what Alembic autogeneration and the test fixtures rely on.
"""

from app.db.base import Base
from app.models.company import Company
from app.models.config import ScoringConfig, TaxonomyTerm
from app.models.crawl import CrawlRun, SourceRecord
from app.models.job import Job
from app.models.outreach import OutreachEvent, OutreachLead
from app.models.recruiter import Contact, JobRecruiterLink, Recruiter
from app.models.signal import HiringSignal
from app.models.user import User, UserProfile
from app.models.verification import EmailVerification

__all__ = [
    "Base",
    "Company",
    "Contact",
    "CrawlRun",
    "EmailVerification",
    "HiringSignal",
    "Job",
    "JobRecruiterLink",
    "OutreachEvent",
    "OutreachLead",
    "Recruiter",
    "ScoringConfig",
    "SourceRecord",
    "TaxonomyTerm",
    "User",
    "UserProfile",
]
