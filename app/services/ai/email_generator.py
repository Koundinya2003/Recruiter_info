"""AI-assisted outreach email drafting.

The generator is given a tightly-scoped context object built from the database:
the company, the job, the recruiter, and the user's own profile. The system
prompt forbids inventing anything outside that context — no fabricated
achievements, no imagined mutual connections, no "I've long admired..." filler.

Whatever comes back is a **draft**. It is stored with ``draft_approved=False``
and cannot be recorded as outreach until the user approves it explicitly.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.logging_config import get_logger
from app.models.company import Company
from app.models.job import Job
from app.models.outreach import OutreachLead
from app.models.recruiter import Recruiter
from app.models.user import UserProfile
from app.services.ai.provider import ChatMessage, Completion, LLMError, LLMProvider, get_provider
from app.services.scoring.context import ProfileContext
from app.utils.text import truncate

log = get_logger(__name__)

SYSTEM_PROMPT = """You write short, professional job-outreach emails on behalf of a candidate.

HARD RULES — breaking any of these makes the draft unusable:
1. Use ONLY facts present in the supplied context. If a detail is not in the
   context, leave it out. Never invent experience, employers, metrics,
   achievements, degrees, tools, or dates.
2. Never claim a relationship that is not stated: no prior conversations, no
   mutual connections, no referrals, no "we met at...", no "a friend suggested".
3. Never claim knowledge of the company beyond what the context contains. No
   invented praise of their product, funding, culture or mission.
4. No flattery, no superlatives about the recruiter or company, no hype.
5. No spam patterns: no urgency, no follow-up threats, no mass-mail phrasing,
   no emoji, no exclamation marks, no "Hope this email finds you well".
6. Be concrete about the overlap between the candidate's actual background and
   the actual role. Specific beats enthusiastic.

STYLE:
- 110-170 words in the body. Shorter is better than padded.
- Plain sentences a real person would say out loud.
- One clear, low-pressure ask at the end.
- Address the recruiter by first name if one is given; otherwise "Hello".

OUTPUT FORMAT — exactly this, nothing else:
Subject: <one specific subject line, under 80 characters>

