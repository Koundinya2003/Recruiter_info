"""Matching context: the target profile the scorers score *against*.

Built from the user's `user_profile` row plus their configurable
`taxonomy_terms`. Nothing here is hardcoded to a particular person — the
defaults below are only used to seed a brand-new account, and every one of them
is editable from the UI afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.config import TaxonomyTerm
from app.models.enums import TaxonomyKind
from app.models.user import UserProfile
from app.utils.text import basic_normalize, contains_term, jaccard, normalize_location

# Seed taxonomy for a new account — mirrors section 3 of the brief.
DEFAULT_TAXONOMY: dict[TaxonomyKind, list[tuple[str, list[str], float, bool]]] = {
    TaxonomyKind.ROLE: [
        ("Associate Product Manager", ["apm", "product manager associate"], 1.0, True),
        ("Product Analyst", ["product data analyst", "analyst product"], 1.0, True),
        ("Product Operations", ["product ops", "productops"], 1.0, True),
        ("Business Analyst", ["business analytics", "ba"], 1.0, True),
        ("Growth Analyst", ["growth analytics", "growth marketing analyst"], 1.0, True),
        ("AI Product", ["ai product manager", "ai product analyst", "llm product"], 1.0, True),
        ("Data Analyst", ["data analytics", "analytics associate"], 1.0, True),
        ("Product Strategy", ["strategy and operations", "strategy analyst"], 1.0, True),
        ("Product Manager", ["pm"], 0.7, False),
        ("Business Operations", ["bizops", "business ops"], 0.6, False),
    ],
    TaxonomyKind.INDUSTRY: [
        ("Fintech", ["financial technology", "payments", "banking technology", "neobank"], 1.0, True),
        ("Consumer Technology", ["consumer tech", "consumer internet", "d2c"], 1.0, True),
        ("SaaS", ["b2b saas", "enterprise software", "software as a service"], 1.0, True),
        ("AI Products", ["artificial intelligence", "machine learning", "ai"], 1.0, True),
    ],
    TaxonomyKind.SKILL: [
        ("SQL", ["postgres", "queries"], 1.0, True),
        ("Product Analytics", ["mixpanel", "amplitude", "product metrics"], 1.0, True),
        ("A/B Testing", ["experimentation", "ab testing", "split testing"], 1.0, True),
        ("Python", [], 0.8, True),
        ("Dashboarding", ["tableau", "power bi", "looker", "metabase"], 0.8, True),
        ("Roadmapping", ["prioritisation", "prioritization", "product roadmap"], 0.8, True),
        ("Stakeholder Management", ["cross functional"], 0.6, False),
        ("User Research", ["customer research", "discovery"], 0.6, False),
    ],
    TaxonomyKind.LOCATION: [
        ("Remote", ["work from home", "anywhere", "distributed"], 1.0, True),
        ("Bangalore", ["bengaluru"], 1.0, True),
        ("Mumbai", [], 0.8, True),
        ("Delhi", ["gurgaon", "gurugram", "noida", "ncr"], 0.8, True),
        ("Hyderabad", [], 0.7, True),
    ],
    TaxonomyKind.EXCLUDE: [
        ("Sales", ["account executive", "sales development", "sdr"], 1.0, True),
        ("Field Operations", ["delivery executive", "field agent"], 1.0, True),
        ("Director", ["vice president", "head of", "chief"], 1.0, True),
    ],
}


@dataclass
class MatchTerm:
    term: str
    aliases: list[str] = field(default_factory=list)
    weight: float = 1.0
    is_primary: bool = True

    @property
    def variants(self) -> list[str]:
        return [self.term, *self.aliases]


@dataclass
class MatchOutcome:
    term: MatchTerm | None
    strength: float  # 0..1
    kind: str = "none"  # exact | alias | partial | none

    @property
    def matched(self) -> bool:
        return self.term is not None and self.strength > 0


@dataclass
class ProfileContext:
    """Everything the scorers need to know about what the user is looking for."""

    user_id: int
    roles: list[MatchTerm] = field(default_factory=list)
    industries: list[MatchTerm] = field(default_factory=list)
    skills: list[MatchTerm] = field(default_factory=list)
    locations: list[MatchTerm] = field(default_factory=list)
    excludes: list[MatchTerm] = field(default_factory=list)

    years_experience: float | None = None
    desired_seniority: int | None = None
    profile_summary: str = ""
    has_profile: bool = False

    # -- matching helpers ----------------------------------------------------
    @staticmethod
    def _match(text: str | None, terms: list[MatchTerm], *, partial_floor: float = 0.34) -> MatchOutcome:
        if not text or not terms:
            return MatchOutcome(None, 0.0)
        best = MatchOutcome(None, 0.0)
        for term in terms:
            for index, variant in enumerate(term.variants):
                if contains_term(text, variant):
                    strength = term.weight if index == 0 else term.weight * 0.95
                    kind = "exact" if index == 0 else "alias"
                    if strength > best.strength:
                        best = MatchOutcome(term, strength, kind)
            if best.strength >= term.weight:
                continue
            overlap = max((jaccard(text, v) for v in term.variants), default=0.0)
            if overlap >= partial_floor:
                strength = term.weight * overlap
                if strength > best.strength:
                    best = MatchOutcome(term, strength, "partial")
        return best

    def match_role(self, title: str | None) -> MatchOutcome:
        return self._match(title, self.roles, partial_floor=0.45)

    def match_industry(self, *texts: str | None) -> MatchOutcome:
        best = MatchOutcome(None, 0.0)
        for text in texts:
            outcome = self._match(text, self.industries)
            if outcome.strength > best.strength:
                best = outcome
        return best

    def match_exclusion(self, title: str | None) -> MatchOutcome:
        if not title:
            return MatchOutcome(None, 0.0)
        for term in self.excludes:
            for variant in term.variants:
                if contains_term(title, variant):
                    return MatchOutcome(term, 1.0, "exact")
        return MatchOutcome(None, 0.0)

    def matching_skills(self, text: str | None) -> list[tuple[MatchTerm, float]]:
        if not text:
            return []
        found: list[tuple[MatchTerm, float]] = []
        for skill in self.skills:
            for variant in skill.variants:
                if contains_term(text, variant):
                    found.append((skill, skill.weight))
                    break
        return found

    def match_location(self, location: str | None) -> MatchOutcome:
        normalized = normalize_location(location)
        if not normalized:
            return MatchOutcome(None, 0.0)
        if normalized.startswith("remote"):
            for term in self.locations:
                if basic_normalize(term.term) == "remote":
                    return MatchOutcome(term, term.weight, "exact")
        return self._match(normalized, self.locations, partial_floor=0.5)


def _terms_from_rows(rows: list[TaxonomyTerm], kind: TaxonomyKind) -> list[MatchTerm]:
    return [
        MatchTerm(term=r.term, aliases=list(r.aliases or []), weight=r.weight, is_primary=r.is_primary)
        for r in rows
        if r.kind is kind and r.active
    ]


def _default_terms(kind: TaxonomyKind) -> list[MatchTerm]:
    return [
        MatchTerm(term=t, aliases=list(a), weight=w, is_primary=p)
        for t, a, w, p in DEFAULT_TAXONOMY.get(kind, [])
    ]


def seniority_from_years(years: float | None) -> int | None:
    if years is None:
        return None
    if years < 1:
        return 0
    if years < 2.5:
        return 1
    if years < 5:
        return 2
    if years < 8:
        return 3
    return 4


def build_context(session: Session, user_id: int) -> ProfileContext:
    """Assemble the matching context for ``user_id``."""
    rows = list(
        session.scalars(select(TaxonomyTerm).where(TaxonomyTerm.user_id == user_id)).all()
    )
    profile = session.scalar(select(UserProfile).where(UserProfile.user_id == user_id))

    def terms(kind: TaxonomyKind) -> list[MatchTerm]:
        found = _terms_from_rows(rows, kind)
        return found if found else _default_terms(kind)

    roles = terms(TaxonomyKind.ROLE)
    industries = terms(TaxonomyKind.INDUSTRY)
    skills = terms(TaxonomyKind.SKILL)
    locations = terms(TaxonomyKind.LOCATION)
    excludes = terms(TaxonomyKind.EXCLUDE)

    years = profile.years_experience if profile else None
    summary_parts: list[str] = []
    if profile:
        # Profile free-text also feeds role/industry/skill matching so a user who
        # never edits the taxonomy still gets sensible results.
        for extra in (profile.target_roles or []):
            if not any(basic_normalize(extra) == basic_normalize(r.term) for r in roles):
                roles.append(MatchTerm(term=extra, weight=1.0))
        for extra in (profile.target_industries or []):
            if not any(basic_normalize(extra) == basic_normalize(i.term) for i in industries):
                industries.append(MatchTerm(term=extra, weight=1.0))
        for extra in (profile.skills or []):
            if not any(basic_normalize(extra) == basic_normalize(s.term) for s in skills):
                skills.append(MatchTerm(term=extra, weight=0.8))
        for extra in (profile.preferred_locations or []):
            if not any(basic_normalize(extra) == basic_normalize(loc.term) for loc in locations):
                locations.append(MatchTerm(term=extra, weight=0.9))
        summary_parts = [
            profile.headline or "",
            profile.experience or "",
            profile.education or "",
        ]

    return ProfileContext(
        user_id=user_id,
        roles=roles,
        industries=industries,
        skills=skills,
        locations=locations,
        excludes=excludes,
        years_experience=years,
        desired_seniority=seniority_from_years(years),
        profile_summary=" ".join(p for p in summary_parts if p).strip(),
        has_profile=profile is not None,
    )


def seed_default_taxonomy(session: Session, user_id: int) -> int:
    """Insert the default taxonomy for a new user. Returns rows created."""
    existing = set(
        session.execute(
            select(TaxonomyTerm.kind, TaxonomyTerm.term).where(TaxonomyTerm.user_id == user_id)
        ).all()
    )
    created = 0
    for kind, entries in DEFAULT_TAXONOMY.items():
        for term, aliases, weight, is_primary in entries:
            if (kind, term) in existing:
                continue
            session.add(
                TaxonomyTerm(
                    user_id=user_id,
                    kind=kind,
                    term=term,
                    aliases=list(aliases),
                    weight=weight,
                    is_primary=is_primary,
                    active=True,
                )
            )
            created += 1
    session.flush()
    return created
