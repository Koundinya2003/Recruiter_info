"""Domain enumerations shared by models, schemas and services.

Stored as VARCHAR with a CHECK constraint (``native_enum=False``) rather than
PostgreSQL ENUM types, so adding a value is an ordinary migration instead of a
type rewrite.
"""

from __future__ import annotations

from enum import StrEnum


class CompanyPriority(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def weight(self) -> float:
        """0..1 multiplier used by the outreach priority engine."""
        return {"CRITICAL": 1.0, "HIGH": 0.8, "MEDIUM": 0.55, "LOW": 0.3}[self.value]


class JobStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class SourceType(StrEnum):
    """Where a record came from. Every source here is public or an official API."""

    CAREER_PAGE = "CAREER_PAGE"
    CAREER_PAGE_JSONLD = "CAREER_PAGE_JSONLD"
    GREENHOUSE_PUBLIC_API = "GREENHOUSE_PUBLIC_API"
    LEVER_PUBLIC_API = "LEVER_PUBLIC_API"
    ASHBY_PUBLIC_API = "ASHBY_PUBLIC_API"
    PUBLIC_JOB_FEED = "PUBLIC_JOB_FEED"
    COMPANY_TEAM_PAGE = "COMPANY_TEAM_PAGE"
    JOB_POSTING_CONTACT = "JOB_POSTING_CONTACT"
    PATTERN_INFERENCE = "PATTERN_INFERENCE"
    MANUAL_ENTRY = "MANUAL_ENTRY"
    DEMO_SEED = "DEMO_SEED"


class EmailConfidence(StrEnum):
    """How strongly a contact address is tied to the named individual."""

    HIGH = "HIGH"      # explicitly published, attributed to this person
    MEDIUM = "MEDIUM"  # published by the company, not attributed to the person
    LOW = "LOW"        # pattern-based inference — never treated as verified
    NONE = "NONE"      # no address known

    @property
    def score(self) -> int:
        return {"HIGH": 100, "MEDIUM": 65, "LOW": 30, "NONE": 0}[self.value]

    @property
    def is_inferred(self) -> bool:
        return self is EmailConfidence.LOW


class VerificationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"
    NOT_CHECKED = "not_checked"


class ContactType(StrEnum):
    EMAIL = "EMAIL"
    PROFILE_URL = "PROFILE_URL"
    APPLICATION_FORM = "APPLICATION_FORM"


class OutreachStatus(StrEnum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    CONTACTED = "CONTACTED"
    REPLIED = "REPLIED"
    FOLLOW_UP = "FOLLOW_UP"
    ARCHIVED = "ARCHIVED"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"


class ResponseStatus(StrEnum):
    AWAITING = "AWAITING"
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NO_RESPONSE = "NO_RESPONSE"


class OutreachEventType(StrEnum):
    LEAD_CREATED = "LEAD_CREATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    DRAFT_GENERATED = "DRAFT_GENERATED"
    DRAFT_EDITED = "DRAFT_EDITED"
    DRAFT_APPROVED = "DRAFT_APPROVED"
    CONTACT_RECORDED = "CONTACT_RECORDED"
    RESPONSE_RECORDED = "RESPONSE_RECORDED"
    NOTE_ADDED = "NOTE_ADDED"
    DO_NOT_CONTACT_SET = "DO_NOT_CONTACT_SET"
    EMAIL_VERIFIED = "EMAIL_VERIFIED"


class SignalType(StrEnum):
    NEW_JOB = "NEW_JOB"
    FRESH_JOB = "FRESH_JOB"
    MULTIPLE_OPENINGS = "MULTIPLE_OPENINGS"
    RECRUITER_IDENTIFIED = "RECRUITER_IDENTIFIED"
    CAREER_PAGE_ACTIVITY = "CAREER_PAGE_ACTIVITY"
    HIGH_RELEVANCE_ROLE = "HIGH_RELEVANCE_ROLE"


class CrawlStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


class CrawlTrigger(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    SEED = "SEED"


class TaxonomyKind(StrEnum):
    ROLE = "ROLE"
    INDUSTRY = "INDUSTRY"
    SKILL = "SKILL"
    LOCATION = "LOCATION"
    SENIORITY = "SENIORITY"
    EXCLUDE = "EXCLUDE"


class RecruiterRelation(StrEnum):
    POSTED_BY = "POSTED_BY"
    COMPANY_TALENT_TEAM = "COMPANY_TALENT_TEAM"
    FUNCTION_MATCH = "FUNCTION_MATCH"
    LISTED_CONTACT = "LISTED_CONTACT"


PRIORITY_BANDS: tuple[tuple[int, str], ...] = (
    (90, "CONTACT NOW"),
    (85, "HIGH PRIORITY"),
    (70, "GOOD OPPORTUNITY"),
    (60, "REVIEW"),
    (0, "LOW PRIORITY"),
)


def priority_band(score: float) -> str:
    for threshold, label in PRIORITY_BANDS:
        if score >= threshold:
            return label
    return "LOW PRIORITY"
