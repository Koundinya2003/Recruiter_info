"""A local HTTP server serving canned job-source and career-page responses.

The test suite never touches a real website. This server stands in for one, so
provider parsing, posting validation and contact discovery are all exercised
deterministically and offline — including the awkward cases that matter most:
a posting that has been closed, a page that belongs to a different company, and
a path robots.txt puts off limits.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

GREENHOUSE_JOBS: dict[str, Any] = {
    "jobs": [
        {
            "id": 4001,
            "title": "Product Analyst",
            "absolute_url": "http://{host}/jobs/4001",
            "location": {"name": "Bangalore, India"},
            "first_published": "2026-08-07T09:00:00Z",
            "updated_at": "2026-08-07T09:00:00Z",
            "content": "<p>Own product analytics. You will write SQL, run A/B testing "
            "and build dashboards for our payments product.</p>",
            "departments": [{"name": "Product"}],
        },
        {
            "id": 4002,
            "title": "Associate Product Manager",
            "absolute_url": "http://{host}/jobs/4002",
            "location": {"name": "Remote - India"},
            "first_published": "2026-08-06T09:00:00Z",
            "content": "<p>Work with product and engineering on roadmapping and "
            "experimentation for our consumer app.</p>",
            "departments": [{"name": "Product"}],
        },
        {
            "id": 4003,
            "title": "Senior Sales Development Representative",
            "absolute_url": "http://{host}/jobs/4003",
            "location": {"name": "Berlin, Germany"},
            "first_published": "2026-07-01T09:00:00Z",
            "content": "<p>Outbound cold calling.</p>",
            "departments": [{"name": "Sales"}],
        },
        {
            "id": 4004,
            "title": "General Application",
            "absolute_url": "http://{host}/jobs/4004",
            "location": {"name": "Anywhere"},
            "content": "<p>Tell us about yourself.</p>",
        },
    ]
}

LEVER_JOBS: list[dict[str, Any]] = [
    {
        "id": "lev-1",
        "text": "Data Analyst",
        "hostedUrl": "http://{host}/lever/lev-1",
        "categories": {"location": "Mumbai", "team": "Analytics", "commitment": "Full-time"},
        "createdAt": 1785456000000,
        "descriptionPlain": "SQL, dashboarding and product analytics for a fintech platform.",
    }
]

CAREER_PAGE_HTML = """<!doctype html>
<html><head><title>Careers at Demo Fintech</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Product Operations Associate",
  "description": "<p>Run product ops: dashboards, experimentation and stakeholder management.</p>",
  "datePosted": "2026-08-07T08:00:00Z",
  "employmentType": "FULL_TIME",
  "hiringOrganization": {"@type": "Organization", "name": "Demo Fintech"},
  "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress",
      "addressLocality": "Bengaluru", "addressCountry": "IN"}},
  "url": "http://{host}/jobs/jsonld-1"
}
</script></head>
<body>
  <h1>Open roles</h1>
  <a href="/jobs/jsonld-1">Product Operations Associate</a>
</body></html>
"""

TEAM_PAGE_HTML = """<!doctype html>
<html><head><title>Talent team</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Person","name":"Demo Recruiter",
 "jobTitle":"Talent Acquisition Partner, Product",
 "email":"demo.recruiter@demo-fintech.example",
 "url":"http://{host}/team/demo-recruiter"}
</script></head>
<body>
  <h2>Hiring team</h2>
  <div><p>Priya Demo</p><p>Technical Recruiter</p>
    <a href="mailto:priya.demo@demo-fintech.example">Email Priya Demo</a></div>
  <div><a href="mailto:careers@demo-fintech.example">careers@demo-fintech.example</a></div>
  <div><a href="mailto:someone@gmail.com">Personal mailbox</a></div>
