"""LLM provider abstraction.

The application talks to :class:`LLMProvider`, never to a vendor SDK.
:class:`OpenAICompatibleProvider` speaks to any OpenAI-compatible
``/chat/completions`` endpoint; it defaults to OpenRouter, and pointing
``OPENROUTER_BASE_URL`` at OpenAI, Together or a local gateway changes nothing
else.

A model is used for exactly one thing here: helping to read a free-text search
request. It never writes a message on the user's behalf, and it is never the
source of a job, a company or a contact. When no key is configured
:func:`get_provider` returns ``None`` and the rule-based parser is used alone —
which is why the product works fully offline.
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


def get_provider() -> LLMProvider | None:
    """The configured provider, or ``None`` when no key is set.

    Returning ``None`` rather than a stand-in is deliberate: callers must decide
    what to do without a model, and the honest answer here is "use the rules".
    """
    provider = OpenAICompatibleProvider()
    if not provider.available():
        log.debug("ai.no_key_configured")
        return None
    return provider
