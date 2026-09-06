# Architecture

One request shapes everything: *find me real, open roles that match what I asked
for, tell me who to contact, and help me keep track.*

```
                    ┌──────────────┐
   "APM roles for   │ search/      │  rules first, a model only fills gaps
   0-2 yrs in       │  parser.py   │  it leaves — never overwrites
   Bangalore"  ───► └──────┬───────┘
                           │  JobQuery
                           ▼
                    ┌──────────────┐
                    │ providers/   │  7 sources. One that needs credentials is
                    │  registry.py │  skipped with its reason recorded.
                    └──────┬───────┘
                           │  RawJob[]
                           ▼
        ┌──────────────────────────────────────┐
        │ services/discovery.py                │
        │                                      │
        │  dedupe ──► score ──► validate ──►   │  every drop is counted
        │                       find contacts  │  and explained
        └──────────────────┬───────────────────┘
                           │
                           ▼
              Job · Contact · JobContact · SearchRun
                           │
                           ▼
                  services/applications.py
                     (the user's tracker)
```

## The pipeline, step by step

`app/services/discovery.py:run_search` is the vertical slice. Its ordering is
deliberate in two places.

**Validation runs after relevance scoring.** Fetching a page costs a request and
a politeness delay, so the budget is spent confirming postings the user might
actually want rather than ones about to be discarded.

**Contacts are found per company, not per job.** Five roles at one employer cost
one crawl, and the result is cached on `Company.contacts_checked_at` so the next
search skips it entirely.

### 1. Read the request — `app/search/`

| Module | Responsibility |
|---|---|
| `gazetteer.py` | Cities, countries, role abbreviations, industries. Hand-curated: an unrecognised word is left in the title, which narrows the search rather than silently changing it |
| `experience.py` | "0–2 years", "3+", "fresher", "Senior". Unstated stays unstated — never a default range |
| `parser.py` | Consumes the most explicitly-marked clauses first (experience, exclusions, keywords, remote, locations, industries, companies); whatever is left is the role title |
| `query.py` | `JobQuery` — what every provider consumes |
| `relevance.py` | Scores a posting against the request |

The parser is rule-based so the product works with no API key. When one is
configured, `refine_with_llm` runs *after* the rules and is constrained three
ways: it may only fill a field the rules left empty (titles are the one
exception — it may add variants); a location it proposes is accepted only if the
gazetteer recognises it; and any failure returns the rule-based query unchanged.

### 2. Query the sources — `app/providers/`

Every provider implements `JobProvider`: declare `required_settings`, answer
`supports(query)`, implement `search(query)`. `JobProvider.run` turns every
failure into a recorded `ProviderResult` — one bad source never ends a search.

`CompanyBoardProvider` is the odd one out and the most valuable. Rather than
trusting a hardcoded slug, it derives candidates from the company name, probes
Greenhouse then Lever then Ashby, and accepts a board only once the live API
answers with postings. A confirmed board is written to `Company.careers_url`, so
later searches skip the probing.

### 3. Dedupe, score, validate — `app/services/`

`validation.py` provides both dedupe keys and the checks:

- `canonicalize_url` strips tracking parameters, case and trailing slashes.
- `duplicate_key` fingerprints (company, sorted title tokens, canonical city),
  which catches the same role syndicated to several boards under different URLs.
- `validate_posting` runs three checks and records each one's outcome
  separately, so "confirmed open" and "the site would not let us look" stay
  distinguishable. A robots.txt disallow or a 403 produces *could not confirm* —
  never a retry in disguise, and never a silent pass.

`relevance.py` applies **hard gates** after weighted scoring. A stated
constraint — the city, the experience range, a named company — is not a weight
to trade off, so violating one caps the score below the display threshold. An
*unknown* value never trips a gate; only a stated value that contradicts the
request.

### 4. Find contacts — `app/services/contacts.py`

Four sources, in descending order of trust: the posting itself, the company's
own pages, a published team inbox, and a directory search link. The first three
require a citable URL; the fourth names nobody by construction.

There is no inference path. `EmailStatus` has no value for a guessed address,
which makes the rule structural rather than a check someone can forget.

### 5. Persist and report — models

A `SearchRun` records the whole funnel: retrieved, duplicates, off-target,
rejected, validated, unverified, plus which providers were queried and why the
others were not. That record is what lets the UI answer *"why only four
results?"* honestly.

## Layers

```
frontend/  Streamlit — one API client among possible others
   │  HTTP
app/api/   routes · deps (auth, session, owner) · serializers
app/services/   discovery · validation · contacts · applications
app/providers/  job sources          app/search/  query understanding
app/models/     SQLAlchemy           app/security/  SSRF · robots · rate limits
app/collectors/http_client.py — the one place anything leaves the process
```

The API is the whole product surface. The Streamlit UI holds no business logic
and no database access, so a different frontend is a drop-in replacement.

## Design decisions worth knowing

**Applications snapshot the job.** `Application` copies the title, company,
location, URL and contact at creation, and `job_id` is `ON DELETE SET NULL`.
Losing a posting must not lose the application — that is exactly when a tracker
is needed most.

**Companies are derived, not curated.** A `Company` row appears because a
validated posting named it. It exists to group contacts and cache a confirmed
board, not as a watchlist to maintain.

**Search history is separable from the job library.** Deleting a `JobSearch`
sets `Job.search_id` to NULL rather than cascading. Tidying your search history
should not throw away the jobs it found.

**Everything fetches through `SafeHTTPClient`.** SSRF validation on every
redirect hop, robots.txt compliance, per-domain politeness, a response size cap,
and retries only on genuinely transient failures. See [security](security.md).
