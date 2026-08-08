"""Text normalisation shared by duplicate detection and the scoring engines.

Normalisation must be deterministic and boring: the same posting seen through
a career page and through an ATS API has to collapse to the same key.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher

# Legal-entity suffixes stripped from company names before comparison.
COMPANY_SUFFIXES = {
    "inc", "inc.", "llc", "ltd", "ltd.", "limited", "corp", "corp.", "corporation",
    "co", "co.", "company", "gmbh", "bv", "nv", "plc", "pvt", "private", "pte", "sa", "ag",
    "technologies", "technology", "labs", "software", "systems", "solutions", "group",
    "holdings", "india", "global", "worldwide", "international",
}

# Noise routinely appended to job titles by ATS platforms.
TITLE_NOISE_PATTERNS = [
    r"\(.*?\)",                      # (Remote), (Contract), (f/m/d)
    r"\[.*?\]",
    r"\b(?:req(?:uisition)?\s*#?\s*\d+)\b",
    r"\bjob\s*id\s*:?\s*\w+\b",
    r"\b(?:full[- ]?time|part[- ]?time|contract|internship|permanent)\b",
    r"\b(?:remote|hybrid|on[- ]?site|onsite)\b",
    r"\b(?:m/f/d|f/m/d|m/w/d|d/f/m)\b",
    r"[-–—|,/]+\s*(?:remote|hybrid|bangalore|bengaluru|mumbai|delhi|gurgaon|noida|pune|hyderabad|chennai|india|usa|uk|emea|apac)\b.*$",
]

SENIORITY_TOKENS = {
    "intern": 0,
    "trainee": 0,
    "graduate": 0,
    "entry": 0,
    "junior": 1,
    "jr": 1,
    "associate": 1,
    "apm": 1,
    "analyst": 1,
    "mid": 2,
    "senior": 3,
    "sr": 3,
    "lead": 4,
    "staff": 4,
    "principal": 5,
    "head": 6,
    "director": 6,
    "vp": 7,
    "chief": 8,
}

_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


def strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )


def basic_normalize(value: str | None) -> str:
    """Lowercase, de-accent, strip punctuation, collapse whitespace."""
    if not value:
        return ""
    text = strip_accents(str(value)).lower()
    text = _NON_ALNUM.sub(" ", text)
    return _WS.sub(" ", text).strip()


def normalize_company_name(name: str | None) -> str:
    """``Razorpay Software Pvt. Ltd.`` -> ``razorpay``."""
    base = basic_normalize(name)
    if not base:
        return ""
    tokens = [t for t in base.split() if t not in COMPANY_SUFFIXES]
    return " ".join(tokens) or base


def normalize_title(title: str | None) -> str:
    """Strip ATS noise and canonicalise common abbreviations."""
    if not title:
        return ""
    text = strip_accents(str(title)).lower()
    for pattern in TITLE_NOISE_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    text = _NON_ALNUM.sub(" ", text)
    text = _WS.sub(" ", text).strip()

    replacements = {
        r"\bsr\b": "senior",
        r"\bjr\b": "junior",
        r"\bapm\b": "associate product manager",
        r"\bpm\b": "product manager",
        r"\bba\b": "business analyst",
        r"\bda\b": "data analyst",
        r"\bmgr\b": "manager",
        r"\bopsl?\b": "operations",
        r"\bai\s*ml\b": "ai",
        r"\bml\b": "machine learning",
    }
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    return _WS.sub(" ", text).strip()


def normalize_location(location: str | None) -> str:
    """Canonicalise a location string; recognises remote variants."""
    base = basic_normalize(location)
    if not base:
        return ""
    if re.search(r"\b(remote|anywhere|work from home|wfh|distributed)\b", base):
        if re.search(r"\b(india|in)\b", base):
            return "remote india"
        return "remote"
    aliases = {
        "bengaluru": "bangalore",
        "gurugram": "gurgaon",
        "new delhi": "delhi",
        "bombay": "mumbai",
        "united states": "usa",
        "united states of america": "usa",
        "united kingdom": "uk",
    }
    for alias, canonical in aliases.items():
        base = re.sub(rf"\b{re.escape(alias)}\b", canonical, base)
    return _WS.sub(" ", base).strip()


def seniority_level(title: str | None) -> int | None:
    """Coarse seniority level from a title, or None when unclear."""
    tokens = normalize_title(title).split()
    levels = [SENIORITY_TOKENS[t] for t in tokens if t in SENIORITY_TOKENS]
    if not levels:
        return None
    return max(levels)


def token_set(value: str | None) -> set[str]:
    return {t for t in basic_normalize(value).split() if len(t) > 1}


def similarity(a: str | None, b: str | None) -> float:
    """0..1 similarity between two normalised strings."""
    left, right = basic_normalize(a), basic_normalize(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def jaccard(a: str | None, b: str | None) -> float:
    left, right = token_set(a), token_set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def content_fingerprint(*parts: str | None) -> str:
    """Stable SHA-256 over normalised parts — the content-similarity dedupe key."""
    joined = "|".join(basic_normalize(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def contains_term(haystack: str | None, term: str) -> bool:
    """Whole-phrase containment on normalised text."""
    text = f" {basic_normalize(haystack)} "
    needle = f" {basic_normalize(term)} "
    return bool(needle.strip()) and needle in text


def truncate(value: str | None, limit: int = 280) -> str:
    if not value:
        return ""
    value = _WS.sub(" ", value).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"
