"""Finding people at a company who could reasonably be contacted about a role.

Sources, in the order they are trusted:

1. The job posting itself — a named contact or a published application address.
2. The company's careers / team / about pages — people the company chose to
   publish, with their titles.
3. A published team inbox (``careers@``, ``talent@``) — an address, not a person.
4. A directory search link — a LinkedIn people-search URL the user runs in
   their own session. It names nobody and asserts nothing.

What this module will not do, at all:

* guess an address from a name and a domain;
* read anything behind a login, LinkedIn member data included;
* collect personal mailbox addresses (gmail, outlook, ...) even when published;
* reconstruct a deliberately obfuscated address.

Every contact carries the URL it was read from. A record with no citable source
cannot be produced here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.collectors.http_client import FetchBlocked, SafeHTTPClient
from app.config import settings
from app.logging_config import get_logger
from app.models.enums import ContactRole, EmailStatus, SourceType
from app.providers.aggregators import linkedin_people_search_url
from app.utils.html import (
    absolute_url,
    extract_emails,
    extract_jsonld,
    extract_mailto_links,
    is_personal_email_domain,
    is_role_address,
    is_talent_address,
    make_soup,
)
from app.utils.text import basic_normalize, normalize_company_name

log = get_logger(__name__)

MAX_NAME_LENGTH = 120

# Titles that mean "this person is involved in hiring", mapped to why.
ROLE_PATTERNS: tuple[tuple[re.Pattern[str], ContactRole], ...] = (
    (
        re.compile(
            r"\b(technical recruiter|tech recruiter|product recruiter|engineering recruiter|"
            r"campus recruiter|university recruiter|gtm recruiter|design recruiter)\b",
            re.I,
        ),
        ContactRole.FUNCTION_RECRUITER,
    ),
    (
        re.compile(
            r"\b(talent acquisition|talent partner|talent lead|talent manager|recruiting"
            r"(?: manager| lead| partner)?|recruitment(?: manager| lead| partner)?|recruiter|"
            r"sourcer|head of talent|head of people|people operations|people partner|"
            r"hr business partner|hrbp)\b",
            re.I,
        ),
        ContactRole.TALENT_ACQUISITION,
    ),
    (
        re.compile(r"\b(hiring manager|engineering manager|head of engineering)\b", re.I),
        ContactRole.HIRING_MANAGER,
    ),
    (
        re.compile(
            r"\b(vp of |vice president|director of|head of|chief |group product manager|"
            r"team lead|tech lead|principal)\b",
            re.I,
        ),
        ContactRole.TEAM_LEAD,
    ),
)

# A person's name: two to four capitalised words.
NAME_RE = re.compile(r"^[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){1,3}$")

# Paths worth trying on a company's own domain, most likely to list people first.
CAREER_PATHS: tuple[str, ...] = (
    "/careers",
    "/jobs",
    "/about/team",
    "/team",
    "/about",
    "/company/team",
    "/people",
    "/contact",
)

# Terms in a link's text that suggest it leads to a page listing people.
PEOPLE_LINK_TERMS = re.compile(
    r"\b(team|people|about|leadership|our\s+people|meet\s+the|careers?|contact)\b", re.I
)


@dataclass
class ContactCandidate:
    """One discovered contact, before it is written to the database."""

    role: ContactRole
    company_name: str
    source: SourceType
    name: str | None = None
    title: str | None = None
    email: str | None = None
    email_status: EmailStatus = EmailStatus.NONE
    profile_url: str | None = None
    search_url: str | None = None
    source_url: str | None = None
    source_excerpt: str | None = None
    confidence: float = 0.5
    rationale: str | None = None

    @property
    def dedupe_key(self) -> str:
        """Identity within one company.

        Name comes first deliberately. The same person is often found twice —
        once in a page's structured data with an address, once in its visible
        text without one — and keying on the address would list them twice.
        Only when there is no name (a team inbox) does the address identify the
        record.
        """
        if self.name:
            return f"name:{basic_normalize(self.name)}"
        if self.email:
            return f"email:{self.email.lower()}"
        if self.search_url:
            return f"search:{self.role.value}"
        return f"other:{basic_normalize(self.title or self.role.value)}"


@dataclass
class ContactDiscoveryResult:
    company_name: str
    contacts: list[ContactCandidate] = field(default_factory=list)
    pages_checked: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def classify_title(title: str | None) -> ContactRole | None:
    """Which hiring role a job title corresponds to, if any."""
    if not title:
        return None
    for pattern, role in ROLE_PATTERNS:
        if pattern.search(title):
            return role
    return None


def _clean_email(value: str | None, *, company_domain: str | None) -> str | None:
    """An address we are willing to record, or ``None``."""
    if not value:
        return None
    email = value.strip().lower().removeprefix("mailto:").split("?", 1)[0].strip()
    if not email or "@" not in email or len(email) > 320:
        return None
    if is_personal_email_domain(email):
        return None
    domain = email.rsplit("@", 1)[-1]
    if company_domain and domain != company_domain and not domain.endswith(f".{company_domain}"):
        # An address on someone else's domain is not this company's contact.
        return None
    return email


# --- Source 1: the posting itself --------------------------------------------


def contacts_from_posting(
    *, company_name: str, description: str | None, posting_url: str, company_domain: str | None
) -> list[ContactCandidate]:
    """A contact the posting itself names or publishes an address for."""
    out: list[ContactCandidate] = []
    if not description:
        return out

    for raw in extract_emails(description)[:5]:
        email = _clean_email(raw, company_domain=company_domain)
        if not email:
            continue
        is_alias = is_role_address(email) or is_talent_address(email)
        out.append(
            ContactCandidate(
                role=ContactRole.TALENT_ALIAS if is_alias else ContactRole.TALENT_ACQUISITION,
                company_name=company_name,
                source=SourceType.JOB_POSTING_CONTACT,
                name=None if is_alias else None,
                title="Contact address published on the job posting",
                email=email,
                email_status=EmailStatus.PUBLISHED_TEAM_ALIAS,
                source_url=posting_url,
                source_excerpt=_excerpt_around(description, email),
                confidence=0.75,
                rationale="Published on the job posting itself.",
            )
        )
    return out


def _excerpt_around(text: str, needle: str, width: int = 160) -> str:
    index = text.lower().find(needle.lower())
    if index == -1:
        return text[:width]
    start = max(0, index - width // 2)
    return " ".join(text[start : start + width].split())


# --- Source 2 and 3: the company's own pages ---------------------------------


def _from_jsonld(html: str, page_url: str, company_name: str, company_domain: str | None) -> list[ContactCandidate]:
    out: list[ContactCandidate] = []
    for block in extract_jsonld(html):
        raw_type = block.get("@type") or block.get("type") or ""
        types = (
            [str(t).lower() for t in raw_type]
            if isinstance(raw_type, list)
            else [str(raw_type).lower()]
        )
        if "person" not in types:
            continue
        title = str(block.get("jobTitle") or "").strip()
        role = classify_title(title)
        if role is None:
            continue
        name = " ".join(str(block.get("name") or "").split())
        if not name or len(name) > MAX_NAME_LENGTH:
            continue
        email = _clean_email(block.get("email"), company_domain=company_domain)
        out.append(
            ContactCandidate(
                role=role,
                company_name=company_name,
                source=SourceType.COMPANY_TEAM_PAGE,
                name=name,
                title=title or None,
                email=email,
                email_status=EmailStatus.PUBLISHED_ATTRIBUTED if email else EmailStatus.NONE,
                profile_url=str(block.get("url") or "") or None,
                source_url=page_url,
                source_excerpt=f"Published structured data: {name} — {title}",
                confidence=0.9,
                rationale="Named in the page's own structured data.",
            )
        )
    return out


def _from_mailto(html: str, page_url: str, company_name: str, company_domain: str | None) -> list[ContactCandidate]:
    out: list[ContactCandidate] = []
    for raw_email, anchor_text in extract_mailto_links(html, base_url=page_url):
        email = _clean_email(raw_email, company_domain=company_domain)
        if not email:
            continue
        anchor = (anchor_text or "").strip()
        if is_role_address(email) or is_talent_address(email):
            if not is_talent_address(email) and not classify_title(anchor):
                continue
            out.append(
                ContactCandidate(
                    role=ContactRole.TALENT_ALIAS,
                    company_name=company_name,
                    source=SourceType.COMPANY_CAREERS_PAGE,
                    name=None,
                    title="Shared talent inbox",
                    email=email,
                    email_status=EmailStatus.PUBLISHED_TEAM_ALIAS,
                    source_url=page_url,
                    source_excerpt=f"Published as a mailto link on {page_url}",
                    confidence=0.7,
                    rationale="A team address the company publishes for exactly this purpose.",
                )
            )
            continue
        if NAME_RE.match(anchor) and len(anchor) <= MAX_NAME_LENGTH:
            out.append(
                ContactCandidate(
                    role=ContactRole.TALENT_ACQUISITION,
                    company_name=company_name,
                    source=SourceType.COMPANY_TEAM_PAGE,
                    name=anchor,
                    title="Recruiting contact",
                    email=email,
                    email_status=EmailStatus.PUBLISHED_ATTRIBUTED,
                    source_url=page_url,
                    source_excerpt=f"Published link attributing {email} to {anchor}",
                    confidence=0.85,
                    rationale="The company published this address against this person's name.",
                )
            )
    return out


def _from_visible_text(html: str, page_url: str, company_name: str, company_domain: str | None) -> list[ContactCandidate]:
    """People listed on the page with a hiring-related title."""
    soup = make_soup(html)
    out: list[ContactCandidate] = []
    seen: set[str] = set()

    for element in soup.find_all(string=lambda s: bool(classify_title(str(s)))):
        title_text = " ".join(str(element).split())[:200]
        role = classify_title(title_text)
        if role is None:
            continue

        container = element.parent
        for _ in range(3):
            if container is None:
                break
            if len(container.get_text(" ", strip=True)) > 30:
                break
            container = container.parent
        if container is None:
            continue

        block_text = container.get_text("\n", strip=True)
        name: str | None = None
        for line in block_text.split("\n"):
            candidate = line.strip()
            if not candidate or classify_title(candidate):
                continue
            if NAME_RE.match(candidate) and len(candidate) <= MAX_NAME_LENGTH:
                name = candidate
                break
        if not name or basic_normalize(name) in seen:
            continue
        seen.add(basic_normalize(name))

        addresses = list(extract_emails(block_text))
        for anchor in container.find_all("a", href=True):
            href = str(anchor["href"]).strip()
            if href.lower().startswith("mailto:"):
                addresses.append(href[7:])
        email = next(
            (
                cleaned
                for cleaned in (
                    _clean_email(a, company_domain=company_domain) for a in dict.fromkeys(addresses)
                )
                if cleaned
            ),
            None,
        )

        profile = None
        for anchor in container.find_all("a", href=True):
            href = absolute_url(page_url, str(anchor["href"]))
            if "linkedin.com/in/" in href.lower():
                profile = href
                break

        status = EmailStatus.NONE
        if email:
            status = (
                EmailStatus.PUBLISHED_TEAM_ALIAS
                if is_role_address(email)
                else EmailStatus.PUBLISHED_ATTRIBUTED
            )
        out.append(
            ContactCandidate(
                role=role,
                company_name=company_name,
                source=SourceType.COMPANY_TEAM_PAGE,
                name=name,
                title=title_text,
                email=email,
                email_status=status,
                profile_url=profile,
                source_url=page_url,
                source_excerpt=block_text[:300],
                confidence=0.8 if email else 0.6,
                rationale=f"Listed on {urlparse(page_url).netloc} as {title_text}.",
            )
        )
    return out


def candidate_pages(*, company_domain: str | None, careers_url: str | None) -> list[str]:
    """Pages worth checking for published contacts, best first."""
    pages: list[str] = []
    if careers_url:
        pages.append(careers_url)
    if company_domain:
        root = f"https://{company_domain}"
        pages.extend(f"{root}{path}" for path in CAREER_PATHS)
    seen: set[str] = set()
    ordered: list[str] = []
    for page in pages:
        key = page.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            ordered.append(page)
    return ordered


def discover_contacts(
    client: SafeHTTPClient,
    *,
    company_name: str,
    company_domain: str | None,
    careers_url: str | None = None,
    posting_url: str | None = None,
    posting_description: str | None = None,
    role_hint: str = "recruiter talent acquisition",
    max_pages: int | None = None,
) -> ContactDiscoveryResult:
    """Find contacts for one company. Never raises."""
    result = ContactDiscoveryResult(company_name=company_name)
    budget = max_pages if max_pages is not None else settings.contacts_max_pages_per_company

    if posting_url:
        result.contacts.extend(
            contacts_from_posting(
                company_name=company_name,
                description=posting_description,
                posting_url=posting_url,
                company_domain=company_domain,
            )
        )

    pages = candidate_pages(company_domain=company_domain, careers_url=careers_url)
    if not pages:
        result.notes.append(
            f"No confirmed website for {company_name}, so there were no pages to check."
        )
    checked = 0
    for url in pages:
        if checked >= budget or client.budget_exhausted:
            break
        try:
            response = client.fetch(url)
        except FetchBlocked as exc:
            result.errors.append(f"{url}: {exc.reason}")
            continue
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{url}: {type(exc).__name__}")
            continue
        checked += 1
        if not response.ok:
            continue
        result.pages_checked.append(response.final_url)
        found = (
            _from_jsonld(response.text, response.final_url, company_name, company_domain)
            + _from_mailto(response.text, response.final_url, company_name, company_domain)
            + _from_visible_text(response.text, response.final_url, company_name, company_domain)
        )
        result.contacts.extend(found)
        if found:
            result.notes.append(
                f"{len(found)} published contact(s) on {urlparse(response.final_url).path or '/'}"
            )

    # A directory search link is always offered. It names nobody, so it is
    # never a substitute for a found person — it is a starting point for the
    # user's own search when the company publishes no one.
    result.contacts.append(
        ContactCandidate(
            role=ContactRole.SEARCH_LINK,
            company_name=company_name,
            source=SourceType.DIRECTORY_SEARCH_LINK,
            name=None,
            title=f"Search LinkedIn for recruiters at {company_name}",
            search_url=linkedin_people_search_url(company_name, role_hint),
            source_url=None,
            confidence=0.2,
            rationale=(
                "A search you run in your own LinkedIn session. This is a link, not a "
                "person — nothing here claims a specific recruiter exists."
            ),
        )
    )

    people = [c for c in result.contacts if c.role.is_person and c.name]
    if not people:
        result.notes.append(
            f"{company_name} does not publish named recruiter contacts on the pages checked. "
            "That is normal, and this tool will not guess an address to fill the gap."
        )
    result.contacts = rank_contacts(dedupe_contacts(result.contacts))
    return result


def dedupe_contacts(contacts: list[ContactCandidate]) -> list[ContactCandidate]:
    """Collapse duplicates, keeping the best-evidenced version of each."""
    best: dict[str, ContactCandidate] = {}
    for contact in contacts:
        key = contact.dedupe_key
        current = best.get(key)
        if current is None:
            best[key] = contact
            continue
        # Prefer the record with an address, then the more specific role, then
        # the higher confidence.
        better = (
            (bool(contact.email), -contact.role.rank, contact.confidence)
            > (bool(current.email), -current.role.rank, current.confidence)
        )
        if better:
            best[key] = contact
    return sorted(best.values(), key=lambda c: (c.role.rank, -c.confidence))


def rank_contacts(contacts: list[ContactCandidate]) -> list[ContactCandidate]:
    """Order contacts by how useful they are to approach about a role."""
    return sorted(
        contacts,
        key=lambda c: (
            c.role.rank,
            0 if c.email else 1,
            0 if c.name else 1,
            -c.confidence,
        ),
    )


def domain_from_url(url: str | None) -> str | None:
    """The registrable host of a URL, unless it is a job board's own domain."""
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    host = host.removeprefix("www.")
    # An ATS or aggregator host is not the employer's own domain.
    third_party = (
        "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "myworkdayjobs.com",
        "smartrecruiters.com", "bamboohr.com", "jobvite.com", "recruitee.com",
        "adzuna.", "themuse.com", "remotive.com", "arbeitnow.com", "jobicy.com",
        "usajobs.gov", "linkedin.com", "indeed.com", "glassdoor.",
    )
    if any(marker in host for marker in third_party):
        return None
    return host


def company_matches(a: str, b: str) -> bool:
    return normalize_company_name(a) == normalize_company_name(b)
