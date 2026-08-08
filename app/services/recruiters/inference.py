"""Pattern-based email inference — the lowest-confidence path, and opt-in only.

An inferred address is a *guess*. This module therefore:

* only runs when the user explicitly asks for it;
* prefers to derive the pattern from an address the company actually published,
  rather than assuming one;
* labels every result ``EmailConfidence.LOW`` with source ``PATTERN_INFERENCE``;
* never sets ``email_verified``, and never overwrites a published address.

The UI shows these as "INFERRED — not published" everywhere they appear.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.types import NormalizedRecruiter
from app.models.company import Company
from app.models.enums import EmailConfidence, SourceType
from app.models.recruiter import Recruiter
from app.utils.html import is_personal_email_domain, is_role_address
from app.utils.text import basic_normalize, strip_accents

# Ordered by how common they are in practice.
PATTERNS: tuple[str, ...] = (
    "{first}.{last}",
    "{first}",
    "{f}{last}",
    "{first}{last}",
    "{first}_{last}",
    "{first}.{l}",
    "{last}.{first}",
    "{f}.{last}",
)


@dataclass
class InferredEmail:
    email: str
    pattern: str
    basis: str
    confidence: EmailConfidence = EmailConfidence.LOW


def _name_parts(name: str) -> tuple[str, str] | None:
    cleaned = re.sub(r"[^a-zA-Z\s'\-]", " ", strip_accents(name)).strip()
    tokens = [t for t in re.split(r"[\s'\-]+", cleaned) if len(t) > 1]
    if len(tokens) < 2:
        return None
    return tokens[0].lower(), tokens[-1].lower()


def render_pattern(pattern: str, first: str, last: str, domain: str) -> str:
    local = pattern.format(first=first, last=last, f=first[0], l=last[0])
    return f"{local}@{domain}".lower()


def detect_pattern(known_emails: dict[str, str]) -> tuple[str, str] | None:
    """Infer the company's address format from ``{email: person_name}`` pairs.

    Returns ``(pattern, basis_description)`` or None when it cannot be derived.
    """
    votes: Counter[str] = Counter()
    evidence: dict[str, str] = {}
    for email, name in known_emails.items():
        if not email or not name or is_role_address(email):
            continue
        parts = _name_parts(name)
        if parts is None:
            continue
        first, last = parts
        local, _, domain = email.partition("@")
        for pattern in PATTERNS:
            if render_pattern(pattern, first, last, domain).split("@")[0] == local.lower():
                votes[pattern] += 1
                evidence.setdefault(pattern, f"{email} belongs to {name}")
                break
    if not votes:
        return None
    pattern, _ = votes.most_common(1)[0]
    return pattern, evidence[pattern]


def infer_email(
    name: str,
    domain: str,
    *,
    pattern: str | None = None,
    basis: str | None = None,
) -> InferredEmail | None:
    """Build a single low-confidence candidate address."""
    domain = (domain or "").strip().lower().lstrip("@")
    if not domain or is_personal_email_domain(f"x@{domain}"):
        return None
    parts = _name_parts(name)
    if parts is None:
        return None
    first, last = parts
    chosen = pattern or PATTERNS[0]
    return InferredEmail(
        email=render_pattern(chosen, first, last, domain),
        pattern=chosen,
        basis=basis
        or f"Assumed the common '{chosen}' format — no company address was available to learn from",
    )


def company_pattern(session: Session, company: Company) -> tuple[str, str] | None:
    """Learn the company's email format from addresses already discovered."""
    rows = session.execute(
        select(Recruiter.public_professional_email, Recruiter.name).where(
            Recruiter.company_id == company.id,
            Recruiter.public_professional_email.is_not(None),
            Recruiter.email_confidence.in_([EmailConfidence.HIGH, EmailConfidence.MEDIUM]),
        )
    ).all()
    known = {email: name for email, name in rows if email}
    return detect_pattern(known)


def infer_for_recruiter(
    session: Session, company: Company, recruiter: Recruiter
) -> InferredEmail | None:
    """Infer an address for one recruiter who has none. Does not persist."""
    if recruiter.public_professional_email:
        return None
    if not company.company_domain:
        return None
    detected = company_pattern(session, company)
    pattern, basis = detected if detected else (None, None)
    return infer_email(recruiter.name, company.company_domain, pattern=pattern, basis=basis)


def as_candidate(
    recruiter: Recruiter, inferred: InferredEmail, company: Company
) -> NormalizedRecruiter:
    """Wrap an inference as an ingestible candidate record."""
    return NormalizedRecruiter(
        name=recruiter.name,
        normalized_name=basic_normalize(recruiter.name),
        title=recruiter.title,
        email=inferred.email,
        profile_url=recruiter.professional_profile_url,
        company_name=company.company_name,
        source=SourceType.PATTERN_INFERENCE,
        source_url=None,
        confidence=EmailConfidence.LOW,
        is_inferred=True,
        source_excerpt=f"INFERRED, not published. {inferred.basis}",
        payload={"pattern": inferred.pattern},
    )
