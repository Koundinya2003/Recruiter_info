"""Reference data used to read a free-text search request.

Deliberately small and hand-curated: it only has to recognise the places and
role words a job seeker actually types. Anything it does not recognise is left
in the title, which is the safe failure mode — an unrecognised word narrows the
search rather than silently changing its meaning.
"""

from __future__ import annotations

from app.utils.text import basic_normalize

# --- Countries ---------------------------------------------------------------
# ISO-3166 alpha-2 codes, lowercase, as used by Adzuna's country path segment.
COUNTRY_CODES: dict[str, str] = {
    "india": "in",
    "united states": "us",
    "usa": "us",
    "us": "us",
    "america": "us",
    "united kingdom": "gb",
    "uk": "gb",
    "britain": "gb",
    "england": "gb",
    "canada": "ca",
    "australia": "au",
    "germany": "de",
    "france": "fr",
    "netherlands": "nl",
    "singapore": "sg",
    "ireland": "ie",
    "spain": "es",
    "italy": "it",
    "poland": "pl",
    "brazil": "br",
    "mexico": "mx",
    "south africa": "za",
    "new zealand": "nz",
    "austria": "at",
    "switzerland": "ch",
    "belgium": "be",
}

# Countries Adzuna publishes a search index for.
ADZUNA_COUNTRIES = frozenset(
    {
        "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb", "in",
        "it", "mx", "nl", "nz", "pl", "sg", "us", "za",
    }
)

# --- Cities ------------------------------------------------------------------
# city (normalized) -> (display name, country code). Aliases map to the same
# display name so "bengaluru" and "bangalore" dedupe to one location.
CITIES: dict[str, tuple[str, str]] = {
    # India
    "bangalore": ("Bangalore", "in"),
    "bengaluru": ("Bangalore", "in"),
    "hyderabad": ("Hyderabad", "in"),
    "mumbai": ("Mumbai", "in"),
    "bombay": ("Mumbai", "in"),
    "delhi": ("Delhi", "in"),
    "new delhi": ("Delhi", "in"),
    "ncr": ("Delhi NCR", "in"),
    "delhi ncr": ("Delhi NCR", "in"),
    "gurgaon": ("Gurgaon", "in"),
    "gurugram": ("Gurgaon", "in"),
    "noida": ("Noida", "in"),
    "pune": ("Pune", "in"),
    "chennai": ("Chennai", "in"),
    "kolkata": ("Kolkata", "in"),
    "ahmedabad": ("Ahmedabad", "in"),
    "jaipur": ("Jaipur", "in"),
    "kochi": ("Kochi", "in"),
    "coimbatore": ("Coimbatore", "in"),
    "indore": ("Indore", "in"),
    "chandigarh": ("Chandigarh", "in"),
    "trivandrum": ("Trivandrum", "in"),
    "thiruvananthapuram": ("Trivandrum", "in"),
    # United States
    "san francisco": ("San Francisco", "us"),
    "sf": ("San Francisco", "us"),
    "bay area": ("San Francisco Bay Area", "us"),
    "new york": ("New York", "us"),
    "nyc": ("New York", "us"),
    "seattle": ("Seattle", "us"),
    "austin": ("Austin", "us"),
    "boston": ("Boston", "us"),
    "chicago": ("Chicago", "us"),
    "los angeles": ("Los Angeles", "us"),
    "denver": ("Denver", "us"),
    "atlanta": ("Atlanta", "us"),
    "san diego": ("San Diego", "us"),
    "washington dc": ("Washington DC", "us"),
    # Europe
    "london": ("London", "gb"),
    "manchester": ("Manchester", "gb"),
    "edinburgh": ("Edinburgh", "gb"),
    "dublin": ("Dublin", "ie"),
    "berlin": ("Berlin", "de"),
    "munich": ("Munich", "de"),
    "hamburg": ("Hamburg", "de"),
    "amsterdam": ("Amsterdam", "nl"),
    "paris": ("Paris", "fr"),
    "madrid": ("Madrid", "es"),
    "barcelona": ("Barcelona", "es"),
    "zurich": ("Zurich", "ch"),
    "warsaw": ("Warsaw", "pl"),
    "lisbon": ("Lisbon", "pt"),
    # Rest of world
    "singapore": ("Singapore", "sg"),
    "toronto": ("Toronto", "ca"),
    "vancouver": ("Vancouver", "ca"),
    "sydney": ("Sydney", "au"),
    "melbourne": ("Melbourne", "au"),
    "dubai": ("Dubai", "ae"),
    "tokyo": ("Tokyo", "jp"),
    "sao paulo": ("São Paulo", "br"),
    "mexico city": ("Mexico City", "mx"),
    "cape town": ("Cape Town", "za"),
    "tel aviv": ("Tel Aviv", "il"),
}

REMOTE_TERMS = frozenset(
    {"remote", "work from home", "wfh", "anywhere", "fully remote", "remote only"}
)

HYBRID_TERMS = frozenset({"hybrid", "onsite", "on-site", "in office", "in-office"})

