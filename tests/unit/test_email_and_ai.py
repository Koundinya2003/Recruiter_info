"""Email verification providers and the AI email generator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.base import utcnow
from app.models.enums import EmailConfidence, SourceType, VerificationStatus
from app.services.ai.email_generator import (
    EmailContext,
    build_messages,
    generate_draft,
    parse_completion,
    review_draft,
)
from app.services.ai.provider import (
    ChatMessage,
    Completion,
    LLMError,
    LLMProvider,
    TemplateProvider,
    get_provider,
)
from app.services.email.verifier import (
    DNSVerifier,
    EmailVerifier,
    HTTPAPIVerifier,
    NullVerifier,
    VerificationResult,
    get_verifier,
    register_provider,
)

# --- Verification providers -----------------------------------------------------


def test_null_verifier_never_checks() -> None:
    result = NullVerifier().verify("anyone@example.com")
    assert result.status is VerificationStatus.NOT_CHECKED
    assert result.confidence == 0.0


@pytest.mark.parametrize(
    "address",
    ["not-an-email", "@example.com", "user@", "user@@example.com", "", "   ", "user name@x.com"],
)
def test_dns_verifier_rejects_malformed_addresses(address: str) -> None:
    result = DNSVerifier().verify(address)
    assert result.status is VerificationStatus.INVALID
    assert result.reason


def test_dns_verifier_rejects_undeliverable_domain() -> None:
    result = DNSVerifier().verify("someone@demo-payments.example")
    assert result.status in {VerificationStatus.INVALID, VerificationStatus.UNKNOWN}
    assert result.confidence == 0.0


def test_dns_verifier_flags_role_addresses_as_risky() -> None:
    """A shared mailbox is deliverable but is not an individual — never `valid`."""
    result = DNSVerifier().verify("careers@google.com")
    assert result.status is VerificationStatus.RISKY
    assert "role address" in result.reason


def test_dns_verifier_accepts_an_individual_at_a_real_domain() -> None:
    result = DNSVerifier().verify("priya.sharma@google.com")
    assert result.status is VerificationStatus.VALID
    assert 0 < result.confidence <= 1.0
    # It must not overclaim: local checks cannot confirm a mailbox.
    assert "cannot confirm" in result.reason


def test_http_verifier_without_configuration_does_not_check() -> None:
    provider = HTTPAPIVerifier(api_url="", api_key="")
    assert provider.available() is False
    assert provider.verify("a@b.com").status is VerificationStatus.NOT_CHECKED


def test_unconfigured_http_provider_falls_back_to_dns() -> None:
    assert get_verifier("http").name == "dns"


def test_unknown_provider_falls_back_safely() -> None:
    assert get_verifier("does-not-exist").name == "dns"


def test_provider_interface_is_swappable() -> None:
    """The whole point of the abstraction: one subclass, one registration."""

    class AlwaysValid(EmailVerifier):
        name = "always_valid"

        def verify(self, email: str) -> VerificationResult:
            return VerificationResult(
                email=email,
                status=VerificationStatus.VALID,
                confidence=0.96,
                provider=self.name,
                reason="stub",
            )

    register_provider("always_valid", AlwaysValid)
    provider = get_verifier("always_valid")
    result = provider.verify("x@y.com")
    assert result.provider == "always_valid"
    assert result.to_dict() == {
        "email": "x@y.com",
        "status": "valid",
        "confidence": 0.96,
        "provider": "always_valid",
        "reason": "stub",
    }


# --- AI provider ----------------------------------------------------------------


def test_offline_provider_is_used_when_no_key_is_configured() -> None:
    provider = get_provider()
    assert provider.name == "offline_template"


def test_template_provider_only_uses_supplied_facts() -> None:
    context = EmailContext(
        company_name="Testly",
        job_title="Product Analyst",
        recruiter_first_name="Casey",
        candidate_name="Alex Doe",
        candidate_headline="analyst with two years in payments",
        matched_skills=["SQL", "A/B Testing"],
    )
    completion = TemplateProvider().complete(build_messages(context))
    body = completion.text
    assert "Casey" in body
    assert "Product Analyst" in body
    assert "Testly" in body
    assert completion.fallback is True
    # Nothing invented: no company praise, no fabricated relationship.
    for phrase in ["long admired", "we met", "mutual", "referred me", "!"]:
        assert phrase not in body


def test_parse_completion_splits_subject_and_body() -> None:
    subject, body = parse_completion("Subject: Hello there\n\nBody text here.", "fallback")
    assert subject == "Hello there"
    assert body == "Body text here."


def test_parse_completion_falls_back_when_no_subject_line() -> None:
    subject, body = parse_completion("Just a body.", "Role at Company")
    assert subject == "Role at Company"
    assert body == "Just a body."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("As discussed last week, I wanted to follow up on the role we spoke about.", "prior"),
        ("Our mutual connection suggested I reach out about this opening.", "mutual"),
        ("Hope this email finds you well, I am writing about the role.", "filler"),
        ("I have long admired your company and its incredible mission here.", "flattery"),
    ],
)
def test_review_draft_flags_fabrication_and_spam_patterns(text: str, expected: str) -> None:
    warnings = review_draft(text, EmailContext(company_name="X"))
    assert warnings, f"expected a warning about {expected}"


def test_review_draft_is_quiet_on_a_good_draft() -> None:
    context = EmailContext(company_name="Testly", recruiter_first_name="Casey")
    body = (
        "Hi Casey,\n\nI saw the Product Analyst opening at Testly. I spent the last two years "
        "on payments analytics, owning the retention dashboard and running checkout "
        "experiments, which lines up with what the role describes. I would be glad to send a "
        "short summary of that work if it is useful.\n\nThanks for your time,\nAlex"
    )
    assert review_draft(body, context) == []


def test_generation_falls_back_when_the_provider_fails() -> None:
    """A provider outage must degrade to a usable draft, not an error page."""

    class BrokenProvider(LLMProvider):
        name = "broken"

        @property
        def model(self) -> str:
            return "broken-1"

        def complete(self, messages: list[ChatMessage], **kwargs: object) -> Completion:
            raise LLMError("provider exploded")

    company = SimpleNamespace(company_name="Testly", industry="Fintech")
    job = SimpleNamespace(
        title="Product Analyst",
        job_url="https://testly.example/j/1",
        description="SQL and analytics",
        location="Remote",
        posted_at=utcnow(),
        relevance_breakdown={"reasons": ["Matches your target role"]},
    )
    recruiter = SimpleNamespace(
        name="Casey Talent",
        title="Talent Partner",
        public_professional_email="casey@testly.example",
        email_confidence=EmailConfidence.HIGH,
        email_source_type=SourceType.COMPANY_TEAM_PAGE,
    )
    profile = SimpleNamespace(
        full_name="Alex Doe",
        headline="product analyst",
        education="B.Tech",
        experience="Built dashboards",
        skills=["SQL"],
        years_experience=2.0,
        portfolio_url=None,
        github_url=None,
        linkedin_url=None,
    )

    draft = generate_draft(company, job, recruiter, profile, provider=BrokenProvider())  # type: ignore[arg-type]
    assert draft.is_fallback is True
    assert draft.body
    assert draft.approved is False
    assert any("offline template" in w for w in draft.warnings)


def test_draft_is_never_returned_as_approved() -> None:
    company = SimpleNamespace(company_name="Testly", industry=None)
    recruiter = SimpleNamespace(
        name="Casey", title="Recruiter", public_professional_email=None,
        email_confidence=EmailConfidence.NONE, email_source_type=None,
    )
    draft = generate_draft(company, None, recruiter, None, provider=TemplateProvider())  # type: ignore[arg-type]
    assert draft.approved is False
    assert draft.to_dict()["approved"] is False


def test_prompt_forbids_fabrication() -> None:
    from app.services.ai.email_generator import SYSTEM_PROMPT

    lowered = SYSTEM_PROMPT.lower()
    for rule in ["never invent", "mutual connection", "no flattery", "referral"]:
        assert rule in lowered or rule.rstrip("s") in lowered
