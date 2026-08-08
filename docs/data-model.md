# Data model

PostgreSQL, SQLAlchemy 2.0 declarative models, Alembic migrations. Sixteen
tables — enough to model the domain honestly, and no more.

Enums are stored as `VARCHAR` with a `CHECK` constraint (`native_enum=False`),
so adding a value is an ordinary migration rather than a PostgreSQL type
rewrite.

## Overview

```
users ──┬── user_profile           (1:1)  résumé context for scoring + drafting
        ├── scoring_configs        (1:N)  editable weights
        ├── taxonomy_terms         (1:N)  editable role/industry/skill taxonomy
        ├── companies              (1:N)
        └── outreach_leads         (1:N)

companies ──┬── jobs               (1:N)
            ├── recruiters         (1:N)
            ├── hiring_signals     (1:N)
            └── crawl_runs         (1:N)

jobs ──┬── job_recruiter_relationships ──┬── recruiters
       └── hiring_signals  (nullable)    │
                                         ├── contacts         (1:N)
                                         └── email_verifications (1:N)

outreach_leads ── outreach_events  (1:N, append-only)
crawl_runs ── source_records       (1:N, raw audit trail)
```

## Tables

### `users`, `user_profile`

One owner per installation. `user_profile` holds everything the AI drafter is
permitted to draw on: name, headline, education, experience, `years_experience`,
and JSONB lists for skills, target roles, target industries and preferred
locations, plus portfolio/GitHub/LinkedIn URLs and résumé text.

Personal data lives here, in rows — never hardcoded into application logic.

### `companies`

`company_name`, `normalized_name`, `company_domain`, `career_page_url`,
`industry`, `priority`, `active`, `is_demo`, `created_at`, `last_checked_at`,
`last_scan_status`, plus a cached `hiring_activity_score` and when it was
computed.

* `UNIQUE (user_id, normalized_name)` — the same company cannot be tracked twice
  under cosmetic name variations (`Acme Inc.` vs `Acme`).

### `jobs`

`title`, `normalized_title`, `description`, `location`, `normalized_location`,
`employment_type`, `job_url`, `canonical_url`, `content_hash`, `source`,
`source_job_id`, `posted_at`, `discovered_at`, `last_seen_at`, `status`,
`is_demo`, `relevance_score`, `relevance_breakdown` (JSONB), `relevance_scored_at`.

* `UNIQUE (company_id, canonical_url)` — URL-level dedupe.
* `INDEX (company_id, content_hash)` — content-level dedupe across sources.
* `content_hash = sha256(normalised company | title | location)`, excluding the
  URL on purpose, so one posting seen through two sources is one row.
* `relevance_breakdown` stores the full explanation, not just the number, so the
  UI can show why without recomputing.

### `recruiters`

`name`, `normalized_name`, `company_name`, `title`, `professional_profile_url`,
`public_professional_email`, `email_source_url`, `email_source_type`,
`email_confidence`, `email_confidence_score`, `email_verified`,
`email_verification_status`, `email_verified_at`, `role_relevance`,
`relevance_score`, `relevance_breakdown`, `do_not_contact`,
`do_not_contact_reason`, `discovered_at`, `last_seen`, `is_demo`.

* `UNIQUE (company_id, normalized_name)`.
* `INDEX (do_not_contact)` — the recommendation engine filters on it in SQL.
* The email fields are denormalised from `contacts` for query convenience; the
  authoritative provenance is always the `contacts` row.

### `contacts`

One row per discovered contact detail, **with its provenance**: `contact_type`,
`value`, `source_url`, `source_type`, `source_excerpt`, `confidence`,
`is_inferred`, `is_primary`, `first_seen`, `last_seen`.

* `UNIQUE (recruiter_id, contact_type, value)`.
* This table is why the product can always answer "where did this come from?".
  An address with `is_inferred = true` has no `source_url` — by definition,
  nothing published it.

### `job_recruiter_relationships`

`job_id`, `recruiter_id`, `relation` (`POSTED_BY`, `COMPANY_TALENT_TEAM`,
`FUNCTION_MATCH`, `LISTED_CONTACT`), `confidence`, `rationale`.

