"""Domain enumerations shared by models, schemas and services.

Stored as VARCHAR with a CHECK constraint (``native_enum=False``) rather than
PostgreSQL ENUM types, so adding a value is an ordinary migration instead of a
type rewrite.
"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """Where a job or contact came from.

    Every value here is a public API or a page the company published itself.
    There is deliberately no value for "generated", "inferred" or "guessed"
    jobs: this product never invents a posting.
    """

    # --- Job sources -------------------------------------------------------
    ADZUNA = "ADZUNA"
    THE_MUSE = "THE_MUSE"
    REMOTIVE = "REMOTIVE"
    ARBEITNOW = "ARBEITNOW"
    JOBICY = "JOBICY"
    USAJOBS = "USAJOBS"
    GREENHOUSE = "GREENHOUSE"
    LEVER = "LEVER"
    ASHBY = "ASHBY"
    CAREER_PAGE = "CAREER_PAGE"

    # --- Contact sources ---------------------------------------------------
    JOB_POSTING_CONTACT = "JOB_POSTING_CONTACT"
    COMPANY_TEAM_PAGE = "COMPANY_TEAM_PAGE"
    COMPANY_CAREERS_PAGE = "COMPANY_CAREERS_PAGE"
    DIRECTORY_SEARCH_LINK = "DIRECTORY_SEARCH_LINK"
    MANUAL_ENTRY = "MANUAL_ENTRY"

    @property
    def label(self) -> str:
        return {
            "ADZUNA": "Adzuna",
            "THE_MUSE": "The Muse",
            "REMOTIVE": "Remotive",
            "ARBEITNOW": "Arbeitnow",
            "JOBICY": "Jobicy",
            "USAJOBS": "USAJobs",
            "GREENHOUSE": "Greenhouse (company board)",
            "LEVER": "Lever (company board)",
            "ASHBY": "Ashby (company board)",
            "CAREER_PAGE": "Company career page",
            "JOB_POSTING_CONTACT": "Named on the job posting",
            "COMPANY_TEAM_PAGE": "Company team page",
            "COMPANY_CAREERS_PAGE": "Company careers page",
            "DIRECTORY_SEARCH_LINK": "Directory search link",
            "MANUAL_ENTRY": "Entered by you",
        }[self.value]

    @property
    def is_first_party(self) -> bool:
        """True when the posting came from the employer's own live job board.

        A first-party source is self-validating: the company's own API only
        returns roles it is currently advertising.
        """
        return self in {
            SourceType.GREENHOUSE,
            SourceType.LEVER,
            SourceType.ASHBY,
            SourceType.CAREER_PAGE,
        }


class ValidationStatus(StrEnum):
    """The outcome of checking a job posting before it is shown.

    Only ``VALID``, ``LIKELY_VALID`` and ``UNVERIFIED`` are ever displayed, and
    the last of those is always labelled as unconfirmed. Everything else is
    withheld.
    """

    PENDING = "PENDING"
    VALID = "VALID"
    LIKELY_VALID = "LIKELY_VALID"
    UNVERIFIED = "UNVERIFIED"
    EXPIRED = "EXPIRED"
    BROKEN = "BROKEN"
    DUPLICATE = "DUPLICATE"
    IRRELEVANT = "IRRELEVANT"
    MISMATCH = "MISMATCH"

    @property
    def is_displayable(self) -> bool:
        return self in {
            ValidationStatus.VALID,
            ValidationStatus.LIKELY_VALID,
            ValidationStatus.UNVERIFIED,
        }

    @property
    def is_confirmed(self) -> bool:
        """True only when the posting was actually reached and checked."""
        return self in {ValidationStatus.VALID, ValidationStatus.LIKELY_VALID}

    @property
    def label(self) -> str:
        return {
            "PENDING": "Not yet checked",
            "VALID": "Verified active",
            "LIKELY_VALID": "Reachable, active",
            "UNVERIFIED": "Could not confirm",
            "EXPIRED": "Closed or expired",
            "BROKEN": "Link does not work",
            "DUPLICATE": "Duplicate posting",
            "IRRELEVANT": "Does not match the search",
            "MISMATCH": "Posting belongs to another company",
        }[self.value]


class ValidationCheck(StrEnum):
    """The individual checks a posting is put through."""

    URL_REACHABLE = "URL_REACHABLE"
    COMPANY_MATCHES = "COMPANY_MATCHES"
    STILL_ACTIVE = "STILL_ACTIVE"
    NOT_DUPLICATE = "NOT_DUPLICATE"
    RELEVANT = "RELEVANT"


class CheckOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ContactRole(StrEnum):
    """Why this person is worth contacting about this role, best first."""

    FUNCTION_RECRUITER = "FUNCTION_RECRUITER"
    TALENT_ACQUISITION = "TALENT_ACQUISITION"
    HIRING_MANAGER = "HIRING_MANAGER"
    TEAM_LEAD = "TEAM_LEAD"
    TALENT_ALIAS = "TALENT_ALIAS"
    SEARCH_LINK = "SEARCH_LINK"

    @property
    def rank(self) -> int:
        """Lower sorts first."""
        return {
            "FUNCTION_RECRUITER": 0,
            "TALENT_ACQUISITION": 1,
            "HIRING_MANAGER": 2,
            "TEAM_LEAD": 3,
            "TALENT_ALIAS": 4,
            "SEARCH_LINK": 5,
        }[self.value]

    @property
    def label(self) -> str:
        return {
            "FUNCTION_RECRUITER": "Recruiter for this function",
            "TALENT_ACQUISITION": "Talent acquisition",
            "HIRING_MANAGER": "Hiring manager",
            "TEAM_LEAD": "Team / functional lead",
            "TALENT_ALIAS": "Company talent inbox",
            "SEARCH_LINK": "Directory search",
        }[self.value]

    @property
    def is_person(self) -> bool:
        """False for entries that are an inbox or a search link, not a human."""
        return self not in {ContactRole.TALENT_ALIAS, ContactRole.SEARCH_LINK}


class EmailStatus(StrEnum):
    """How an address came to be known. There is no "guessed" value on purpose.

    The product never derives an address from a name-and-domain pattern, so an
    address is either published somewhere we can cite, or absent.
    """

    NONE = "NONE"
    PUBLISHED_ATTRIBUTED = "PUBLISHED_ATTRIBUTED"
    PUBLISHED_TEAM_ALIAS = "PUBLISHED_TEAM_ALIAS"
    USER_PROVIDED = "USER_PROVIDED"

    @property
    def label(self) -> str:
        return {
            "NONE": "No public address found",
            "PUBLISHED_ATTRIBUTED": "Published and attributed to this person",
            "PUBLISHED_TEAM_ALIAS": "Published company address (not personal)",
            "USER_PROVIDED": "Added by you",
        }[self.value]


class VerificationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    RISKY = "risky"
    UNKNOWN = "unknown"
    NOT_CHECKED = "not_checked"


class ApplicationStatus(StrEnum):
    """The tracker pipeline. Advanced by the user, never automatically."""

    SAVED = "SAVED"
    APPLIED = "APPLIED"
    OUTREACH_SENT = "OUTREACH_SENT"
    INTERVIEW = "INTERVIEW"
    REJECTED = "REJECTED"
    OFFER = "OFFER"
    CLOSED = "CLOSED"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()

    @property
    def is_open(self) -> bool:
        return self not in {ApplicationStatus.REJECTED, ApplicationStatus.CLOSED}

    @property
    def counts_as_applied(self) -> bool:
        """Statuses that mean an application actually went in."""
        return self in {
            ApplicationStatus.APPLIED,
            ApplicationStatus.OUTREACH_SENT,
            ApplicationStatus.INTERVIEW,
            ApplicationStatus.REJECTED,
            ApplicationStatus.OFFER,
        }


APPLICATION_PIPELINE: tuple[ApplicationStatus, ...] = (
    ApplicationStatus.SAVED,
    ApplicationStatus.APPLIED,
    ApplicationStatus.OUTREACH_SENT,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.REJECTED,
    ApplicationStatus.OFFER,
    ApplicationStatus.CLOSED,
)


class OutreachStatus(StrEnum):
    """Whether the user has contacted anyone about this application."""

    NOT_STARTED = "NOT_STARTED"
    EMAIL_SENT = "EMAIL_SENT"
    LINKEDIN_SENT = "LINKEDIN_SENT"
    REPLIED = "REPLIED"
    NO_RESPONSE = "NO_RESPONSE"

    @property
    def label(self) -> str:
        return {
            "NOT_STARTED": "Not started",
            "EMAIL_SENT": "Email sent",
            "LINKEDIN_SENT": "LinkedIn message sent",
            "REPLIED": "They replied",
            "NO_RESPONSE": "No response",
        }[self.value]

    @property
    def is_sent(self) -> bool:
        return self is not OutreachStatus.NOT_STARTED


class SearchRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExperienceLevel(StrEnum):
    """Coarse experience bands, used when a query gives no explicit year range."""

    INTERNSHIP = "INTERNSHIP"
    ENTRY = "ENTRY"
    MID = "MID"
    SENIOR = "SENIOR"
    LEAD = "LEAD"
    EXECUTIVE = "EXECUTIVE"

    @property
    def years(self) -> tuple[float, float]:
        return {
            "INTERNSHIP": (0.0, 1.0),
            "ENTRY": (0.0, 2.0),
            "MID": (2.0, 5.0),
            "SENIOR": (5.0, 10.0),
            "LEAD": (8.0, 15.0),
            "EXECUTIVE": (12.0, 40.0),
        }[self.value]

    @property
    def label(self) -> str:
        return self.value.title()
