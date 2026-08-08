# Collectors

## The contract

```
BaseCollector
    ├── CareerPageCollector              (career_pages/)
    ├── JobCollectorMixin                (job_sources/)
    │     ├── GreenhouseCollector
    │     ├── LeverCollector
    │     └── AshbyCollector
    └── PublicRecruiterSourceCollector   (public_sources/)
```

Every collector implements four methods:

| Method | Responsibility |
| --- | --- |
| `collect()` | Fetch raw records from the source. May raise — `run()` handles it. |
| `normalize(raw)` | Canonicalise one record, or return `None` to drop it. |
| `deduplicate(records)` | Drop within-run duplicates (default: by `dedupe_key`). |
| `validate(record)` | Return `(is_valid, reason)` — the reason is stored. |

`run()` drives them in order and returns a `CollectionOutcome`:

```python
CollectionOutcome(
    collector, source,
    records=[...],                    # accepted
    rejected=[(record, reason), ...], # and why
    duplicates_dropped=int,
    errors=[CollectorError, ...],
    pages_fetched, bytes_downloaded,
    rate_limit_waits, rate_limit_seconds,
    blocked_reason=str | None,
    notes=[...],                      # human-readable, always populated
)
```

**`run()` never raises.** A network failure, a parse error or a policy refusal
becomes structured data on the outcome. The caller writes that to `crawl_runs`
and `source_records`, which is what makes silent failure impossible.

The `notes` field matters more than it looks: a collector that found nothing is
required to say *why* — "listings are rendered client-side, use the ATS board
URL instead" is far more useful than an empty array.

## The guarded HTTP client

Nothing fetches an external URL except `SafeHTTPClient`. It composes, per
request:

1. **SSRF validation** (`security/url_guard.py`) — scheme allowlist, no embedded
   credentials, blocked internal hostnames, port allowlist, and DNS resolution
   with rejection of loopback / private / link-local / CGNAT / metadata
   addresses. Every redirect hop is validated again separately.
2. **robots.txt** (`security/robots.py`) — cached per origin for an hour. A
   `Disallow` ends the fetch. A `Crawl-delay` larger than ours becomes ours.
   A 404 means "no policy, allowed"; an unreachable or 5xx robots.txt means "we
   cannot determine the policy" and we decline.
3. **Per-domain rate limiting** (`security/rate_limit.py`) — a minimum interval
   between requests to the same domain, enforced by *waiting*. Every wait is
   recorded and shown on the Admin page.
4. **Timeout** — configurable, applied to every request.
5. **Retry with exponential backoff** — 2s, 4s, 8s — on transient failures only
   (network errors, 408, 5xx). Never on 401/403/429.
6. **Response size cap** — enforced while streaming, before the body is
   materialised.
7. **Page budget** — a hard cap on requests per run.

### Blocking is terminal

```python
BLOCKING_STATUSES = {401, 402, 403, 407, 429, 451}
```

These raise `FetchBlocked` immediately. They are not retried, not retried with a
different User-Agent, not routed through a proxy. A site declining automated
access is a decision we honour, and the run is recorded as `BLOCKED` so you can
see it happened.

## Job sources

### Official ATS APIs (preferred)

| Platform | Endpoint | Detected from |
| --- | --- | --- |
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | `boards.greenhouse.io/{token}`, `job-boards.greenhouse.io/{token}`, `?for={token}` |
| Lever | `api.lever.co/v0/postings/{token}?mode=json` | `jobs.lever.co/{token}` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{token}` | `jobs.ashbyhq.com/{token}` |

These are documented, unauthenticated endpoints whose purpose is to let anyone
render a company's open roles. Using them is the legitimate path: one request,
better data than HTML parsing, and no ambiguity about permission.

`detect_ats()` reads the board token straight from the career page URL you
entered, so pasting `https://boards.greenhouse.io/acme` is all the setup needed.

**ATS sources are authoritative** — they list *all* current openings, so a job
that disappears between runs is marked `CLOSED`. Partial sources never close
jobs, because absence there does not mean the role is gone.

### Career pages

`CareerPageCollector` tries three things, best first:

1. **`schema.org/JobPosting` JSON-LD on the career page.** Structured data the
   company published specifically so aggregators can read it. Full fidelity:
   title, description, location, `datePosted`, employment type.
2. **Job-detail links, each fetched for its own JSON-LD.** Budget-limited
   (default 8 pages), restricted to the company's own host or a known ATS host.
3. **Headings and anchor text.** Only when no structured data exists anywhere,
   and recorded under the weaker `CAREER_PAGE` source type so the provenance
   stays honest.

If the page renders its listings purely client-side, the collector reports that
plainly and recommends the ATS board URL. We do not run a headless browser to
defeat a site's rendering choices.

## Recruiter sources

`PublicRecruiterSourceCollector` reads only pages the user supplied — the career
page plus any extra URLs they add. It does **not** probe for `/team`, `/about`
or similar: guessing at URLs a site never advertised is neither polite nor
productive.

It extracts, in order of confidence:

| What | Confidence | Why |
| --- | --- | --- |
| JSON-LD `Person` with a talent `jobTitle` and an `email` | `HIGH` | Published and attributed to a named individual |
| A `mailto:` link next to a person's name | `HIGH` | The link *is* the attribution |
| A shared talent address (`careers@`, `talent@`) | `MEDIUM` | Published by the company, not tied to a person |
| A named person with a talent title but no address | `NONE` | Still useful; no contact claimed |

Hard rules enforced in `validate()`:

* **Personal mailbox domains are never collected** (gmail, outlook, yahoo, …),
  even when published.
* An address whose domain does not belong to the company is rejected.
* A record with neither a title nor a contact detail is rejected as
  unactionable.

## Pattern inference

Separate from collection, opt-in per scan
(`POST /api/companies/{id}/scan {"infer_emails": true}`), in
`services/recruiters/inference.py`.

It prefers to **learn** the format from an address the company actually
published — given `priya.sharma@acme.com` belongs to Priya Sharma, it derives
`{first}.{last}` — rather than assuming one. Results are permanently:

* `EmailConfidence.LOW`, `SourceType.PATTERN_INFERENCE`, `source_url = NULL`;
* excluded from ever setting `email_verified = true`;
* rendered as **"INFERRED — not published"** in the UI;
* accompanied by a stored `source_excerpt` explaining the basis for the guess.

An inferred address never overwrites a published one, regardless of order.

## Crawl tracking

```python
run = start_crawl_run(session, collector=..., source=..., company=...)  # before any I/O
outcome = collector.run()
finish_crawl_run(session, run, outcome, added=..., updated=...)          # always
```

Terminal status mapping:

| Outcome | Status |
| --- | --- |
| Fatal error | `FAILED` |
| `blocked_reason` set | `BLOCKED` |
| Non-fatal errors | `PARTIAL` |
| Clean | `SUCCESS` |

Up to 200 `source_records` per run are stored — accepted and rejected alike,
each with its raw payload and, when rejected, the reason.

## Adding a collector

1. Subclass `BaseCollector` (or `JobCollectorMixin` for jobs, which already
   implements normalise/validate/dedupe for postings).
2. Implement `collect()` using `self.client.fetch(url)` — never `httpx` directly.
3. Register it in `collectors/registry.py`.
4. If the source lists *all* current openings, add it to
   `is_authoritative_source()`.
5. Add fixtures to `tests/fixtures/mock_server.py` and a test in
   `tests/integration/test_collectors.py`. No test may contact a real site.
