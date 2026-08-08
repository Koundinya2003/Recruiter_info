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
python scripts/seed_demo.py   # optional, gives you something to look at
```

Two processes:

```bash
uvicorn app.main:app --reload            # API   :8000
streamlit run frontend/Dashboard.py      # UI    :8501
```

## Quality gates

Run all four before committing:

```bash
ruff check app/ tests/ scripts/ frontend/   # lint (includes bandit rules)
mypy app/                                    # type check
pytest                                       # 257 tests
alembic check                                # models and migrations agree
```

## Test layout

```
tests/
├── conftest.py            DB fixtures, mock site, TestClient
├── fixtures/mock_server.py  a local stand-in for a real career site
├── unit/                  normalisation, dedupe, scoring, state machine,
│                          verification providers, AI guardrails
├── integration/           collectors, ingest flow, demo safety, API workflow
└── security/              SSRF, injection, auth, input validation, secrets
```

Useful invocations:

```bash
pytest tests/unit -q                      # fast
pytest tests/security -v                  # the ones that must never regress
pytest -k "dedup or duplicate"            # by name
pytest --lf                               # last failed
TEST_DATABASE_URL=postgresql+psycopg2://user:pass@host/db pytest
```

### The offline rule

**No test may contact a real website.** `tests/fixtures/mock_server.py` serves
canned Greenhouse and Lever JSON, a JSON-LD career page, a team page with mixed
contact types, plus `robots.txt`, a 403 endpoint, a 500 endpoint and a redirect.

It is a `ThreadingHTTPServer` on purpose: collectors hold keep-alive connections
open (one for `robots.txt`, one for the page), which deadlocks a
single-connection server.

Tests that need the mock site construct the client explicitly:

```python
SafeHTTPClient(allow_private=True, max_pages=40, max_retries=0)
```

`allow_private` is opt-in per client so the global setting stays `False` and the
SSRF tests keep exercising the real production policy.

If PostgreSQL is unavailable, database-backed tests skip rather than fail, via
the `requires_db` marker in `conftest.py`.

## Adding a feature

### A new collector

1. Subclass `BaseCollector`, or `JobCollectorMixin` for job sources (it already
   implements normalise/validate/dedupe for postings).
2. Implement `collect()` using `self.client.fetch(url)`. Never call `httpx`
   directly — that is what keeps SSRF, robots and rate limiting universal.
3. Register it in `collectors/registry.py`.
4. Add it to `is_authoritative_source()` only if the source lists *all* current
   openings.
5. Add fixtures to the mock server and a test in
   `tests/integration/test_collectors.py`.

### A new scoring component

1. Add the weight to the relevant `DEFAULT_*_WEIGHTS` in
   `services/scoring/weights.py`.
2. Add a `result.add(key, label, points, max_points, reasons)` call in the
   scorer. **Always pass reasons** — a component with no reasons is a bug.
3. Expose it on the Settings page if it should be user-tunable.
4. Add a test asserting both the number and the explanation.

### A new API endpoint

1. Schemas in `app/schemas/`, with validators for anything user-supplied.
2. Route in `app/api/routes/`, depending on `db_session` and `current_user`.
3. Business logic in `app/services/` — routes stay thin, and rules that must not
   be bypassable belong in a service.
4. Client method in `frontend/api_client.py`.
5. Tests in `tests/integration/test_api_flow.py`.

### A migration

```bash
alembic revision --autogenerate -m "add x to y"
# read the generated file; autogenerate is a first draft, not an oracle
alembic upgrade head
alembic check
```

## Conventions

* Timezone-aware UTC everywhere via `app.db.base.utcnow()`. Never
  `datetime.utcnow()`.
* Enums are `StrEnum` in `app/models/enums.py`, persisted as VARCHAR + CHECK.
* Structured logging: `log.info("event.name", key=value)`, never f-strings into
  log messages.
* Collectors never raise out of `run()`; failures become data on the outcome.
* Comments explain *why*. The code already says what.

## Debugging

**Scan found nothing.** Open the Admin page — the crawl run's `notes` say why
(client-side rendering, robots disallow, no career page URL). `BLOCKED` means
the site declined us, which is a correct outcome, not a bug to route around.

**Scores look wrong.** Open any job's "Why?" panel: it shows each component,
its points, and the reason. If the taxonomy is the problem, edit it on the
Settings page and click *Re-score all jobs*.

**A lead will not move.** The state machine refuses illegal transitions with an
explanatory message. `CONTACTED` is deliberately unreachable by hand — use
*record outreach* so it is logged.

**Verification says `invalid` for demo data.** Correct: demo addresses use the
reserved `.example` TLD, which cannot resolve. The verifier is being honest.

## Project layout notes

* `app/main.py` is the composition root — middleware, error handlers, routers.
* `frontend/` holds no business logic; it renders API responses.
* `frontend/components/ui.py` owns the visual vocabulary. In particular,
  `email_badge()` is the single function deciding how an address is presented,
  which is what guarantees a verified public address and an inferred guess can
  never look the same.
* `scripts/seed_demo.py` is the only script that writes data; it refuses any
  address outside `.example`.
