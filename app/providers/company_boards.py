"""First-party job boards: Greenhouse, Lever and Ashby.

These are the best source in the product. Each is a documented, unauthenticated
JSON endpoint whose entire purpose is to let anyone render a company's open
roles, so a posting that comes back is by definition one the employer is
advertising right now. That is why postings from here start out already
validated instead of needing a separate liveness fetch.

Board identifiers are never assumed. A candidate slug is derived from the
company name, probed against the real API, and only accepted when the endpoint
answers with postings — so a wrong guess produces "no public board found",
never a fabricated company board.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.collectors.http_client import FetchBlocked
from app.config import settings
from app.logging_config import get_logger
from app.models.enums import SourceType
from app.providers.base import JobProvider
from app.providers.types import RawJob
from app.search.query import JobQuery
from app.utils.dates import parse_datetime
from app.utils.html import html_to_text
from app.utils.text import basic_normalize, normalize_company_name

log = get_logger(__name__)

DEFAULT_BOARDS_FILE = Path(__file__).resolve().parents[1] / "data" / "company_boards.json"

_LEGAL_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|limited|plc|gmbh|pvt|private|corp|corporation|co|company|"
    r"technologies|technology|labs|software|solutions|systems|group|holdings)\b\.?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Board:
    platform: str
    token: str
    company: str

    @property
    def api_url(self) -> str:
        return PLATFORMS[self.platform]["api"].format(token=self.token)

    @property
    def public_url(self) -> str:
        return PLATFORMS[self.platform]["public"].format(token=self.token)

    @property
    def source(self) -> SourceType:
        return PLATFORMS[self.platform]["source"]


PLATFORMS: dict[str, dict[str, Any]] = {
    "greenhouse": {
        "api": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        "public": "https://boards.greenhouse.io/{token}",
        "source": SourceType.GREENHOUSE,
        "hosts": ("greenhouse.io", "job-boards.greenhouse.io", "boards.greenhouse.io"),
    },
    "lever": {
        "api": "https://api.lever.co/v0/postings/{token}?mode=json",
        "public": "https://jobs.lever.co/{token}",
        "source": SourceType.LEVER,
        "hosts": ("lever.co", "jobs.lever.co"),
    },
    "ashby": {
        "api": "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
        "public": "https://jobs.ashbyhq.com/{token}",
        "source": SourceType.ASHBY,
        "hosts": ("ashbyhq.com", "jobs.ashbyhq.com"),
    },
}


def candidate_tokens(company: str) -> list[str]:
    """Plausible board slugs for a company name, most likely first.

    Every one of these is a *guess to be tested*, never an answer: the provider
    only uses a slug the live API confirms.
    """
    base = _LEGAL_SUFFIX.sub(" ", company or "")
    normalized = basic_normalize(base)
    if not normalized:
        return []
    words = normalized.split()
    out = [
        "".join(words),
        "-".join(words),
        words[0] if words else "",
    ]
    seen: set[str] = set()
    tokens: list[str] = []
    for token in out:
        token = token.strip("-")
        if token and len(token) >= 2 and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens[:3]


@lru_cache(maxsize=1)
def load_seed_boards() -> list[Board]:
    """Known board identifiers, from the configured JSON file.

    The shipped file is a starting point, not an authority: every entry is
    still probed live before use, and users are expected to edit it to their
    own target companies.
    """
    path = Path(settings.company_boards_file or DEFAULT_BOARDS_FILE)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("boards.file_unreadable", path=str(path), error=str(exc)[:200])
        return []
    entries = data.get("boards") if isinstance(data, dict) else data
    boards: list[Board] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        platform = str(entry.get("platform") or "").lower()
        token = str(entry.get("token") or "").strip()
        company = str(entry.get("company") or token).strip()
        if platform in PLATFORMS and token:
            boards.append(Board(platform=platform, token=token, company=company))
    return boards


def reset_seed_boards_cache() -> None:
    load_seed_boards.cache_clear()


class CompanyBoardProvider(JobProvider):
    """Searches companies' own Greenhouse / Lever / Ashby boards."""

    name = "company_boards"
    label = "Company job boards"
    source = SourceType.GREENHOUSE  # per-job source is set from the board
    coverage = (
        "Greenhouse, Lever and Ashby boards belonging to the companies you name, "
        "plus the boards listed in your company board file. No key required."
    )

    #: How many companies to probe in one search. Each costs up to 3 requests.
    MAX_COMPANIES = 8

    def __init__(self, client: Any, *, known_boards: dict[str, Board] | None = None) -> None:
        super().__init__(client)
        # company (normalized) -> already-confirmed board, from the database.
        self.known_boards = known_boards or {}
        self.confirmed: dict[str, Board] = {}

    def supports(self, query: JobQuery) -> tuple[bool, str | None]:
        if not query.companies and not load_seed_boards():
            return False, (
                "no companies named in the search and no company board file configured"
            )
        return True, None

    # -- board resolution ----------------------------------------------------
    def resolve_board(self, company: str) -> Board | None:
        """Find the live board for ``company``, or ``None``.

        Confirmation means the platform's API answered with a job list. A 404,
        an empty body or an unparseable response all mean "not found".
        """
        key = normalize_company_name(company)
        cached = self.known_boards.get(key)
        if cached is not None:
            return cached

        for token in candidate_tokens(company):
            for platform in ("greenhouse", "lever", "ashby"):
                board = Board(platform=platform, token=token, company=company)
                try:
                    payload = self.fetch_json(board.api_url)
                except FetchBlocked as exc:
                    log.debug("board.blocked", company=company, platform=platform, reason=exc.reason)
                    continue
                except Exception:  # noqa: BLE001, S112 - a 404 here means "no board", not an error
                    continue
                if _extract_board_jobs(platform, payload):
                    self.confirmed[key] = board
                    self.result.notes.append(
                        f"Confirmed {company} publishes a {platform.title()} board ({board.token})."
                    )
                    return board
        self.result.notes.append(f"No public Greenhouse/Lever/Ashby board found for {company}.")
        return None

    # -- search --------------------------------------------------------------
    def _target_boards(self, query: JobQuery) -> list[Board]:
        boards: list[Board] = []
        seen: set[tuple[str, str]] = set()

        for company in query.companies[: self.MAX_COMPANIES]:
            board = self.resolve_board(company)
            if board and (board.platform, board.token) not in seen:
                seen.add((board.platform, board.token))
                boards.append(board)

        if len(boards) < self.MAX_COMPANIES:
            wanted = {normalize_company_name(c) for c in query.companies}
            seeds = load_seed_boards()
            # When the search names companies, only seeds matching them are
            # worth the requests; otherwise take the file in order.
            ordered = [b for b in seeds if normalize_company_name(b.company) in wanted] or (
                [] if query.companies else seeds
            )
            for board in ordered:
                if (board.platform, board.token) in seen:
                    continue
                seen.add((board.platform, board.token))
                boards.append(board)
                if len(boards) >= self.MAX_COMPANIES:
                    break
        return boards

    def search(self, query: JobQuery) -> list[RawJob]:
        out: list[RawJob] = []
        for board in self._target_boards(query):
            if self.client.budget_exhausted:
                self.result.notes.append("Stopped early: request budget for this search is spent.")
                break
            try:
                payload = self.fetch_json(board.api_url)
            except FetchBlocked as exc:
                self.result.errors.append(f"{board.company} board declined the request: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                self.result.errors.append(
                    f"{board.company} board could not be read: {type(exc).__name__}"
                )
                continue
            jobs = _extract_board_jobs(board.platform, payload)
            for item in jobs:
                job = _board_job_to_raw(board, item, board.api_url)
                if job is not None:
                    out.append(job)
        return out


