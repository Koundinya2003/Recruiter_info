# Development

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

createdb -O roi roi
createdb -O roi roi_test

cp .env.example .env          # set DATABASE_URL
alembic upgrade head
```

Two processes:

```bash
uvicorn app.main:app --reload            # API   :8000  (docs at /docs)
streamlit run frontend/Dashboard.py      # UI    :8501
```

There is no demo seeder, deliberately. Seeding would mean writing fabricated
jobs, companies and recruiters into the database — exactly what this product
exists not to do. Run a real search instead; several sources need no
credentials.

## Quality gates

Run all four before committing:

```bash
ruff check .            # lint, including the bandit (S) ruleset
mypy app                # type check
pytest                  # 236 tests
alembic upgrade head    # then compare against ORM metadata (see below)
```

Checking the migration still matches the models:

```bash
python -c "
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.db.session import get_engine
from app.models import Base
import app.models
with get_engine().connect() as c:
    print(compare_metadata(MigrationContext.configure(c), Base.metadata) or 'in sync')"
```

## Test layout

```
tests/
├── conftest.py                  DB fixtures, mock site, TestClient
├── fixtures/mock_server.py      stands in for every job source and career site
├── unit/
│   ├── test_query_parsing.py    experience, gazetteer, the NL parser
│   ├── test_relevance.py        scoring and the hard gates
│   └── test_dedupe.py           canonical URLs and fingerprints
├── integration/
│   ├── test_providers.py        every provider's payload mapping
│   ├── test_validation.py       valid / expired / broken / mismatch / unconfirmed
│   ├── test_contacts.py         discovery, provenance, ranking, budgets
│   ├── test_pipeline.py         the funnel end to end
│   ├── test_tracker.py          statuses, dates, follow-ups, counts
│   └── test_api_flow.py         the whole workflow through the API
└── security/                    SSRF, injection, auth, secret leakage
```

**The suite never touches a real website.** `tests/fixtures/mock_server.py` runs
a local HTTP server serving the documented response shape of every source, plus
the posting states that matter: live, closed, expired, wrong company, 404,
robots-disallowed and 403. Provider tests point a provider's `BASE` at it with
`monkeypatch`.

Tests needing the database are marked `requires_db` and skip cleanly when
PostgreSQL is not reachable, so `pytest tests/unit` always runs.

## Adding a job source

See [sources.md](sources.md) — the contract, the three rules for `search()`, and
how to test the mapping.

## Adding a place or a role word

`app/search/gazetteer.py`. Add a city to `CITIES` as
`"alias": ("Display Name", "country_code")` — several aliases may map to one
display name, which is how "Bengaluru" and "Bangalore" dedupe to one location.
Role abbreviations go in `ROLE_ABBREVIATIONS`, close variants worth also
searching in `TITLE_EXPANSIONS`.

An unrecognised word is left in the role title rather than guessed at. That is
the safe failure mode: it narrows the search instead of silently changing it.

## Changing how strict the search is

| Setting | Effect |
|---|---|
| `SEARCH_MIN_RELEVANCE` | Raise it for fewer, closer matches; lower it to see more near-misses |
| `VALIDATION_MAX_JOBS` | Postings put through a live check per search. Each costs a request and a politeness delay |
| `CONTACTS_MAX_COMPANIES` | Companies crawled for contacts per search |
| `CRAWLER_DOMAIN_DELAY_SECONDS` | Seconds between requests to one domain. Do not set this to 0 outside tests |

The hard gates in `app/search/relevance.py` are not tuned by settings. A
constraint the user stated outright is not a weight to trade off, and
`GATE_SCORE` sits below every sane threshold on purpose.

## Migrations

```bash
alembic revision --autogenerate -m "add whatever"
alembic upgrade head
```

Read the generated file before committing it — autogenerate misses server
defaults and index flags, and `native_enum=False` columns are plain `VARCHAR`,
so adding an enum value needs no migration at all.

## Conventions

- The API is the whole product surface. The frontend holds no business logic and
  no database access.
- A field the source did not give stays `None`. Never fill a gap with a
  plausible value — "Not stated" is a real answer and the UI shows it.
- Every discovered record carries where it came from. If you cannot cite it, do
  not store it.
- A check that could not be completed is its own outcome, distinct from a pass
  and from a failure.