<body>
"""


@dataclass
class EmailContext:
    """Everything — and only what — the model is allowed to draw on."""

    company_name: str
    job_title: str | None = None
    job_url: str | None = None
    job_location: str | None = None
    job_summary: str | None = None
    job_posted: str | None = None
    recruiter_name: str | None = None
    recruiter_first_name: str | None = None
    recruiter_title: str | None = None
    candidate_name: str | None = None
    candidate_headline: str | None = None
    candidate_education: str | None = None
    relevant_experience: str | None = None
    candidate_skills: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    years_experience: float | None = None
    portfolio_url: str | None = None
    github_url: str | None = None
    linkedin_url: str | None = None
    reason_for_reaching_out: str | None = None
    relevance_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", [], {})}


@dataclass
class EmailDraft:
    subject: str
    body: str
    provider: str
    model: str
    is_fallback: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        """Drafts are never approved on creation. Approval is a user action."""
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "body": self.body,
            "provider": self.provider,
            "model": self.model,
            "is_fallback": self.is_fallback,
            "warnings": self.warnings,
            "approved": self.approved,
        }


def _first_name(full_name: str | None) -> str | None:
    if not full_name:
        return None
    token = full_name.strip().split()[0]
    # Team mailboxes ("Acme Talent Team") have no first name to use.
    if token.lower() in {"the", "team", "talent", "recruiting", "careers"}:
        return None
    return token


def build_context(
    company: Company,
    job: Job | None,
    recruiter: Recruiter,
    profile: UserProfile | None,
    matching: ProfileContext | None = None,
    *,
    reason: str | None = None,
) -> EmailContext:
    """Assemble the drafting context from stored records only."""
    matched_skills: list[str] = []
    if matching is not None and job is not None:
        haystack = " ".join(filter(None, [job.title, job.description]))
        matched_skills = [term.term for term, _ in matching.matching_skills(haystack)][:5]

    relevance_reasons: list[str] = []
    if job is not None and job.relevance_breakdown:
        relevance_reasons = list(job.relevance_breakdown.get("reasons", []))[:4]

    return EmailContext(
        company_name=company.company_name,
        job_title=job.title if job else None,
        job_url=job.job_url if job else None,
        job_location=job.location if job else None,
        job_summary=truncate(job.description, 700) if job and job.description else None,
        job_posted=job.posted_at.strftime("%d %b %Y") if job and job.posted_at else None,
        recruiter_name=recruiter.name,
        recruiter_first_name=_first_name(recruiter.name),
        recruiter_title=recruiter.title,
        candidate_name=profile.full_name if profile else None,
        candidate_headline=profile.headline if profile else None,
        candidate_education=truncate(profile.education, 300) if profile else None,
        relevant_experience=truncate(profile.experience, 900) if profile else None,
        candidate_skills=list(profile.skills or [])[:12] if profile else [],
        matched_skills=matched_skills,
        years_experience=profile.years_experience if profile else None,
        portfolio_url=profile.portfolio_url if profile else None,
        github_url=profile.github_url if profile else None,
        linkedin_url=profile.linkedin_url if profile else None,
        reason_for_reaching_out=reason,
        relevance_reasons=relevance_reasons,
    )


def build_messages(context: EmailContext) -> list[ChatMessage]:
    payload = json.dumps(context.to_dict(), indent=2, ensure_ascii=False)
    user_prompt = (
        "Write the outreach email using only the context below.\n\n"
        f"CONTEXT_JSON:\n{payload}"
    )
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_prompt),
    ]


SUBJECT_RE = re.compile(r"^\s*subject\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# Phrases that signal the model drifted into the behaviour we forbade.
RISK_PHRASES: tuple[tuple[str, str], ...] = (
    (r"\bwe (?:met|spoke|connected)\b", "claims a prior interaction"),
    (r"\bmutual (?:friend|connection|contact)\b", "claims a mutual connection"),
    (r"\breferred me\b", "claims a referral"),
    (r"\bas discussed\b", "implies a prior conversation"),
    (r"\bhope this (?:email|message) finds you well\b", "generic filler opener"),
    (r"\bi have long admired\b", "unfounded flattery"),
    (r"\b(?:huge|massive|big) fan\b", "unfounded flattery"),
    (r"!{1,}", "exclamation marks read as salesy"),
)


def parse_completion(text: str, fallback_subject: str) -> tuple[str, str]:
    """Split a completion into ``(subject, body)``."""
    match = SUBJECT_RE.search(text)
    if match:
        subject = match.group(1).strip()
        body = text[match.end() :].strip()
    else:
        subject = fallback_subject
        body = text.strip()
    return subject[:400] or fallback_subject, body


def review_draft(body: str, context: EmailContext) -> list[str]:
    """Flag anything that looks like a rule violation. Advisory, not blocking."""
    warnings: list[str] = []
    for pattern, description in RISK_PHRASES:
        if re.search(pattern, body, flags=re.IGNORECASE):
            warnings.append(f"Review before sending — {description}.")
    word_count = len(body.split())
    if word_count > 260:
        warnings.append(f"Draft is long ({word_count} words); recruiters skim.")
    if word_count < 50:
        warnings.append(f"Draft is very short ({word_count} words); it may read as low-effort.")
    if context.recruiter_first_name and context.recruiter_first_name not in body:
        warnings.append("Draft does not address the recruiter by name.")
    return list(dict.fromkeys(warnings))


def generate_draft(
    company: Company,
    job: Job | None,
    recruiter: Recruiter,
    profile: UserProfile | None,
    matching: ProfileContext | None = None,
    *,
    provider: LLMProvider | None = None,
    reason: str | None = None,
) -> EmailDraft:
    """Generate a draft email. Falls back to the offline drafter on failure."""
    context = build_context(company, job, recruiter, profile, matching, reason=reason)
    messages = build_messages(context)
    provider = provider or get_provider()

    fallback_subject = (
        f"{context.job_title} at {context.company_name}"
        if context.job_title
        else f"Opportunities at {context.company_name}"
    )

    completion: Completion
    try:
        completion = provider.complete(messages)
    except LLMError as exc:
        log.warning("ai.generation_failed", error=str(exc), fallback=True)
        from app.services.ai.provider import TemplateProvider

        completion = TemplateProvider().complete(messages)
        completion.fallback = True

    subject, body = parse_completion(completion.text, fallback_subject)
    warnings = review_draft(body, context)
    if completion.fallback:
        warnings.insert(
            0,
            "Generated by the offline template (no AI provider configured or the "
            "provider failed). Edit it before sending.",
        )

    return EmailDraft(
        subject=subject,
        body=body,
        provider=completion.provider,
        model=completion.model,
        is_fallback=completion.fallback,
        warnings=warnings,
    )


def attach_draft_to_lead(session: Session, lead: OutreachLead, draft: EmailDraft) -> OutreachLead:
    """Store a generated draft on a lead. Approval remains a separate step."""
    lead.draft_subject = draft.subject
    lead.draft_body = draft.body
    lead.draft_provider = draft.provider
    lead.draft_model = draft.model
    lead.draft_generated_at = utcnow()
    lead.draft_approved = False
    lead.draft_approved_at = None
    session.flush()
    return lead
