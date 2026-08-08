"""LLM provider abstraction.

The application talks to :class:`LLMProvider`, never to a vendor SDK. Two
implementations ship:

* :class:`OpenAICompatibleProvider` — any OpenAI-compatible ``/chat/completions``
  endpoint. Defaults to OpenRouter; point ``OPENROUTER_BASE_URL`` at OpenAI,
  Together, or a local gateway and nothing else changes.
* :class:`TemplateProvider` — a deterministic, offline drafter used when no API
  key is configured, so email generation works out of the box (and in tests)
  without a network call or a bill.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)


class LLMError(Exception):
    """Raised when a provider cannot produce a completion."""


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    usage: dict[str, Any] = field(default_factory=dict)
    fallback: bool = False


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None, temperature: float = 0.4
    ) -> Completion:
        """Produce a completion for ``messages``."""

    @property
    @abstractmethod
    def model(self) -> str:
        ...

    def available(self) -> bool:
        return True


class OpenAICompatibleProvider(LLMProvider):
    """Any OpenAI-compatible chat completions API."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.openrouter_api_key
        self.base_url = (base_url or settings.openrouter_base_url).rstrip("/")
        self._model = model or settings.openrouter_model
        self.timeout = timeout if timeout is not None else settings.ai_request_timeout

    @property
    def model(self) -> str:
        return self._model

    def available(self) -> bool:
        return bool(self.api_key.strip())

    def complete(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None, temperature: float = 0.4
    ) -> Completion:
        if not self.available():
            raise LLMError("No API key configured for the AI provider")

        payload = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": max_tokens or settings.ai_max_tokens,
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # OpenRouter asks callers to identify themselves.
            "HTTP-Referer": "https://github.com/recruiter-outreach-intelligence",
            "X-Title": settings.app_name,
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"AI provider unreachable: {exc}") from exc

        if response.status_code >= 400:
            # Never echo the response body verbatim: it can contain the key.
            raise LLMError(
                f"AI provider returned HTTP {response.status_code}. Check the model name and key."
            )

        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected response shape from the AI provider: {exc}") from exc

        return Completion(
            text=(text or "").strip(),
            model=data.get("model", self._model),
            provider=self.name,
            usage=data.get("usage", {}) or {},
        )


class TemplateProvider(LLMProvider):
    """Offline fallback: fills a plain, honest template. No network, no key.

    The template only ever restates facts passed in through the prompt context,
    which is precisely the property we want — it cannot invent a shared
    connection or an achievement, because it has no generative capacity at all.
    """

    name = "offline_template"

    @property
    def model(self) -> str:
        return "offline-template-v1"

    def complete(
        self, messages: list[ChatMessage], *, max_tokens: int | None = None, temperature: float = 0.4
    ) -> Completion:
        context: dict[str, Any] = {}
        for message in messages:
            if message.role == "user":
                marker = "CONTEXT_JSON:"
                if marker in message.content:
                    blob = message.content.split(marker, 1)[1].strip()
                    try:
                        context = json.loads(blob)
                    except json.JSONDecodeError:
                        context = {}
        return Completion(
            text=render_template_email(context),
            model=self.model,
            provider=self.name,
            fallback=True,
        )


def render_template_email(context: dict[str, Any]) -> str:
    """Render the offline draft. Uses only supplied facts."""
    recruiter = context.get("recruiter_first_name") or "there"
    company = context.get("company_name") or "your team"
    role = context.get("job_title") or "the open role"
    job_url = context.get("job_url") or ""
    candidate = context.get("candidate_name") or ""
    headline = context.get("candidate_headline") or ""
    skills = context.get("matched_skills") or []
    highlights = context.get("relevant_experience") or ""
    portfolio = context.get("portfolio_url") or ""
    linkedin = context.get("linkedin_url") or ""
    reason = context.get("reason_for_reaching_out") or ""

    subject = f"{role} at {company} — {candidate}" if candidate else f"{role} at {company}"

    lines = [f"Subject: {subject}", "", f"Hi {recruiter},", ""]

    opener = f"I saw the {role} opening at {company}"
    if job_url:
        opener += f" ({job_url})"
    opener += " and wanted to introduce myself."
    lines.append(opener)
    lines.append("")

    if headline:
        lines.append(f"I'm {candidate}, {headline}." if candidate else f"{headline}.")
    if highlights:
        lines.append(highlights.strip())
    if skills:
        lines.append(
            "The overlap I see with the role: " + ", ".join(str(s) for s in skills[:4]) + "."
        )
    if reason:
        lines.append(reason.strip())

    lines.append("")
    lines.append(
        "If it would help, I can send a short summary of the most relevant work. "
        "Happy to answer any questions."
    )
    lines.append("")
    lines.append("Thanks for your time,")
    if candidate:
        lines.append(candidate)
    contact_line = " | ".join(p for p in [portfolio, linkedin] if p)
    if contact_line:
        lines.append(contact_line)

    return "\n".join(lines)


def get_provider(force_offline: bool = False) -> LLMProvider:
    """Return the configured provider, or the offline drafter when unavailable."""
    if force_offline:
        return TemplateProvider()
    provider = OpenAICompatibleProvider()
    if not provider.available():
        log.info("ai.no_key_configured", fallback="offline_template")
        return TemplateProvider()
    return provider
