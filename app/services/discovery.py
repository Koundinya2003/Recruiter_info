"""The search pipeline — the vertical slice the whole product hangs off.

    parse request → query every usable source → dedupe → score against the
    request → validate the survivors → find contacts → persist

Two principles shape the order. First, validation runs *after* relevance
scoring, so the request budget is spent confirming postings the user might
actually want rather than ones we are about to discard. Second, every posting
that is dropped is counted and explained on the run record: a search that
returns four jobs out of ninety should be able to say where the other
eighty-six went.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.http_client import SafeHTTPClient
from app.config import settings
from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.company import Company
from app.models.contact import Contact, JobContact
from app.models.enums import ContactRole, SearchRunStatus, ValidationStatus
from app.models.job import Job
from app.models.search import JobSearch, SearchRun
from app.models.user import User
from app.providers.company_boards import PLATFORMS, Board
from app.providers.registry import build_providers, provider_statuses
from app.providers.types import RawJob
from app.search.query import JobQuery
from app.search.relevance import score_job
from app.services.contacts import (
    ContactCandidate,
    discover_contacts,
    domain_from_url,
)
from app.services.validation import (
    canonicalize_url,
    duplicate_key,
    validate_posting,
)
from app.utils.text import normalize_company_name, normalize_location, normalize_title, truncate

log = get_logger(__name__)

SUMMARY_LENGTH = 600


@dataclass
class Candidate:
    """A posting travelling through the pipeline."""

    raw: RawJob
    canonical_url: str
    fingerprint: str
    relevance: Any = None
    validation: Any = None

    @property
    def board_token(self) -> str | None:
        token = self.raw.payload.get("board")
        return str(token) if token else None


@dataclass
class SearchOutcome:
    """What a search produced, in the shape the API and UI need."""

    search_id: int
    run_id: int
    status: SearchRunStatus
    query: JobQuery
    job_ids: list[int] = field(default_factory=list)
    raw_found: int = 0
    duplicates_dropped: int = 0
    irrelevant_dropped: int = 0
    validated: int = 0
    unverified: int = 0
    rejected: int = 0
    contacts_found: int = 0
    providers_queried: list[str] = field(default_factory=list)
    providers_skipped: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_id": self.search_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "query": self.query.to_dict(),
            "job_ids": self.job_ids,
            "funnel": {
                "raw_found": self.raw_found,
                "duplicates_dropped": self.duplicates_dropped,
                "irrelevant_dropped": self.irrelevant_dropped,
                "validated": self.validated,
                "unverified": self.unverified,
                "rejected": self.rejected,
                "contacts_found": self.contacts_found,
            },
            "providers_queried": self.providers_queried,
            "providers_skipped": self.providers_skipped,
            "errors": self.errors,
            "notes": self.notes,
        }


# --- Persistence helpers ------------------------------------------------------


def get_or_create_company(session: Session, name: str, *, domain: str | None = None) -> Company:
    normalized = normalize_company_name(name)
    company = session.scalar(select(Company).where(Company.normalized_name == normalized))
    if company is None:
        company = Company(name=name.strip()[:300], normalized_name=normalized, domain=domain)
        session.add(company)
        session.flush()
    elif domain and not company.domain:
        company.domain = domain
    return company


def known_boards_for(session: Session) -> dict[str, Board]:
    """Boards already confirmed for a company, so repeats skip the probing."""
    boards: dict[str, Board] = {}
    rows = session.scalars(select(Company).where(Company.careers_url.is_not(None))).all()
    for company in rows:
        url = (company.careers_url or "").lower()
        for platform, config in PLATFORMS.items():
            if any(host in url for host in config["hosts"]):
                token = url.rstrip("/").rsplit("/", 1)[-1]
                if token:
                    boards[company.normalized_name] = Board(
                        platform=platform, token=token, company=company.name
                    )
                break
    return boards


def persist_contact(
    session: Session, company: Company, candidate: ContactCandidate
) -> Contact:
    existing = session.scalar(
        select(Contact).where(
            Contact.company_id == company.id, Contact.dedupe_key == candidate.dedupe_key
        )
    )
    if existing is not None:
        existing.last_seen_at = utcnow()
        # Upgrade a record when a better-evidenced version turns up.
        if candidate.email and not existing.email:
            existing.email = candidate.email
            existing.email_status = candidate.email_status
        if candidate.profile_url and not existing.profile_url:
            existing.profile_url = candidate.profile_url
        if candidate.title and not existing.title:
            existing.title = candidate.title
        return existing

    contact = Contact(
        company_id=company.id,
        name=candidate.name,
        dedupe_key=candidate.dedupe_key,
        title=candidate.title,
        company_name=company.name,
        role=candidate.role,
        profile_url=candidate.profile_url,
        search_url=candidate.search_url,
        email=candidate.email,
        email_status=candidate.email_status,
        source=candidate.source,
        source_url=candidate.source_url,
        source_excerpt=candidate.source_excerpt,
        confidence=candidate.confidence,
    )
    session.add(contact)
    session.flush()
    return contact


def link_contacts(session: Session, job: Job, contacts: list[tuple[Contact, str | None]]) -> int:
    created = 0
    for rank, (contact, rationale) in enumerate(contacts):
        existing = session.scalar(
            select(JobContact).where(
                JobContact.job_id == job.id, JobContact.contact_id == contact.id
            )
        )
        if existing is not None:
            existing.rank = rank
            continue
        session.add(
            JobContact(
                job_id=job.id,
                contact_id=contact.id,
                rank=rank,
                relevance=max(0.0, 1.0 - rank * 0.15),
                rationale=rationale,
            )
        )
        created += 1
    return created


# --- The pipeline -------------------------------------------------------------


def run_search(
    session: Session,
    user: User,
    query: JobQuery,
    *,
    search: JobSearch | None = None,
    find_contacts: bool = True,
    only_providers: list[str] | None = None,
) -> SearchOutcome:
    """Execute ``query`` end to end and persist everything it produced."""
    if search is None:
        search = persist_search(session, user, query)
    run = SearchRun(search_id=search.id, status=SearchRunStatus.RUNNING)
    session.add(run)
    session.flush()

    outcome = SearchOutcome(
        search_id=search.id, run_id=run.id, status=SearchRunStatus.RUNNING, query=query
    )

    with SafeHTTPClient(max_pages=settings.crawler_max_pages_per_run) as client:
        candidates = _collect(client, query, outcome, session, only_providers)
        kept = _dedupe_and_score(candidates, query, outcome)
        validated = _validate(client, kept, outcome)
        jobs = _persist_jobs(session, user, search, validated, outcome)
        if find_contacts and jobs:
            _attach_contacts(client, session, jobs, query, outcome)

    _finish(session, run, outcome)
    search.last_run_at = utcnow()
    session.flush()
    return outcome


def persist_search(session: Session, user: User, query: JobQuery) -> JobSearch:
    search = JobSearch(
        user_id=user.id,
        raw_query=query.raw,
        label=query.describe()[:300],
        titles=query.titles,
        locations=query.locations,
        companies=query.companies,
        industries=query.industries,
        keywords=query.keywords,
        exclusions=query.exclusions,
        min_years=query.min_years,
        max_years=query.max_years,
        experience_level=query.experience_level.value if query.experience_level else None,
        remote_only=query.remote_only,
        parse_method=query.parse_method,
        parse_notes=query.parse_notes,
    )
    session.add(search)
    session.flush()
    return search


def _collect(
    client: SafeHTTPClient,
    query: JobQuery,
    outcome: SearchOutcome,
    session: Session,
    only_providers: list[str] | None,
) -> list[Candidate]:
    providers = build_providers(
        client, only=only_providers, known_boards=known_boards_for(session)
    )
    if not providers:
        outcome.errors.append("No job sources are enabled. Turn one on under Sources.")
        return []

    candidates: list[Candidate] = []
    for provider in providers:
        result = provider.run(query)
        if result.skipped_reason:
            outcome.providers_skipped[provider.label] = result.skipped_reason
            continue
        outcome.providers_queried.append(provider.label)
        outcome.errors.extend(result.errors)
        outcome.notes.extend(result.notes)
        for raw in result.jobs:
            candidates.append(
                Candidate(
                    raw=raw,
                    canonical_url=canonicalize_url(raw.url),
                    fingerprint=duplicate_key(
                        company_name=raw.company_name, title=raw.title, location=raw.location
                    ),
                )
            )

    outcome.raw_found = len(candidates)
    if not candidates and not outcome.errors:
        usable = [s.label for s in provider_statuses() if s.usable]
        outcome.notes.append(
            "No postings came back from " + (", ".join(usable) or "any source") + "."
        )
    return candidates


def _dedupe_and_score(
    candidates: list[Candidate], query: JobQuery, outcome: SearchOutcome
) -> list[Candidate]:
    # A posting from the employer's own board beats the same role from an
    # aggregator: it is the authoritative copy and links to the real apply flow.
    ordered = sorted(candidates, key=lambda c: 0 if c.raw.source.is_first_party else 1)

    by_url: dict[str, Candidate] = {}
    by_fingerprint: dict[str, Candidate] = {}
    unique: list[Candidate] = []
    for candidate in ordered:
        if candidate.canonical_url in by_url or candidate.fingerprint in by_fingerprint:
            outcome.duplicates_dropped += 1
            continue
        by_url[candidate.canonical_url] = candidate
        by_fingerprint[candidate.fingerprint] = candidate
        unique.append(candidate)

    scored: list[Candidate] = []
    for candidate in unique:
        candidate.relevance = score_job(
            query,
            title=candidate.raw.title,
            company_name=candidate.raw.company_name,
            location=candidate.raw.location,
            description=candidate.raw.description,
            is_remote=candidate.raw.is_remote,
        )
        if candidate.relevance.is_match:
            scored.append(candidate)
        else:
            outcome.irrelevant_dropped += 1

    scored.sort(key=lambda c: c.relevance.score, reverse=True)
    limit = min(query.limit, settings.validation_max_jobs)
    if len(scored) > limit:
        outcome.notes.append(
            f"{len(scored) - limit} further matching posting(s) were left unvalidated to stay "
            f"within this search's request budget. Raise the result limit to see them."
        )
    return scored[:limit]


def _validate(
    client: SafeHTTPClient, candidates: list[Candidate], outcome: SearchOutcome
) -> list[Candidate]:
    kept: list[Candidate] = []
    seen_final_urls: set[str] = set()

    for candidate in candidates:
        candidate.validation = validate_posting(
            client,
            url=candidate.raw.url,
            title=candidate.raw.title,
            company_name=candidate.raw.company_name,
            source=candidate.raw.source,
            board_token=candidate.board_token,
        )
        status = candidate.validation.status

        # A redirect can reveal that two different-looking links are one job.
        final = canonicalize_url(candidate.validation.final_url or candidate.canonical_url)
        if final in seen_final_urls:
            outcome.duplicates_dropped += 1
            continue
        seen_final_urls.add(final)

        if not status.is_displayable:
            outcome.rejected += 1
            continue
        if status is ValidationStatus.UNVERIFIED:
            outcome.unverified += 1
        else:
            outcome.validated += 1
        kept.append(candidate)
    return kept


def _persist_jobs(
    session: Session,
    user: User,
    search: JobSearch,
    candidates: list[Candidate],
    outcome: SearchOutcome,
) -> list[Job]:
    jobs: list[Job] = []
    for candidate in candidates:
        raw = candidate.raw
        domain = domain_from_url(candidate.validation.final_url or raw.url)
        company = get_or_create_company(session, raw.company_name, domain=domain)
        if raw.source.is_first_party and raw.source_query_url and not company.careers_url:
            company.careers_url = _public_board_url(raw)

        existing = session.scalar(
            select(Job).where(
                Job.user_id == user.id, Job.canonical_url == candidate.canonical_url
            )
        )
        job = existing or Job(
            user_id=user.id,
            company_id=company.id,
            canonical_url=candidate.canonical_url,
            job_url=raw.url,
            title=raw.title,
            company_name=raw.company_name,
            source=raw.source,
            fingerprint=candidate.fingerprint,
            normalized_title=normalize_title(raw.title),
        )
        job.search_id = search.id
        job.company_id = company.id
        job.title = raw.title
        job.normalized_title = normalize_title(raw.title)
        job.company_name = raw.company_name
        job.location = raw.location
        job.normalized_location = normalize_location(raw.location) or None
        job.is_remote = raw.is_remote
        job.employment_type = raw.employment_type
        job.department = raw.department
        job.salary_text = raw.salary_text
        job.description = raw.description
        job.summary = truncate(raw.description, SUMMARY_LENGTH) if raw.description else None
        job.apply_url = raw.apply_url
        job.source_query_url = raw.source_query_url
        job.external_id = raw.external_id
        job.fingerprint = candidate.fingerprint
        job.posted_at = raw.posted_at
        job.last_seen_at = utcnow()
        job.payload = raw.payload

        experience = candidate.relevance.experience
        job.experience_text = experience.text
        job.min_years = experience.min_years
        job.max_years = experience.max_years

        job.validation_status = candidate.validation.status
        job.validation_checks = candidate.validation.checks
        job.validation_reason = candidate.validation.reason
        job.validated_at = utcnow()
        job.http_status = candidate.validation.http_status
        job.final_url = candidate.validation.final_url

        job.relevance_score = candidate.relevance.score
        job.relevance_breakdown = candidate.relevance.breakdown
        job.match_reasons = candidate.relevance.reasons

        if existing is None:
            session.add(job)
        session.flush()
        jobs.append(job)
        outcome.job_ids.append(job.id)
    return jobs


def _public_board_url(raw: RawJob) -> str | None:
    platform = raw.payload.get("platform")
    token = raw.payload.get("board")
    if platform in PLATFORMS and token:
        return PLATFORMS[platform]["public"].format(token=token)
    return None


def _attach_contacts(
    client: SafeHTTPClient,
    session: Session,
    jobs: list[Job],
    query: JobQuery,
    outcome: SearchOutcome,
) -> None:
    """Find contacts once per company, then link them to that company's jobs."""
    by_company: dict[int, list[Job]] = {}
    for job in jobs:
        by_company.setdefault(job.company_id, []).append(job)

    budget = settings.contacts_max_companies
    cache_cutoff = utcnow().timestamp() - settings.contacts_cache_hours * 3600
    role_hint = _role_hint(query)

    for index, (company_id, company_jobs) in enumerate(by_company.items()):
        company = session.get(Company, company_id)
        if company is None:
            continue

        cached = session.scalars(
            select(Contact).where(Contact.company_id == company.id, Contact.is_archived.is_(False))
        ).all()
        recently_checked = (
            company.contacts_checked_at is not None
            and company.contacts_checked_at.timestamp() > cache_cutoff
        )
        if cached and recently_checked:
            contacts = list(cached)
        elif index >= budget:
            outcome.notes.append(
                f"Contact discovery stopped after {budget} companies for this search; "
                f"{company.name} was not checked."
            )
            contacts = list(cached)
        else:
            sample = company_jobs[0]
            result = discover_contacts(
                client,
                company_name=company.name,
                company_domain=company.domain,
                careers_url=company.careers_url,
                posting_url=sample.final_url or sample.job_url,
                posting_description=sample.description,
                role_hint=role_hint,
            )
            outcome.notes.extend(result.notes)
            company.contacts_checked_at = utcnow()
            contacts = [persist_contact(session, company, c) for c in result.contacts]
            session.flush()

        ordered = sorted(
            contacts,
            key=lambda c: (c.role.rank, 0 if c.email else 1, 0 if c.name else 1),
        )
        outcome.contacts_found += sum(1 for c in ordered if c.role.is_person and c.name)
        for job in company_jobs:
            link_contacts(
                session,
                job,
                [(contact, _contact_rationale(contact)) for contact in ordered[:6]],
            )
    session.flush()