# --- Role vocabulary ---------------------------------------------------------
# Common abbreviations job seekers type. Expanded into extra title variants so
# an "APM" search also matches boards that spell it out.
ROLE_ABBREVIATIONS: dict[str, str] = {
    "apm": "associate product manager",
    "pm": "product manager",
    "tpm": "technical program manager",
    "gpm": "group product manager",
    "sde": "software development engineer",
    "swe": "software engineer",
    "sre": "site reliability engineer",
    "ml engineer": "machine learning engineer",
    "ds": "data scientist",
    "ba": "business analyst",
    "qa": "quality assurance engineer",
    "ux": "user experience designer",
    "ui": "user interface designer",
    "hr": "human resources",
    "sdr": "sales development representative",
    "ae": "account executive",
    "csm": "customer success manager",
}

# Extra title variants worth searching alongside the one the user typed.
TITLE_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "associate product manager": ("product manager", "apm", "product management associate"),
    "product manager": ("associate product manager", "product owner"),
    "software engineer": ("software development engineer", "backend engineer", "developer"),
    "data scientist": ("data science", "machine learning engineer"),
    "data analyst": ("business analyst", "analytics"),
    "product designer": ("ux designer", "product design"),
    "program manager": ("technical program manager", "project manager"),
}

# --- Industries --------------------------------------------------------------
INDUSTRY_TERMS: dict[str, tuple[str, ...]] = {
    "fintech": ("fintech", "payments", "banking", "financial services", "lending"),
    "healthtech": ("healthtech", "healthcare", "health tech", "medical", "biotech"),
    "edtech": ("edtech", "education", "learning", "e-learning"),
    "ecommerce": ("ecommerce", "e-commerce", "retail", "marketplace", "d2c"),
    "saas": ("saas", "b2b saas", "enterprise software"),
    "gaming": ("gaming", "games", "game studio"),
    "logistics": ("logistics", "supply chain", "delivery", "mobility"),
    "consulting": ("consulting", "advisory", "professional services"),
    "media": ("media", "entertainment", "streaming", "publishing"),
    "climate": ("climate", "cleantech", "sustainability", "renewable"),
    "ai": ("ai", "artificial intelligence", "machine learning", "llm", "genai"),
    "cybersecurity": ("cybersecurity", "security", "infosec"),
    "traveltech": ("travel", "traveltech", "hospitality"),
    "agritech": ("agritech", "agriculture", "farming"),
}


def resolve_location(text: str | None) -> tuple[str, str | None] | None:
    """Map a location phrase to ``(display name, country code)``.

    Returns ``None`` when the phrase is not a place we recognise, so the caller
    can leave it alone instead of guessing.
    """
    if not text:
        return None
    key = basic_normalize(text)
    if not key:
        return None
    if key in CITIES:
        return CITIES[key]
    if key in COUNTRY_CODES:
        code = COUNTRY_CODES[key]
        display = next(
            (name.title() for name, c in COUNTRY_CODES.items() if c == code and name == key),
            text.strip().title(),
        )
        return (display, code)
    # "Bangalore, India" / "London, UK"
    if "," in text:
        head = text.split(",", 1)[0]
        if basic_normalize(head) != key:
            return resolve_location(head)
    return None


def country_for(locations: list[str]) -> list[str]:
    """Country codes implied by a list of location names, in first-seen order."""
    codes: list[str] = []
    for location in locations:
        resolved = resolve_location(location)
        if resolved and resolved[1] and resolved[1] not in codes:
            codes.append(resolved[1])
    return codes


def is_remote_term(text: str | None) -> bool:
    return basic_normalize(text) in REMOTE_TERMS


def expand_title(title: str) -> list[str]:
    """The title plus close variants worth querying alongside it."""
    key = basic_normalize(title)
    out = [title.strip()]
    expanded = ROLE_ABBREVIATIONS.get(key)
    if expanded and expanded not in {basic_normalize(t) for t in out}:
        out.append(expanded)
    for variant in TITLE_EXPANSIONS.get(key, ()):  # noqa: SIM118
        if basic_normalize(variant) not in {basic_normalize(t) for t in out}:
            out.append(variant)
    return out


def industry_keywords(industry: str) -> tuple[str, ...]:
    return INDUSTRY_TERMS.get(basic_normalize(industry), (industry.strip().lower(),))


def _build_alias_index() -> dict[str, set[str]]:
    """display name -> every spelling that resolves to it."""
    index: dict[str, set[str]] = {}
    for alias, (display, _code) in CITIES.items():
        index.setdefault(display, set()).add(alias)
        index[display].add(basic_normalize(display))
    for alias in COUNTRY_CODES:
        display = alias.title()
        index.setdefault(display, set()).add(alias)
    return index


_ALIAS_INDEX: dict[str, set[str]] = _build_alias_index()


def location_aliases(name: str) -> set[str]:
    """Every normalized spelling of a place, so "Bengaluru" matches "Bangalore".

    Falls back to the name itself for places the gazetteer does not know, which
    keeps an unrecognised location working as a plain substring match.
    """
    display = name
    resolved = resolve_location(name)
    if resolved is not None:
        display = resolved[0]
    aliases = set(_ALIAS_INDEX.get(display, set()))
    aliases.add(basic_normalize(display))
    aliases.add(basic_normalize(name))
    return {a for a in aliases if a}


def country_aliases(code: str) -> set[str]:
    """Names that refer to a country, for country-level location matching."""
    return {alias for alias, c in COUNTRY_CODES.items() if c == code}