* `UNIQUE (job_id, recruiter_id)`.
* The `rationale` is a sentence shown in the UI. We never assert a connection we
  cannot express in words.

### `hiring_signals`

`signal_type`, `points`, `description`, `details` (JSONB), `detected_at`,
optional `job_id`. These are the evidence behind a company's hiring activity
score, and they drive the company timeline.

### `outreach_leads`

The queue. `status`, `outreach_priority`, `priority_band`, `priority_breakdown`,
draft fields (`draft_subject`, `draft_body`, `draft_provider`, `draft_model`,
`draft_generated_at`, `draft_edited_at`, `draft_approved`, `draft_approved_at`),
and outreach records (`contacted_at`, `last_contact_at`, `contact_count`,
`response_status`, `responded_at`, `notes`).

* **`UNIQUE (recruiter_id, job_id)`** — the database-level guarantee that the
  same person cannot be queued twice for the same role.
* `contacted_at` is written once and never overwritten; follow-ups increment
  `contact_count` and move `last_contact_at`.

### `outreach_events`

Append-only history: `event_type`, `from_status`, `to_status`, `note`, `payload`
(JSONB), `actor`, `created_at`. Never updated, never deleted. This is the
"complete history" the product promises.

### `email_verifications`

One row per check: `email`, `status`, `confidence`, `provider`, `reason`,
`raw_response`, `checked_at`. Keeping every check (rather than only the latest)
means you can see an address degrade over time.

### `crawl_runs`

`collector`, `source`, `target_url`, `trigger`, `started_at`, `completed_at`,
`status`, `records_found`, `records_added`, `records_updated`,
`records_rejected`, `pages_fetched`, `bytes_downloaded`, `rate_limit_waits`,
`rate_limit_seconds`, `error_count`, `errors` (JSONB), `notes`.

The row is inserted **before** the first network call. A crawl that dies leaves
a `RUNNING` or `FAILED` row; it can never vanish.

### `source_records`

Every raw item observed during a crawl — including the ones that were rejected
and **why** (`accepted`, `reject_reason`, `raw_payload`). This is what makes the
Admin page able to show "we saw 12 items, kept 9, and here is why the other 3
were dropped".

### `scoring_configs`, `taxonomy_terms`

User-editable configuration. `scoring_configs` holds four JSONB weight maps plus
an options map (thresholds and time windows). `taxonomy_terms` holds
`(kind, term, aliases, weight, is_primary, active)` with
`UNIQUE (user_id, kind, term)`.

Kinds: `ROLE`, `INDUSTRY`, `SKILL`, `LOCATION`, `SENIORITY`, `EXCLUDE`.

## Key enums

| Enum | Values |
| --- | --- |
| `CompanyPriority` | CRITICAL, HIGH, MEDIUM, LOW |
| `JobStatus` | OPEN, CLOSED, STALE, UNKNOWN |
| `EmailConfidence` | HIGH, MEDIUM, LOW (inferred), NONE |
| `VerificationStatus` | valid, invalid, risky, unknown, not_checked |
| `OutreachStatus` | NEW, REVIEWED, APPROVED, CONTACTED, REPLIED, FOLLOW_UP, ARCHIVED, DO_NOT_CONTACT |
| `SourceType` | CAREER_PAGE, CAREER_PAGE_JSONLD, GREENHOUSE/LEVER/ASHBY_PUBLIC_API, PUBLIC_JOB_FEED, COMPANY_TEAM_PAGE, JOB_POSTING_CONTACT, PATTERN_INFERENCE, MANUAL_ENTRY, DEMO_SEED |
| `CrawlStatus` | RUNNING, SUCCESS, PARTIAL, BLOCKED, FAILED, SKIPPED |

## Deletion behaviour

Deleting a company cascades to its jobs, recruiters, contacts, signals, links
and crawl runs. Leads are deleted explicitly first so the cascade cannot orphan
them. Leads themselves are **archived rather than deleted** in the UI — the
outreach history is the most valuable data in the system.

## Migrations

```bash
alembic upgrade head
alembic check                                    # asserted by the test suite
alembic revision --autogenerate -m "add x"
```

`migrations/env.py` reads `DATABASE_URL` from application settings, so no
credential is ever written into `alembic.ini`.
