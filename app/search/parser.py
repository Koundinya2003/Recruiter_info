"""Turn a free-text job search request into a :class:`JobQuery`.

The parser is rule-based and runs with no API key, because the product has to
work offline. When an LLM is configured it runs *after* the rules and may only
fill gaps the rules left or add title variants — it never overwrites a value
the rules extracted from an explicit phrase, and its output is validated field
by field before being merged. That ordering is what keeps a bad model response
from quietly changing what the user asked for.

Worked example::

    "Find Associate Product Manager roles for 0-2 years of experience in
     Bangalore and Hyderabad."

    titles     = ["Associate Product Manager"]
    min_years  = 0.0, max_years = 2.0
    locations  = ["Bangalore", "Hyderabad"]
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.logging_config import get_logger
from app.search.experience import parse_experience
from app.search.gazetteer import (
    CITIES,
    COUNTRY_CODES,
    HYBRID_TERMS,
    INDUSTRY_TERMS,
    REMOTE_TERMS,
    ROLE_ABBREVIATIONS,
    resolve_location,
)
from app.search.query import MAX_LIMIT, JobQuery
from app.utils.text import basic_normalize

log = get_logger(__name__)

# Lead-ins people type that carry no meaning for the search.
_LEAD_IN = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:help\s+me\s+)?"
    r"(?:find|search(?:\s+for)?|look(?:ing)?\s+for|show(?:\s+me)?|get(?:\s+me)?|"
    r"list|fetch|discover|i\s+(?:want|need|am\s+looking\s+for))\s+",
    re.IGNORECASE,
)

# Words that mean "job" and add nothing once we know it is a job search.
_ROLE_NOUNS = re.compile(
    r"\b(?:roles?|jobs?|positions?|openings?|opportunit(?:y|ies)|vacanc(?:y|ies)|"
    r"posting?s?|listings?)\b",
    re.IGNORECASE,
)

_EXPERIENCE_CLAUSE = re.compile(
    r"\b(?:for|with|requiring|needing|having|of)?\s*"
    r"(?:\d{1,2}\s*(?:-|–|—|to|~)\s*\d{1,2}|\d{1,2}\s*\+?)\s*"
    r"(?:years?|yrs?)(?:\s+of)?(?:\s+(?:work\s+)?experience)?",
    re.IGNORECASE,
)
_EXPERIENCE_WORDS = re.compile(
    r"\b(?:for\s+)?(?:freshers?|fresh\s+graduates?|new\s+grads?|entry[\s\-]?level|"
    r"early\s+career|campus\s+hires?)\b",
    re.IGNORECASE,
)

_LOCATION_CLAUSE = re.compile(
    r"\b(?:in|at|near|around|based\s+in|located\s+in|across)\s+"
    r"(?P<body>[A-Za-z][A-Za-z .'’\-]*"
    r"(?:\s*(?:,|and|&|/|\+|or)\s*[A-Za-z][A-Za-z .'’\-]*)*)",
    re.IGNORECASE,
)
_COMPANY_CLAUSE = re.compile(
    r"\b(?:at|with|for)\s+(?P<body>(?:[A-Z][\w&.'’\-]*)(?:\s+[A-Z][\w&.'’\-]*){0,3}"
    r"(?:\s*(?:,|and|&|/)\s*[A-Z][\w&.'’\-]*(?:\s+[A-Z][\w&.'’\-]*){0,3})*)",
)
_KEYWORD_CLAUSE = re.compile(
    r"\b(?:with|using|skills?(?:\s+in)?|knowing|experienced?\s+in|proficient\s+in)\s+"
    r"(?P<body>[\w+#.]+(?:\s*(?:,|and|&|/|\+)\s*[\w+#.]+)*)",
    re.IGNORECASE,
)
_EXCLUDE_CLAUSE = re.compile(
    r"\b(?:not|no|except|excluding|exclude|without|avoid)\s+"
    r"(?P<body>[\w .'’\-]+?)(?=\s*(?:,|\.|;|$|\band\b))",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"[\"“”']([^\"“”']{2,60})[\"“”']")
_SPLIT = re.compile(r"\s*(?:,|/|\+|&|\band\b|\bor\b)\s*", re.IGNORECASE)

# Words that look like a company in "at X" but are not.
_NOT_A_COMPANY = frozenset(
    {
        "least", "most", "level", "entry", "senior", "junior", "any", "all",
        "startups", "startup", "companies", "company", "firms", "mnc", "mncs",
        "product", "tech", "the", "a", "an", "top", "big", "small", "early",
        "stage", "unicorn", "unicorns", "faang",
    }
)

# Sentinel used while blanking out consumed spans so offsets stay stable.
_BLANK = "\x00"


class _Text:
    """A string with spans progressively blanked out as they are consumed."""

    def __init__(self, value: str) -> None:
        self.original = value
        self.chars = list(value)

    def take(self, start: int, end: int) -> None:
        for i in range(max(0, start), min(len(self.chars), end)):
            self.chars[i] = _BLANK

    @property
    def remaining(self) -> str:
        return "".join(c for c in self.chars if c != _BLANK)

    @property
    def current(self) -> str:
        return "".join(" " if c == _BLANK else c for c in self.chars)


def _split_list(body: str) -> list[str]:
    parts = [p.strip(" .,-–—\t") for p in _SPLIT.split(body)]
    return [p for p in parts if p and len(p) > 1]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = basic_normalize(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value.strip())
    return out


def _extract_experience(text: _Text, query: JobQuery) -> None:
    for pattern in (_EXPERIENCE_CLAUSE, _EXPERIENCE_WORDS):
        match = pattern.search(text.current)
        if not match:
            continue
        parsed = parse_experience(match.group(0))
        if parsed.min_years is not None or parsed.max_years is not None or parsed.level:
            query.min_years = parsed.min_years
            query.max_years = parsed.max_years
            query.experience_level = parsed.level
            query.experience_text = match.group(0).strip(" .,")
            text.take(match.start(), match.end())
            return


def _extract_remote(text: _Text, query: JobQuery) -> None:
    lowered = text.current.lower()
    for term in sorted(REMOTE_TERMS, key=len, reverse=True):
        index = lowered.find(term)
        if index == -1:
            continue
        # "remote" as part of a longer place name is not a remote flag.
        query.remote_only = True
        text.take(index, index + len(term))
        return
    for term in HYBRID_TERMS:
        index = lowered.find(term)
        if index != -1:
            query.parse_notes.append(f"Noted '{term}' — filtering on it is up to you.")
            text.take(index, index + len(term))
            return


def _extract_locations(text: _Text, query: JobQuery) -> None:
    found: list[str] = []
    for match in _LOCATION_CLAUSE.finditer(text.current):
        candidates = _split_list(match.group("body"))
        resolved = [(c, resolve_location(c)) for c in candidates]
        hits = [r[1][0] for r in resolved if r[1] is not None]
        if not hits:
            continue
        found.extend(hits)
        # Only consume the words that actually resolved, so "in Bangalore for
        # a fintech" does not swallow the industry.
        consumed_end = match.start("body")
        body = match.group("body")
        for candidate, resolution in resolved:
            position = body.find(candidate)
            if resolution is None:
                break
            consumed_end = max(consumed_end, match.start("body") + position + len(candidate))
        text.take(match.start(), consumed_end)
    # Bare city names with no preposition ("Bangalore APM jobs").
    if not found:
        lowered = text.current.lower()
        for name in sorted(CITIES, key=len, reverse=True):
            index = lowered.find(name)
            if index == -1:
                continue
            before_ok = index == 0 or not lowered[index - 1].isalnum()
            after = index + len(name)
            after_ok = after >= len(lowered) or not lowered[after].isalnum()
            if before_ok and after_ok:
                found.append(CITIES[name][0])
                text.take(index, after)
                break
    query.locations = _dedupe(found)


def _extract_countries(text: _Text, query: JobQuery) -> None:
    """Pick up a bare country name when no city was mentioned."""
    if query.locations:
        return
    lowered = text.current.lower()
    for name in sorted(COUNTRY_CODES, key=len, reverse=True):
        if len(name) <= 3:
            continue  # "us"/"uk" are too collision-prone without a preposition
        index = lowered.find(name)
        if index == -1:
            continue
        before_ok = index == 0 or not lowered[index - 1].isalnum()
        after = index + len(name)
        after_ok = after >= len(lowered) or not lowered[after].isalnum()
        if before_ok and after_ok:
            query.locations = [name.title()]
            text.take(index, after)
            return


def _extract_companies(text: _Text, query: JobQuery) -> None:
    found: list[str] = []
    for match in _COMPANY_CLAUSE.finditer(text.current):
        candidates = _split_list(match.group("body"))
        keep = [
            c
            for c in candidates
            if basic_normalize(c) not in _NOT_A_COMPANY
            and resolve_location(c) is None
            and basic_normalize(c) not in INDUSTRY_TERMS
        ]
        if not keep or len(keep) != len(candidates):
            continue
        found.extend(keep)
        text.take(match.start(), match.end())
    query.companies = _dedupe(found)


def _extract_industries(text: _Text, query: JobQuery) -> None:
    found: list[str] = []
    lowered = text.current.lower()
    for industry, terms in INDUSTRY_TERMS.items():
        for term in terms:
            index = lowered.find(term)
            if index == -1:
                continue
            before_ok = index == 0 or not lowered[index - 1].isalnum()
            after = index + len(term)
            after_ok = after >= len(lowered) or not lowered[after].isalnum()
            if before_ok and after_ok:
                found.append(industry)
                text.take(index, after)
                break
    query.industries = _dedupe(found)


def _extract_keywords(text: _Text, query: JobQuery) -> None:
    found: list[str] = []
    for match in _QUOTED.finditer(text.current):
        found.append(match.group(1))
        text.take(match.start(), match.end())
    for match in _KEYWORD_CLAUSE.finditer(text.current):
        found.extend(_split_list(match.group("body")))
        text.take(match.start(), match.end())
    query.keywords = _dedupe(found)


def _extract_exclusions(text: _Text, query: JobQuery) -> None:
    found: list[str] = []
    for match in _EXCLUDE_CLAUSE.finditer(text.current):
        found.extend(_split_list(match.group("body")))
        text.take(match.start(), match.end())
    query.exclusions = _dedupe([f for f in found if len(f) > 2])


def _extract_titles(text: _Text, query: JobQuery) -> None:
    remainder = text.current
    remainder = _LEAD_IN.sub(" ", remainder)
    remainder = _ROLE_NOUNS.sub(" ", remainder)
    remainder = re.sub(r"\b(?:for|in|at|with|of|the|a|an|any|please|me)\b", " ", remainder, flags=re.I)
    remainder = re.sub(r"[^\w\s&/+#.'’\-]", " ", remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip(" .,-")

    if not remainder:
        return
    titles = [t for t in _split_list(remainder) if t] or [remainder]
    cleaned: list[str] = []
    for title in titles:
        candidate = title.strip()
        if len(candidate) < 2:
            continue
        expanded = ROLE_ABBREVIATIONS.get(basic_normalize(candidate))
        cleaned.append(expanded.title() if expanded else candidate)
    query.titles = _dedupe(cleaned)


def parse_query_rules(raw: str, *, limit: int | None = None) -> JobQuery:
    """Parse ``raw`` with rules only. Never raises on odd input."""
    query = JobQuery(raw=raw.strip(), parse_method="rules")
    if limit:
        query.limit = max(1, min(int(limit), MAX_LIMIT))
    if not raw or not raw.strip():
        query.parse_notes.append("Empty search request.")
        return query

    text = _Text(raw.strip())
    # Order matters: the most explicitly-marked clauses are consumed first so
    # whatever is left over can safely be treated as the role title.
    _extract_experience(text, query)
    _extract_exclusions(text, query)
    _extract_keywords(text, query)
    _extract_remote(text, query)
    _extract_locations(text, query)
    _extract_countries(text, query)
    _extract_industries(text, query)
    _extract_companies(text, query)
    _extract_titles(text, query)

    if not query.titles and not query.keywords:
        query.parse_notes.append(
            "No role title recognised — searching on the whole phrase instead."
        )
        query.titles = [raw.strip()[:120]]
    if not query.locations and not query.remote_only:
        query.parse_notes.append("No location given — searching everywhere the sources cover.")
    return query


# --- Optional LLM refinement -------------------------------------------------

_LLM_SYSTEM = (
    "You extract structured job-search criteria from a sentence. "
    "Reply with ONLY a JSON object, no prose. Keys: titles (array of strings), "
    "locations (array of city or country names), companies (array), industries "
    "(array), keywords (array of skills), exclusions (array), min_years (number "
    "or null), max_years (number or null), remote_only (boolean). "
    "Use only information present in the sentence. Never invent a company, a "
    "city or a number that is not there. Use null when something is not stated."
)

_ALLOWED_LIST_FIELDS = ("titles", "locations", "companies", "industries", "keywords", "exclusions")
_MAX_LIST_ITEMS = 8
_MAX_ITEM_LENGTH = 80


def _coerce_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value[:_MAX_LIST_ITEMS]:
        if isinstance(item, str) and 1 < len(item.strip()) <= _MAX_ITEM_LENGTH:
            out.append(item.strip())
    return out


def refine_with_llm(query: JobQuery, *, raw: str) -> JobQuery:
    """Fill gaps the rules left, using the configured LLM. Best-effort only.

    Every value is validated before it is merged, and a field the rules already
    filled from an explicit phrase is never overwritten. If anything goes wrong
    the rule-based query is returned unchanged.
    """
    from app.services.ai.provider import ChatMessage, LLMError, get_provider

    provider = get_provider()
    if provider is None:
        # No model configured. The rules already did the work; say nothing.
        return query

    try:
        completion = provider.complete(
            [
                ChatMessage(role="system", content=_LLM_SYSTEM),
                ChatMessage(role="user", content=raw[:1000]),
            ],
            max_tokens=400,
        )
        payload = _extract_json(completion.text)
    except (LLMError, ValueError, json.JSONDecodeError) as exc:
        log.info("query.llm_refine_failed", error=str(exc)[:200])
        query.parse_notes.append("Understood with rules only (the language model did not answer).")
        return query

    if not isinstance(payload, dict):
        return query

    merged = 0
    for fieldname in _ALLOWED_LIST_FIELDS:
        candidate = _coerce_list(payload.get(fieldname))
        if not candidate:
            continue
        current = getattr(query, fieldname)
        if fieldname == "locations":
            # Only accept places the gazetteer recognises: this is the field a
            # hallucination would do the most damage to.
            candidate = [
                resolved[0]
                for resolved in (resolve_location(c) for c in candidate)
                if resolved is not None
            ]
            if not candidate:
                continue
        if not current:
            setattr(query, fieldname, _dedupe(candidate))
            merged += 1
        elif fieldname == "titles":
            setattr(query, fieldname, _dedupe(current + candidate))
            merged += 1

    if query.min_years is None and query.max_years is None:
        low, high = payload.get("min_years"), payload.get("max_years")
        low = low if isinstance(low, (int, float)) and 0 <= low <= 40 else None
        high = high if isinstance(high, (int, float)) and 0 <= high <= 40 else None
        if low is not None or high is not None:
            query.min_years = float(low) if low is not None else None
            query.max_years = float(high) if high is not None else None
            merged += 1

    if not query.remote_only and payload.get("remote_only") is True:
        query.remote_only = True
        merged += 1

    if merged:
        query.parse_method = "rules+llm"
        query.parse_notes.append(f"A language model filled in {merged} field(s) the rules missed.")
    return query


def _extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", stripped).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(stripped[start : end + 1])


def parse_query(raw: str, *, limit: int | None = None, use_llm: bool = True) -> JobQuery:
    """Parse a free-text search request into structured criteria."""
    query = parse_query_rules(raw, limit=limit)
    if use_llm:
        query = refine_with_llm(query, raw=raw)
    return query
