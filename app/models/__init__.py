"""SQLAlchemy models.

Importing this package registers every model on the shared ``Base.metadata``,
which is what Alembic autogeneration and the test fixtures rely on.
"""

from app.db.base import Base
from app.models.application import Application, ApplicationEvent
from app.models.company import Company
from app.models.contact import Contact, JobContact
from app.models.job import Job
from app.models.search import JobSearch, SearchRun
from app.models.user import User, UserProfile

__all__ = [
    "Application",
    "ApplicationEvent",
    "Base",
    "Company",
    "Contact",
    "Job",
    "JobContact",
    "JobSearch",
    "SearchRun",
    "User",
    "UserProfile",
]