# --- Per-platform payload readers --------------------------------------------


def _extract_board_jobs(platform: str, payload: Any) -> list[dict[str, Any]]:
    if platform == "greenhouse":
        items = payload.get("jobs") if isinstance(payload, dict) else None
    elif platform == "lever":
        items = payload if isinstance(payload, list) else None
    elif platform == "ashby":
        items = payload.get("jobs") if isinstance(payload, dict) else None
    else:
        items = None
    return [i for i in items or [] if isinstance(i, dict)]


def _board_job_to_raw(board: Board, item: dict[str, Any], query_url: str) -> RawJob | None:
    if board.platform == "greenhouse":
        location = item.get("location")
        location_name = location.get("name") if isinstance(location, dict) else location
        return RawJob(
            title=str(item.get("title") or ""),
            company_name=board.company,
            url=str(item.get("absolute_url") or ""),
            source=board.source,
            source_query_url=query_url,
            external_id=str(item.get("id")) if item.get("id") is not None else None,
            location=str(location_name) if location_name else None,
            is_remote=JobProvider.looks_remote(str(location_name or ""), item.get("title")),
            department=", ".join(
                str(d.get("name"))
                for d in item.get("departments") or []
                if isinstance(d, dict) and d.get("name")
            )
            or None,
            description=html_to_text(item.get("content")),
            posted_at=parse_datetime(item.get("first_published") or item.get("updated_at")),
            payload={"board": board.token, "platform": board.platform},
        )

    if board.platform == "lever":
        categories = item.get("categories") or {}
        categories = categories if isinstance(categories, dict) else {}
        return RawJob(
            title=str(item.get("text") or ""),
            company_name=board.company,
            url=str(item.get("hostedUrl") or item.get("applyUrl") or ""),
            source=board.source,
            source_query_url=query_url,
            external_id=str(item.get("id")) if item.get("id") else None,
            apply_url=item.get("applyUrl"),
            location=categories.get("location"),
            is_remote=JobProvider.looks_remote(
                str(categories.get("location") or ""), str(categories.get("commitment") or "")
            ),
            employment_type=categories.get("commitment"),
            department=categories.get("team"),
            description=item.get("descriptionPlain") or html_to_text(item.get("description")),
            posted_at=parse_datetime(item.get("createdAt")),
            payload={"board": board.token, "platform": board.platform},
        )

    if board.platform == "ashby":
        return RawJob(
            title=str(item.get("title") or ""),
            company_name=board.company,
            url=str(item.get("jobUrl") or item.get("applyUrl") or ""),
            source=board.source,
            source_query_url=query_url,
            external_id=str(item.get("id")) if item.get("id") else None,
            apply_url=item.get("applyUrl"),
            location=item.get("location"),
            is_remote=bool(item.get("isRemote")),
            employment_type=item.get("employmentType"),
            department=item.get("department") or item.get("team"),
            description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml")),
            posted_at=parse_datetime(item.get("publishedAt") or item.get("updatedAt")),
            payload={"board": board.token, "platform": board.platform},
        )
    return None


def board_host_matches(url: str, token: str) -> bool:
    """True when ``url`` is on an ATS host and carries the board token."""
    lowered = url.lower()
    for config in PLATFORMS.values():
        if any(host in lowered for host in config["hosts"]):
            return token.lower() in lowered
    return False
