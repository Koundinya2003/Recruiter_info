# Data model

Nine tables. Every one exists to serve a step of
*search → validate → contact → apply → track*.

```
User ──┬── UserProfile              search defaults
       ├── JobSearch ── SearchRun    what you asked for, and each execution
       ├── Job ──┬── JobContact ── Contact ── Company
       └── Application ── ApplicationEvent
```

## Tables

### `users`, `user_profile`
A single local owner, created on first request. The profile holds search
defaults only — target titles, locations, skills. This is a search workspace,
not a résumé manager.

### `job_searches`
What the user typed (`raw_query`, kept verbatim so the parse can be re-run) plus
the criteria parsed out of it. `parse_method` records whether a model was
involved, and `parse_notes` carries anything the parser wants to tell the user
— both surfaced in the UI so a misreading can be corrected rather than guessed at.

### `search_runs`
One execution. Append-only. Carries the whole funnel:

```
raw_found → duplicates_dropped → irrelevant_dropped → rejected
                                                    → validated + unverified
```

plus `providers_queried`, `providers_skipped` (name → reason), `errors` and
`notes`. This is what lets the app say *"41 retrieved, 12 duplicates, 21
off-target, 4 failed validation, 4 shown"* instead of just showing four rows.

### `companies`
Derived from postings, not curated. Unique on `normalized_name`.

- `domain` — confirmed by following a posting URL, never guessed from the name,
  and never an IP address or a job board's own host.
- `careers_url` — a confirmed Greenhouse/Lever/Ashby board, so later searches
  skip the probing.
- `contacts_checked_at` — when discovery last ran, so repeat searches do not
  re-crawl the same pages.

### `jobs`
One discovered posting. Nothing on this table is synthesised: an unknown field
stays NULL rather than being filled with a plausible value.

| Group | Columns |
|---|---|
| Identity | `canonical_url` (unique per user), `fingerprint`, `external_id` |
| Content | `title`, `company_name`, `location`, `is_remote`, `department`, `salary_text`, `description`, `summary` |
| Experience | `experience_text` (the phrase as printed), `min_years`, `max_years` — all NULL when the posting is silent |
| Provenance | `source`, `source_query_url`, `job_url`, `apply_url`, `final_url`, `posted_at`, `discovered_at` |
| Validation | `validation_status`, `validation_checks` (per-check outcome), `validation_reason`, `validated_at`, `http_status` |
| Relevance | `relevance_score`, `relevance_breakdown`, `match_reasons` |
| User state | `is_saved`, `is_dismissed` |

Two independent dedupe keys: `uq_job_user_canonical_url` and the
`(user_id, fingerprint)` index over (company, sorted title tokens, canonical city).

### `contacts`
A person, a published inbox, or a directory search link — `role` says which, and
`Contact.is_person` is the guard the UI uses.

- `name` is **NULL** for an inbox or a search link. Giving either a person's name
  would be the exact failure this product is built to avoid.
- `dedupe_key` is name-first, then address. The same person is often found twice
  — once in structured data with an address, once in visible text without — and
  keying on the address would list them twice.
- `email_status` ∈ `NONE | PUBLISHED_ATTRIBUTED | PUBLISHED_TEAM_ALIAS |
  USER_PROVIDED`. There is deliberately **no value for a guessed address**.
- `source`, `source_url` and `source_excerpt` answer *"where did this come
  from?"* for every row.

### `job_contacts`
Which contacts were surfaced for which job, with `rank` (0 is best) and the
`rationale` shown under the contact.

### `applications`
The tracker, and the one table that must survive everything else.

`job_id` and `contact_id` are `ON DELETE SET NULL`, and the row carries its own
copy of `job_title`, `company_name`, `location`, `job_url`, `source`,
`contact_name`, `contact_title`, `contact_email` and `contact_profile_url`.

That duplication is the point. A posting comes down without notice, and that is
precisely when you still need to know what you applied to and who you spoke to.

State lives in two independent columns:

- `status`: `SAVED → APPLIED → OUTREACH_SENT → INTERVIEW → REJECTED → OFFER → CLOSED`
- `outreach_status`: `NOT_STARTED → EMAIL_SENT | LINKEDIN_SENT → REPLIED | NO_RESPONSE`

They are separate because contacting someone and applying are separate acts that
happen in either order. Dates (`date_found`, `date_applied`, `outreach_sent_at`)
are stamped by the service when the matching state is set, never inferred later.

### `application_events`
Append-only history: created, status changed, outreach changed, contact set,
fields updated. Never edited, never deleted.

## Enumerations

Stored as `VARCHAR` with `native_enum=False`, so adding a value is an ordinary
migration rather than a PostgreSQL type rewrite.

`SourceType` has no value for a generated or inferred job. `EmailStatus` has no
value for a guessed address. Where a rule can be made structural instead of
procedural, it is.

## Migrations

One migration, `0001_initial`, verified against the ORM metadata:

```bash
alembic upgrade head
python -c "
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.db.session import get_engine
from app.models import Base
import app.models
with get_engine().connect() as c:
    print(compare_metadata(MigrationContext.configure(c), Base.metadata) or 'in sync')"
```
