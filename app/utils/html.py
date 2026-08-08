"""HTML helpers: text extraction, JSON-LD discovery and email extraction.

Email extraction only ever runs over content the site chose to publish
(``mailto:`` links and visible page text). There is no attempt to reconstruct
obfuscated addresses, and role addresses are distinguished from personal ones.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Addresses that belong to a function rather than a person. Still useful, but
# they can never be HIGH confidence for an individual.
ROLE_ADDRESS_LOCALPARTS = frozenset(
    {
        "careers", "jobs", "hiring", "recruiting", "recruitment", "talent",
        "hr", "people", "work", "join", "apply", "jobapplications", "team",
        "info", "hello", "contact", "support", "help", "admin", "sales",
        "press", "media", "legal", "privacy", "security", "noreply", "no-reply",
        "donotreply", "webmaster", "postmaster", "abuse",
    }
)

TALENT_ADDRESS_LOCALPARTS = frozenset(
    {"careers", "jobs", "hiring", "recruiting", "recruitment", "talent", "hr", "people", "join", "apply"}
)

# Addresses we must never collect, even if they appear publicly.
PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "hotmail.com",
        "outlook.com", "live.com", "aol.com", "icloud.com", "me.com", "mail.com",
        "protonmail.com", "proton.me", "yandex.com", "gmx.com", "zoho.com",
        "rediffmail.com",
    }
)


def html_to_text(html: str | None, *, limit: int = 20000) -> str:
    """Strip markup to readable text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def extract_jsonld(html: str) -> list[dict[str, Any]]:
    """Return every JSON-LD object embedded in the page, flattened."""
    soup = make_soup(html)
    blocks: list[dict[str, Any]] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        blocks.extend(_flatten_jsonld(data))
    return blocks


def _flatten_jsonld(data: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            out.extend(_flatten_jsonld(item))
    elif isinstance(data, dict):
        out.append(data)
        if "@graph" in data:
            out.extend(_flatten_jsonld(data["@graph"]))
    return out


def is_personal_email_domain(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in PERSONAL_EMAIL_DOMAINS


def local_part(email: str) -> str:
    return email.split("@", 1)[0].lower()


def is_role_address(email: str) -> bool:
    part = re.split(r"[.\-_+]", local_part(email))[0]
    return local_part(email) in ROLE_ADDRESS_LOCALPARTS or part in ROLE_ADDRESS_LOCALPARTS


def is_talent_address(email: str) -> bool:
    part = re.split(r"[.\-_+]", local_part(email))[0]
    return local_part(email) in TALENT_ADDRESS_LOCALPARTS or part in TALENT_ADDRESS_LOCALPARTS


def extract_mailto_links(html: str, *, base_url: str | None = None) -> list[tuple[str, str]]:
    """Return ``(email, link_text)`` for every ``mailto:`` anchor in the page."""
    soup = make_soup(html)
    found: list[tuple[str, str]] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href.lower().startswith("mailto:"):
            continue
        address = href[7:].split("?", 1)[0].strip()
        if not EMAIL_RE.fullmatch(address):
            continue
        found.append((address.lower(), anchor.get_text(" ", strip=True)))
    return found


def extract_emails(text: str | None) -> list[str]:
    """All syntactically valid addresses in visible text, de-duplicated."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in EMAIL_RE.findall(text):
        address = match.lower().rstrip(".")
        if address in seen:
            continue
        seen.add(address)
        out.append(address)
    return out


def absolute_url(base: str, href: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base, href)
