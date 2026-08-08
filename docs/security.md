# Security

## Threat model

A single-user local application that (a) fetches URLs the user supplies, (b)
stores third parties' professional contact details, and (c) can be pointed at an
LLM API with a paid key. The risks that follow from that shape:

| Threat | Control |
| --- | --- |
| SSRF via a user-supplied URL | Full URL guard with DNS resolution, applied to every hop |
| SQL injection | ORM parameterisation everywhere; no string-built SQL |
| Secret leakage into git, logs or responses | Env-only config, `.gitignore`, safe error bodies, redacted provider errors |
| Unauthorised API access | Optional API key with constant-time comparison |
| Runaway clients / accidental self-DoS | Per-client API rate limiting, per-domain crawl limiting |
| Harming a third-party site | robots.txt, politeness delays, budgets, terminal handling of blocks |
| Harming a third party personally | Provenance on every contact, no personal mailboxes, `DO_NOT_CONTACT`, approval gate |
| Resource exhaustion from a hostile response | Response size cap enforced while streaming, request timeouts, redirect cap |

## SSRF protection

`app/security/url_guard.py` is the single entry point for URL validation. In
order:

1. **Scheme allowlist** — `http`, `https` only. `file://`, `gopher://`,
   `dict://`, `jar:`, `data:` and `javascript:` are rejected.
2. **No embedded credentials** — `http://user:pass@host` is refused.
3. **Blocked hostnames** — `localhost`, `metadata.google.internal`,
   `instance-data`, and the suffixes `.local`, `.internal`, `.cluster.local`,
   `.localdomain`.
4. **Port allowlist** — 80, 443, 8000, 8080, 8443.
5. **DNS resolution, then per-address checks.** This is the check that actually
   works, because `evil.example` can resolve to `169.254.169.254` as easily as a
   literal can. Every resolved address must be globally routable:
   * rejected: loopback, private, link-local, multicast, reserved, unspecified;
   * rejected explicitly: `100.64.0.0/10` (CGNAT), `198.18.0.0/15`,
     `192.0.0.0/24`, TEST-NET-1/2/3, `240.0.0.0/4`, `64:ff9b::/96`, `100::/64`,
     `2001:db8::/32` — listed by hand so the guard does not depend on stdlib
     version behaviour;
   * rejected: known cloud metadata addresses.
6. **Length and control characters** — 2048 char cap; whitespace, CR and LF are
   rejected, which also prevents request-splitting attempts.

**Redirects are not trusted.** `SafeHTTPClient` sets `follow_redirects=False`
and re-validates every hop against the same guard, so a permitted URL cannot
redirect into internal space.

`CRAWLER_ALLOW_PRIVATE_NETWORKS` exists solely so the test suite can point
collectors at a local mock server. It defaults to `false` and must stay that way
in any real deployment. Tests assert both behaviours.

### Validation happens at the boundary too

Pydantic validators reject unsafe URLs before they reach a service: company
`career_page_url`, profile `portfolio_url` / `github_url` / `linkedin_url`,
recruiter `professional_profile_url` / `email_source_url`, and scan
`extra_recruiter_urls`. The API returns `422` with a specific message rather
than attempting the fetch.

## SQL injection

Every query is built with SQLAlchemy constructs and bound parameters. There is
no string concatenation into SQL anywhere in the codebase, including the
`ILIKE` search paths, where the pattern is passed as a parameter.

`tests/security/test_api_security.py` fires eight classic payloads at every
search and filter endpoint and then asserts the tables still exist and the rows
are intact. A payload stored as a company name is asserted to come back byte for
byte — proof it was treated as data.

## Secrets

* All configuration comes from environment variables via `pydantic-settings`.
* `.env` is git-ignored; `.env.example` ships with **empty** placeholders, and a
  test asserts every key line in it is empty.
* `alembic.ini` deliberately has no `sqlalchemy.url`; migrations read
  `DATABASE_URL` from settings.
* `/api/admin/health` reports *whether* an integration is configured, never the
  value. A test asserts secrets never appear in its response.
* Provider errors are constructed by hand rather than echoing the response body,
  because a 401 body can contain the key. A test asserts the key never appears
  in the raised error.
* Error responses carry a request id and a generic message; tracebacks,
  `psycopg2`/`sqlalchemy` internals, filesystem paths and the database URL never
  reach the client.

## Authentication

Setting `API_KEY` requires `X-API-Key` on every `/api` request, compared with
`hmac.compare_digest`. Leaving it empty runs the app as an unauthenticated local
tool, which is the intended default for a single-user install on localhost.

`/api/admin/health` stays reachable without a key so process monitoring works.

## Rate limiting

Two independent limiters:

* **`FixedWindowRateLimiter`** protects this application's API
  (`API_RATE_LIMIT_PER_MINUTE`, default 240/min per client), returning `429`
  with `Retry-After`.
* **`DomainRateLimiter`** enforces politeness towards sites we crawl by
  *waiting* between requests to the same domain. It honours a site's
  `Crawl-delay` when that is longer than ours. Every wait is recorded and shown
  on the Admin page.

Waiting is the entire mechanism. There is no proxy rotation, no address
cycling, no User-Agent shuffling — none of the things that exist to make a
blocked client look like a different client.

## Input and output validation

* Pydantic models validate every request body and query parameter: bounded
  lengths, enum membership, numeric ranges, `EmailStr` for addresses, regex on
  sort keys.
* Responses are Pydantic models too, so internal fields cannot leak by
  accident.
* Validation failures return `422` with a field-level list, without echoing the
  whole request body.
* `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` and
  `Referrer-Policy: no-referrer` are set on every response. All API output is
  JSON; nothing is interpolated into server-rendered markup.

## Responsible-use controls

Security here includes not harming the people in the database:

* **Provenance is mandatory.** Adding a contact with an address requires a
  source URL. Every `contacts` row stores where the detail came from.
* **Personal mailboxes are never collected**, even when published.
* **Inferred addresses can never be marked verified**, and are visually distinct
  everywhere.
* **`DO_NOT_CONTACT` is absolute** — enforced in the SQL of the recommendation
  engine, blocking lead creation, draft generation, approval and recording.
* **Approval is required.** `CONTACTED` is reachable only from `APPROVED` with
  an approved draft; editing a draft revokes approval.
* **The application never sends email.** There is no SMTP client, no send
  endpoint, no scheduled send. It cannot become a bulk mailer by
  misconfiguration.

## Dependency and operational notes

* Pinned dependencies in `requirements.txt`.
* `ruff` runs with the `S` (bandit) ruleset enabled across the codebase.
* `mypy` passes cleanly over `app/`.
* Structured logging via `structlog`, with a request id bound to every line for
  the life of a request.
* `pool_pre_ping` on the database engine; connections are returned to the pool
  in a `finally` block.

## Running the security tests

```bash
pytest tests/security -v
```

86 tests covering SSRF payloads, malformed URLs, SQL injection, input
validation, auth enforcement and secret leakage.
