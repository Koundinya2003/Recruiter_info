# Job sources

Seven providers. Each is a documented public endpoint whose purpose is to let
third parties search or render postings — no scraping around a login, no session
replay, no bypass.

A provider that cannot run says so. `ProviderResult.skipped_reason` is recorded
on the search run and shown in the UI, because *"Adzuna needs credentials"* is
useful and silently returning fewer results is not.

## The providers

| Provider | Endpoint | Credentials | Notes |
|---|---|---|---|
| `company_boards` | Greenhouse / Lever / Ashby public APIs | None | First-party. Slugs probed, never assumed |
| `adzuna` | `api.adzuna.com/v1/api/jobs/{country}/search/1` | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | 19 countries incl. India. Best non-remote breadth |
| `themuse` | `themuse.com/api/public/jobs` | Optional `THE_MUSE_API_KEY` | No server-side search: titles filtered locally |
| `remotive` | `remotive.com/api/remote-jobs` | None | Remote only — skipped when a search names cities |
| `arbeitnow` | `arbeitnow.com/api/job-board-api` | None | No server-side search; paged and filtered locally |
| `jobicy` | `jobicy.com/api/v2/remote-jobs` | None | Remote only |
| `usajobs` | `data.usajobs.gov/api/search` | `USAJOBS_API_KEY`, `USAJOBS_USER_AGENT` | US federal only |

## Company job boards

The best source here, and the one worth understanding.

Greenhouse, Lever and Ashby each publish an unauthenticated JSON endpoint whose
entire purpose is to let anyone render a company's open roles. A posting that
comes back is, by definition, one the employer is advertising right now — better
evidence of liveness than fetching the HTML page afterwards. That is why
first-party postings survive validation even when the posting page itself blocks
automated access: `SourceType.is_first_party` records the distinction.

**Board identifiers are never assumed.** `candidate_tokens("Razorpay Software
Private Limited")` yields `["razorpay"]` — a *guess to be tested*. Each candidate
is probed against the live API and accepted only when the endpoint answers with
postings. A wrong guess produces a note:

> No public Greenhouse/Lever/Ashby board found for Zzzz Corp.

A confirmed board is written to `Company.careers_url`, so later searches go
straight to it.

### The seed list

When a search names no companies, the boards in
[`app/data/company_boards.json`](../app/data/company_boards.json) are used:

```json
{"company": "Stripe", "platform": "greenhouse", "token": "stripe"}
```

That file is **a starting point, not an authority**. Every entry is still probed
before use, and any the API does not recognise is skipped with a note. Edit it
to your own target companies, or point `COMPANY_BOARDS_FILE` at your own file.

To find a token, open a company's careers page and read the URL:
`boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`,
`jobs.ashbyhq.com/<token>`.

Three consecutive board failures stop the pass — several public APIs failing in
a row is a connectivity problem, not a run of missing boards, and reporting it
once is more useful than reporting it eight times.

## Adding a provider

```python
class MyBoardProvider(JobProvider):
    name = "myboard"
    label = "My Board"
    source = SourceType.MY_BOARD          # add to SourceType first
    coverage = "What this source actually covers."
    required_settings = ("myboard_api_key",)   # omit if none needed
    signup_url = "https://example.com/api"

    def supports(self, query: JobQuery) -> tuple[bool, str | None]:
        if query.countries and "us" not in query.countries:
            return False, "covers United States postings only"
        return True, None

    def search(self, query: JobQuery) -> list[RawJob]:
        payload = self.fetch_json(f"{self.BASE}?q={quote(query.primary_title)}")
        return [self._to_job(item, self.BASE) for item in payload["results"]]
```

Then add it to `PROVIDER_CLASSES` in `app/providers/registry.py`, and any
settings to `app/config.py` and `.env.example`.

Three rules for `search`:

1. **Return only what the source returned.** No filling in a missing company
   name, no defaulting an experience range, no constructing a URL the payload
   did not contain. A field the source omitted stays `None`.
2. **Let it raise.** `JobProvider.run` catches `FetchBlocked` and everything else
   and records it. Do not swallow errors into an empty list — that turns a
   broken source into "no jobs matched".
3. **Fetch through `self.fetch_json`**, so the URL is recorded and the request
   goes through the guarded client.

### Testing it

Point the provider's `BASE` at the mock server and assert the mapping —
that is the layer where a mistake silently mislabels real data:

```python
def test_parses_results(self, client, mock_site, monkeypatch):
    monkeypatch.setattr(MyBoardProvider, "BASE", mock_site.url("/api/myboard"))
    result = MyBoardProvider(client).run(QUERY)
    assert result.jobs[0].company_name == "Demo Fintech"
    assert result.jobs[0].source is SourceType.MY_BOARD
```

Add the payload to `tests/fixtures/mock_server.py` using the source's **real**
documented response shape. A fixture that does not match the real API tests
nothing.