def _role_hint(query: JobQuery) -> str:
    """Search terms for the directory link, biased to the role being sought."""
    title = query.primary_title.strip()
    return f"recruiter talent acquisition {title}".strip()


def _contact_rationale(contact: Contact) -> str:
    if contact.role is ContactRole.SEARCH_LINK:
        return "A search to run yourself — no specific person is being claimed here."
    if contact.role is ContactRole.TALENT_ALIAS:
        return f"Team address published by {contact.company_name}."
    where = contact.source_url or contact.source.label
    return f"{contact.role.label} — found on {where}."


def _finish(session: Session, run: SearchRun, outcome: SearchOutcome) -> None:
    run.finished_at = utcnow()
    run.providers_queried = outcome.providers_queried
    run.providers_skipped = outcome.providers_skipped
    run.raw_found = outcome.raw_found
    run.duplicates_dropped = outcome.duplicates_dropped
    run.irrelevant_dropped = outcome.irrelevant_dropped
    run.validated = outcome.validated
    run.unverified = outcome.unverified
    run.rejected = outcome.rejected
    run.contacts_found = outcome.contacts_found
    run.errors = outcome.errors[:50]
    run.notes = outcome.notes[:80]

    if outcome.errors and not outcome.job_ids:
        run.status = SearchRunStatus.FAILED
    elif outcome.errors or outcome.providers_skipped:
        run.status = SearchRunStatus.PARTIAL
    else:
        run.status = SearchRunStatus.SUCCESS
    outcome.status = run.status
    session.flush()
    log.info(
        "search.finished",
        run_id=run.id,
        status=run.status.value,
        raw=outcome.raw_found,
        kept=len(outcome.job_ids),
    )
