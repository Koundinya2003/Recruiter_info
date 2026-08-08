"""Public recruiter-source collector.

Reads *publicly published* talent contacts from pages a company puts up for
exactly this purpose: careers pages, "meet the team" pages, and job postings
that name a hiring contact.

Explicitly out of scope, permanently:

* anything behind a login, including LinkedIn member data;
* private profiles, private messages, or member-only directories;
* personal mailbox addresses (gmail/outlook/...), even if published;
* obfuscated-address reconstruction.

Confidence is assigned by provenance:

* ``HIGH``   — address published on the page *and* attributed to a named person.
* ``MEDIUM`` — company talent address (careers@, talent@) not tied to a person.
* ``LOW``    — pattern-inferred; produced elsewhere and never by this collector.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.collectors.base import BaseCollector
from app.collectors.http_client import FetchBlocked
from app.collectors.types import CollectorError, NormalizedRecruiter, RawRecruiter
from app.logging_config import get_logger
from app.models.enums import EmailConfidence, SourceType
from app.utils.html import (
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

TALENT_TITLE_RE = re.compile(
    r"\b(talent acquisition|talent partner|technical recruiter|recruiter|recruiting|"
    r"recruitment|sourcer|people operations|people partner|head of people|hr business partner|"
    r"talent lead|hiring manager)\b",
    re.IGNORECASE,
)

# A person's name near a talent title on a team page.
NAME_RE = re.compile(r"^[A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+){1,3}$")

MAX_NAME_LENGTH = 120


class PublicRecruiterSourceCollector(BaseCollector[RawRecruiter, NormalizedRecruiter]):
    """Collects publicly listed talent contacts for one company."""

    name = "public_recruiter_sources"
    source_type = SourceType.COMPANY_TEAM_PAGE
    record_type = "recruiter"

    def __init__(
        self,
        *,
        company_name: str,
        urls: list[str],
        company_domain: str | None = None,
        max_pages: int = 4,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.company_name = company_name
        self.company_domain = (company_domain or "").lower().lstrip("@")
        self.urls = [u for u in urls if u]
        self.max_pages = max_pages

    # -- extraction ----------------------------------------------------------
    def _from_jsonld(self, html: str, page_url: str) -> list[RawRecruiter]:
        out: list[RawRecruiter] = []
        for block in extract_jsonld(html):
            type_field = block.get("@type") or block.get("type") or ""
            types = (
                [str(t).lower() for t in type_field]
                if isinstance(type_field, list)
                else [str(type_field).lower()]
            )
            if "person" not in types:
                continue
            title = str(block.get("jobTitle") or "")
            if not TALENT_TITLE_RE.search(title):
                continue
            name = str(block.get("name") or "").strip()
            if not name:
                continue
            email = block.get("email")
            email = str(email).replace("mailto:", "").strip().lower() if email else None
            if email and is_personal_email_domain(email):
                email = None
            out.append(
                RawRecruiter(
                    name=name[:MAX_NAME_LENGTH],
                    title=title or None,
                    email=email,
                    profile_url=str(block.get("url") or "") or None,
                    company_name=self.company_name,
                    source=SourceType.COMPANY_TEAM_PAGE,
                    source_url=page_url,
                    confidence=EmailConfidence.HIGH if email else EmailConfidence.NONE,
                    source_excerpt=f"Published structured data: {name} — {title}",
                    payload={"structured_data": True},
                )
            )
        return out

    def _from_mailto(self, html: str, page_url: str) -> list[RawRecruiter]:
        out: list[RawRecruiter] = []
        for email, anchor_text in extract_mailto_links(html, base_url=page_url):
            if is_personal_email_domain(email):
                continue
            if not is_talent_address(email) and not TALENT_TITLE_RE.search(anchor_text or ""):
                continue
            if is_role_address(email):
                name = f"{self.company_name} Talent Team"
                title = "Talent / Recruiting (shared team address)"
                confidence = EmailConfidence.MEDIUM
                excerpt = f"Published on {page_url} as a link: {email}"
            else:
                name = anchor_text.strip() if anchor_text and NAME_RE.match(anchor_text.strip()) else ""
                if not name:
                    continue
                title = "Recruiting contact"
                confidence = EmailConfidence.HIGH
                excerpt = f"Published link attributing {email} to {name}"
            out.append(
                RawRecruiter(
                    name=name[:MAX_NAME_LENGTH],
                    title=title,
                    email=email,
                    company_name=self.company_name,
                    source=SourceType.COMPANY_TEAM_PAGE,
                    source_url=page_url,
                    confidence=confidence,
                    source_excerpt=excerpt,
                    payload={"anchor_text": anchor_text},
                )
            )
        return out

    def _from_visible_text(self, html: str, page_url: str) -> list[RawRecruiter]:
        """Named people listed with a talent title in the page body."""
        soup = make_soup(html)
        out: list[RawRecruiter] = []
        for element in soup.find_all(string=TALENT_TITLE_RE):
            title_text = " ".join(str(element).split())[:200]
            container = element.parent
            for _ in range(3):
                if container is None:
                    break
                block_text = container.get_text(" ", strip=True)
                if len(block_text) > 30:
                    break
                container = container.parent
            if container is None:
                continue
            block_text = container.get_text("\n", strip=True)

            candidate_name = None
            for line in block_text.split("\n"):
                line = line.strip()
                if TALENT_TITLE_RE.search(line):
                    continue
                if NAME_RE.match(line) and len(line) <= MAX_NAME_LENGTH:
                    candidate_name = line
                    break
            if not candidate_name:
                continue

            # Addresses in the visible text *and* in any mailto: link inside
            # the same block — a published link next to a person's name is an
            # attribution, and reading it is exactly what the link is for.
            candidates = extract_emails(block_text)
            for anchor in container.find_all("a", href=True):
                href = str(anchor["href"]).strip()
                if href.lower().startswith("mailto:"):
                    candidates.append(href[7:].split("?", 1)[0].strip().lower())

            emails = [
                e
                for e in dict.fromkeys(candidates)
                if e
                and not is_personal_email_domain(e)
                and (not self.company_domain or e.endswith(f"@{self.company_domain}"))
            ]
            email = emails[0] if emails else None
            confidence = EmailConfidence.NONE
            if email:
                confidence = (
                    EmailConfidence.MEDIUM if is_role_address(email) else EmailConfidence.HIGH
                )
            out.append(
                RawRecruiter(
                    name=candidate_name,
                    title=title_text,
                    email=email,
                    company_name=self.company_name,
                    source=SourceType.COMPANY_TEAM_PAGE,
                    source_url=page_url,
                    confidence=confidence,
                    source_excerpt=block_text[:300],
                    payload={"structured_data": False},
                )
            )
        return out

    # -- pipeline ------------------------------------------------------------
    def collect(self) -> list[RawRecruiter]:
        found: list[RawRecruiter] = []
        for url in self.urls[: self.max_pages]:
            if self.client.budget_exhausted:
                self.outcome.notes.append("Stopped early: crawl page budget exhausted")
                break
            try:
                response = self.client.fetch(url)
            except FetchBlocked as exc:
                self.outcome.errors.append(
                    CollectorError(stage="collect", message=str(exc), url=url)
                )
                if self.outcome.blocked_reason is None:
                    self.outcome.blocked_reason = exc.reason
                continue
            except Exception as exc:  # noqa: BLE001
                self.outcome.errors.append(
                    CollectorError(
                        stage="collect", message=f"{type(exc).__name__}: {exc}", url=url
                    )
                )
                continue

            page_url = response.final_url
            page_found = (
                self._from_jsonld(response.text, page_url)
                + self._from_mailto(response.text, page_url)
                + self._from_visible_text(response.text, page_url)
            )
            found.extend(page_found)
            self.outcome.notes.append(
                f"{len(page_found)} public talent contact(s) on {urlparse(page_url).path or '/'}"
            )

        if not found:
            self.outcome.notes.append(
                "No publicly published talent contacts found. This is normal — most "
                "companies do not publish recruiter addresses, and we do not guess them "
                "unless you explicitly ask for pattern inference."
            )
        return found

    def normalize(self, raw: RawRecruiter) -> NormalizedRecruiter | None:
        name = " ".join((raw.name or "").split())
        if not name or len(name) > MAX_NAME_LENGTH:
            return None
        email = (raw.email or "").strip().lower() or None
        if email and is_personal_email_domain(email):
            email = None
        confidence = raw.confidence if email else EmailConfidence.NONE
        return NormalizedRecruiter(
            name=name,
            normalized_name=basic_normalize(name),
            title=" ".join((raw.title or "").split()) or None,
            email=email,
            profile_url=raw.profile_url,
            company_name=raw.company_name or self.company_name,
            source=raw.source,
            source_url=raw.source_url,
            confidence=confidence,
            is_inferred=False,
            source_excerpt=raw.source_excerpt,
            payload=raw.payload,
        )

    def validate(self, record: NormalizedRecruiter) -> tuple[bool, str | None]:
        if not record.normalized_name:
            return False, "name normalised to nothing"
        if record.email and is_personal_email_domain(record.email):
            return False, "personal mailbox address — not collected"
        if record.email and self.company_domain:
            domain = record.email.rsplit("@", 1)[-1]
            if domain != self.company_domain and not domain.endswith(f".{self.company_domain}"):
                return False, f"address domain {domain} does not belong to the company"
        if not record.title and not record.email:
            return False, "no title and no contact detail — nothing actionable"
        if record.company_name and normalize_company_name(
            record.company_name
        ) != normalize_company_name(self.company_name):
            return False, "contact belongs to a different company"
        return True, None

    def dedupe_key(self, record: NormalizedRecruiter) -> str:
        return f"{record.normalized_name}|{record.email or ''}"
