# Architecture

## The shape of the problem

The product is one chain, and the architecture mirrors it directly:

```
Company → Hiring signal → Relevant job → Relevant recruiter
        → Public contact → Verification → Priority → Draft → Approval
```

Each arrow is a service with a single job, and the pipeline is orchestrated in
one place (`app/services/scan.py`) so the flow can be read top to bottom.

## Layers

```
┌───────────────────────────────────────────────────────────┐
│ frontend/  Streamlit — a pure HTTP client of the API      │
└───────────────────────────┬───────────────────────────────┘
                            │  JSON over HTTP
┌───────────────────────────▼───────────────────────────────┐
│ app/api/       routes · request/response schemas · deps    │
├───────────────────────────────────────────────────────────┤
│ app/services/  business rules — scoring, ingest, outreach, │
│                verification, AI drafting, crawl tracking   │
├───────────────────────────────────────────────────────────┤
│ app/collectors/  data acquisition, behind one guarded HTTP │
│                  client that enforces every crawl policy   │
├───────────────────────────────────────────────────────────┤
│ app/models/ + app/db/   SQLAlchemy models, session, seed   │
├───────────────────────────────────────────────────────────┤
│ app/security/   SSRF guard · rate limiting · robots.txt    │
└───────────────────────────────────────────────────────────┘
```

Dependencies point strictly downward. A collector never touches the database; a
service never makes an HTTP request except through a collector; a route never
contains a business rule.

## Why these boundaries

**Routes are thin.** Every rule that must not be bypassable lives in a service.
The approval gate, duplicate-outreach prevention and `DO_NOT_CONTACT`
enforcement are in `services/outreach/service.py`, so a new route — or a script,
or a future React frontend — cannot accidentally skip them.

**Collectors return data, never persist it.** `BaseCollector` produces a
`CollectionOutcome` containing accepted records, rejected records *with reasons*,
errors, and counters. The service layer decides what to store. This is what
makes collectors testable offline and makes "the crawler found nothing" a
first-class, reportable outcome instead of an empty list.

**One HTTP client, one policy.** Nothing in the application calls `httpx`
directly for external content except `SafeHTTPClient`. SSRF validation, robots
compliance, rate limiting, timeouts, backoff, budget and size caps are composed
in exactly one place, so they cannot drift apart or be forgotten in a new
collector.

**Scores are structures, not numbers.** Every scorer returns a `ScoreResult`
made of named `ScoreComponent`s that each carry their reasons. It is not
possible to produce a score in this codebase without also producing its
explanation — the "no black box" requirement is enforced by the type, not by
discipline.

**Configuration lives in the database.** Scoring weights (`scoring_configs`) and
the role taxonomy (`taxonomy_terms`) are rows, editable from the Settings page.
Code contains only the defaults used to seed a new account.

## Request flow: "who should I contact today?"

```
GET /api/dashboard
  → api/routes/dashboard.py
      → load_weights(session, user)          scoring_configs
      → build_context(session, user)         taxonomy_terms + user_profile
      → contact_today(...)                   services/scoring/outreach_priority
          ├─ select open jobs above the relevance threshold
          ├─ select recruiters, DO_NOT_CONTACT filtered in the query itself
          ├─ exclude pairs already contacted or archived
          ├─ score_opportunity() for each pair  → ScoreResult with reasons
          ├─ sort by priority, cap each recruiter at two rows
          └─ return the ranked shortlist
      → aggregate counters for the KPI tiles
```

`DO_NOT_CONTACT` is applied as a `WHERE` clause, not a filter after scoring.
Someone who asked not to be contacted should never enter the ranking at all.

## Request flow: a company scan

```
POST /api/companies/{id}/scan
  → services/scan.py :: scan_company
      ├─ build_job_collectors(company)        registry picks ATS API or career page
      │    for each collector:
      │      ├─ start_crawl_run()             row written BEFORE any network call
      │      ├─ collector.run()               collect → normalize → dedupe → validate
      │      ├─ ingest_jobs()                 cross-source dedupe + relevance scoring
      │      └─ finish_crawl_run()            always a terminal status
      ├─ build_recruiter_collectors(company)  only URLs the user supplied
      │      └─ ingest_recruiters()           contacts stored with provenance
      ├─ run_email_inference()                opt-in only; LOW confidence
      ├─ link_recruiters_to_jobs()            with a stored rationale
      ├─ rescore_company_recruiters()
      └─ refresh_company_hiring_activity()    signals persisted to hiring_signals
```

A crawl run is opened before the first request and closed in a `finally`-shaped
path, so a crash leaves a `FAILED` row rather than no row.

## Duplicate detection

Two layers, because the same posting genuinely arrives twice:

1. **Within a run** — `BaseCollector.deduplicate()` keys on a content hash.
2. **Across runs and sources** — `services/jobs/ingest.py` checks, in order:
   canonical URL → content fingerprint → (source, external id) → fuzzy title
   similarity at the same location.

The content fingerprint is `sha256(normalised company + title + location)` and
deliberately **excludes the URL**, which is what lets the same job seen through
Greenhouse and through the company's own page collapse into one record.

## Provider abstractions

Both external integrations are swappable and degrade to a working offline
implementation, so the product never has a hard dependency on a paid service:

| Concern | Interface | Implementations |
| --- | --- | --- |
| Email verification | `EmailVerifier.verify(email) -> VerificationResult` | `dns` (local, default), `null`, `http` (any REST vendor) |
| Text generation | `LLMProvider.complete(messages) -> Completion` | `OpenAICompatibleProvider` (OpenRouter/OpenAI/local), `TemplateProvider` (offline) |

Adding a provider is one subclass plus one registration. Selecting one is one
environment variable.

## Background work

`app/workers/scheduler.py` runs APScheduler in-process, walking active companies
on an interval with the same rate limiting and crawl tracking as a manual scan.
No broker, no worker fleet, no queue — a single-user research tool does not need
that infrastructure, and adding it would be cost without benefit.

## Extending the frontend

The Streamlit UI holds no business logic; it renders API responses and posts
user actions back. `frontend/components/ui.py` owns the visual vocabulary — in
particular `email_badge()`, the single function that decides how an address is
presented, which is what guarantees a verified public address and an inferred
guess can never be rendered identically.

Swapping in React/Next.js means writing a new client against the same 38
endpoints; no backend change is required.