</body></html>
"""

# --- Aggregator API payloads -------------------------------------------------

THE_MUSE_JOBS: dict[str, Any] = {
    "page": 0,
    "page_count": 1,
    "results": [
        {
            "id": 9001,
            "name": "Associate Product Manager",
            "type": "external",
            "publication_date": "2026-08-20T09:00:00Z",
            "contents": "<p>Own a slice of the roadmap. 0-2 years of experience.</p>",
            "company": {"id": 11, "name": "Demo Fintech", "short_name": "demo-fintech"},
            "locations": [{"name": "Bangalore, India"}],
            "categories": [{"name": "Product Management"}],
            "levels": [{"name": "Entry Level", "short_name": "entry"}],
            "refs": {"landing_page": "http://{host}/jobs/9001"},
        },
        {
            "id": 9002,
            "name": "Principal Security Engineer",
            "type": "external",
            "publication_date": "2026-08-19T09:00:00Z",
            "contents": "<p>12+ years of experience securing distributed systems.</p>",
            "company": {"id": 12, "name": "Other Corp", "short_name": "other-corp"},
            "locations": [{"name": "Berlin, Germany"}],
            "categories": [{"name": "Engineering"}],
            "levels": [{"name": "Senior Level", "short_name": "senior"}],
            "refs": {"landing_page": "http://{host}/jobs/9002"},
        },
    ],
}

REMOTIVE_JOBS: dict[str, Any] = {
    "job-count": 1,
    "jobs": [
        {
            "id": 7001,
            "url": "http://{host}/jobs/7001",
            "title": "Associate Product Manager",
            "company_name": "Remote Demo Co",
            "category": "Product",
            "job_type": "full_time",
            "publication_date": "2026-08-21T00:00:00",
            "candidate_required_location": "Anywhere",
            "salary": "",
            "description": "<p>Remote APM role. 0-2 years of experience.</p>",
            "tags": ["product"],
        }
    ],
}

ADZUNA_JOBS: dict[str, Any] = {
    "count": 1,
    "results": [
        {
            "id": "6001",
            "created": "2026-08-18T10:00:00Z",
            "title": "Associate Product Manager",
            "description": "Work on payments. 0-2 years of experience required.",
            "redirect_url": "http://{host}/jobs/6001",
            "location": {"display_name": "Hyderabad, Telangana", "area": ["India"]},
            "company": {"display_name": "Demo Fintech"},
            "contract_time": "full_time",
            "category": {"label": "IT Jobs", "tag": "it-jobs"},
        }
    ],
}

ARBEITNOW_JOBS: dict[str, Any] = {
    "data": [
        {
            "slug": "apm-demo",
            "company_name": "EU Demo GmbH",
            "title": "Associate Product Manager",
            "description": "<p>Join our product team. 0-2 years of experience.</p>",
            "remote": True,
            "url": "http://{host}/jobs/5001",
            "tags": ["product"],
            "job_types": ["full_time"],
            "location": "Berlin",
            "created_at": 1787000000,
        }
    ],
    "links": {},
    "meta": {},
}

JOBICY_JOBS: dict[str, Any] = {
    "jobCount": 1,
    "jobs": [
        {
            "id": 3001,
            "url": "http://{host}/jobs/3001",
            "jobTitle": "Associate Product Manager",
            "companyName": "Jobicy Demo",
            "jobIndustry": ["Product"],
            "jobType": ["full-time"],
            "jobGeo": "Anywhere",
            "jobLevel": "Entry",
            "jobExcerpt": "Remote APM role, 0-2 years.",
            "jobDescription": "<p>Remote APM role, 0-2 years of experience.</p>",
            "pubDate": "2026-08-22 10:00:00",
        }
    ],
}

USAJOBS_RESULTS: dict[str, Any] = {
    "SearchResult": {
        "SearchResultItems": [
            {
                "MatchedObjectDescriptor": {
                    "PositionID": "FED-1",
                    "PositionTitle": "Program Analyst",
                    "PositionURI": "http://{host}/jobs/fed-1",
                    "ApplyURI": ["http://{host}/jobs/fed-1/apply"],
                    "OrganizationName": "Demo Agency",
                    "PositionLocation": [{"LocationName": "Washington, DC"}],
                    "PositionSchedule": [{"Name": "Full-Time"}],
                    "QualificationSummary": "0-2 years of experience.",
                    "UserArea": {"Details": {"JobSummary": "Analyse programs."}},
                }
            }
        ]
    }
}

# --- Posting pages -----------------------------------------------------------

LIVE_POSTING_HTML = """<!doctype html>
<html><head><title>Associate Product Manager at Demo Fintech</title>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Associate Product Manager",
  "description": "Own a slice of the roadmap.",
  "datePosted": "2026-08-20T08:00:00Z",
  "validThrough": "2099-01-01T00:00:00Z",
  "hiringOrganization": {"@type": "Organization", "name": "Demo Fintech"},
  "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress",
      "addressLocality": "Bengaluru", "addressCountry": "IN"}}
}
</script></head>
<body><h1>Associate Product Manager</h1><p>Demo Fintech is hiring.</p></body></html>
"""

CLOSED_POSTING_HTML = """<!doctype html>
<html><head><title>Associate Product Manager at Demo Fintech</title></head>
<body><h1>Associate Product Manager</h1>
<p>This job is closed and we are no longer accepting applications.</p>
</body></html>
"""

EXPIRED_POSTING_HTML = """<!doctype html>
<html><head><title>Associate Product Manager</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Associate Product Manager",
 "validThrough":"2020-01-01T00:00:00Z",
 "hiringOrganization":{"@type":"Organization","name":"Demo Fintech"}}
</script></head>
<body><h1>Associate Product Manager</h1></body></html>
"""

WRONG_COMPANY_HTML = """<!doctype html>
<html><head><title>Associate Product Manager</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Associate Product Manager",
 "hiringOrganization":{"@type":"Organization","name":"Completely Different Holdings"}}
</script></head>
<body><h1>Associate Product Manager</h1></body></html>
"""


ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"
ROBOTS_DISALLOW_PRIVATE = "User-agent: *\nDisallow: /private\n"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # silence test output
        return

    def _send(self, body: str, *, status: int = 200, content_type: str = "text/html") -> None:
        payload = body.replace("{host}", self.headers.get("Host", "localhost")).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, payload: Any, *, status: int = 200) -> None:
        self._send(json.dumps(payload), status=status, content_type="application/json")

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]

        if path == "/robots.txt":
            self._send(ROBOTS_DISALLOW_PRIVATE, content_type="text/plain")
        elif path.startswith("/v1/boards/") and path.endswith("/jobs"):
            self._send(
                json.dumps(GREENHOUSE_JOBS).replace(
                    "{host}", self.headers.get("Host", "localhost")
                ),
                content_type="application/json",
            )
        elif path.startswith("/v0/postings/"):
            self._send(
                json.dumps(LEVER_JOBS).replace("{host}", self.headers.get("Host", "localhost")),
                content_type="application/json",
            )
        elif path == "/api/public/jobs":
            self._json(THE_MUSE_JOBS)
        elif path == "/api/remote-jobs":
            self._json(REMOTIVE_JOBS)
        elif path.startswith("/v1/api/jobs/"):
            self._json(ADZUNA_JOBS)
        elif path == "/api/job-board-api":
            self._json(ARBEITNOW_JOBS)
        elif path == "/api/v2/remote-jobs":
            self._json(JOBICY_JOBS)
        elif path == "/api/search":
            self._json(USAJOBS_RESULTS)
        elif path == "/posting/live":
            self._send(LIVE_POSTING_HTML)
        elif path == "/posting/closed":
            self._send(CLOSED_POSTING_HTML)
        elif path == "/posting/expired":
            self._send(EXPIRED_POSTING_HTML)
        elif path == "/posting/wrong-company":
            self._send(WRONG_COMPANY_HTML)
        elif path == "/posting/gone":
            self._send("<html>no such job</html>", status=404)
        elif path == "/careers":
            self._send(CAREER_PAGE_HTML)
        elif path == "/team":
            self._send(TEAM_PAGE_HTML)
        elif path == "/private/secret":
            self._send("<html>should never be fetched</html>")
        elif path == "/blocked":
            self._send("<html>forbidden</html>", status=403)
        elif path == "/boom":
            self._send("<html>server error</html>", status=500)
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/careers")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path.startswith("/jobs/"):
            self._send(f"<html><body><h1>Job {path}</h1></body></html>")
        else:
            self._send("<html><body>not found</body></html>", status=404)


class MockSite:
    """Context manager that runs the mock site on an ephemeral local port."""

    def __init__(self) -> None:
        # Threading matters: collectors hold keep-alive connections open (one
        # for robots.txt, one for the page), which would deadlock a
        # single-connection server.
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def start(self) -> MockSite:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> MockSite:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
