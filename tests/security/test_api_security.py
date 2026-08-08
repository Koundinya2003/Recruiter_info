"""API security: injection, auth, input validation and secret leakage."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from app.config import settings
from app.db.session import get_engine
from tests.conftest import requires_db

pytestmark = requires_db

SQL_INJECTION_PAYLOADS = [
    "'; DROP TABLE companies; --",
    "' OR '1'='1",
    "1; DELETE FROM users WHERE 1=1; --",
    "admin'--",
    "' UNION SELECT NULL, version(), NULL --",
    "%'; UPDATE recruiters SET do_not_contact = false; --",
    "\\'; TRUNCATE outreach_leads; --",
    "'||(SELECT pg_sleep(5))||'",
]


@pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
def test_sql_injection_in_search_is_inert(api_client, payload: str) -> None:
    """The ORM parameterises everything; injection strings are just strings."""
    response = api_client.get("/api/search", params={"q": payload})
    assert response.status_code == 200

    tables = set(inspect(get_engine()).get_table_names())
    assert {"companies", "users", "recruiters", "outreach_leads"} <= tables


@pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
def test_sql_injection_in_filters_is_inert(api_client, payload: str) -> None:
    for path in ("/api/jobs", "/api/recruiters", "/api/companies"):
        response = api_client.get(path, params={"q": payload})
        assert response.status_code == 200
        assert isinstance(response.json(), list)


def test_sql_injection_in_a_stored_field_is_stored_literally(api_client) -> None:
    payload = "Acme'); DROP TABLE jobs; --"
    created = api_client.post("/api/companies", json={"company_name": payload})
    assert created.status_code == 201
    assert created.json()["company_name"] == payload

    tables = set(inspect(get_engine()).get_table_names())
    assert "jobs" in tables

    with get_engine().connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM companies")).scalar()
    assert count and count >= 1


# --- SSRF at the API boundary ---------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/api/admin/health",
        "file:///etc/passwd",
        "http://192.168.0.1/",
        "http://user:pass@example.com/",
        "gopher://127.0.0.1:6379/_INFO",
    ],
)
def test_career_page_url_cannot_be_an_ssrf_target(api_client, url: str) -> None:
    response = api_client.post(
        "/api/companies", json={"company_name": f"Evil {url[:12]}", "career_page_url": url}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Validation failed"
    assert body["problems"]


def test_profile_urls_are_validated(api_client) -> None:
    response = api_client.put(
        "/api/settings/profile", json={"portfolio_url": "http://127.0.0.1:8000/"}
    )
    assert response.status_code == 422


def test_scan_extra_urls_are_validated(api_client) -> None:
    created = api_client.post("/api/companies", json={"company_name": "Scan Target"}).json()
    response = api_client.post(
        f"/api/companies/{created['id']}/scan",
        json={"extra_recruiter_urls": ["http://169.254.169.254/"]},
    )
    assert response.status_code == 422


# --- Input validation -----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},                                        # missing required name
        {"company_name": ""},                      # empty name
        {"company_name": "x" * 500},               # too long
        {"company_name": "Ok", "priority": "URGENT"},   # not a valid enum member
        {"company_name": "Ok", "company_domain": "not a domain"},
        {"company_name": "Ok", "active": "maybe"},
    ],
)
def test_invalid_company_payloads_are_rejected(api_client, payload: dict) -> None:
    assert api_client.post("/api/companies", json=payload).status_code == 422


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/jobs", {"limit": 5000}),
        ("/api/jobs", {"limit": -1}),
        ("/api/jobs", {"min_relevance": 500}),
        ("/api/jobs", {"sort": "'; DROP TABLE jobs; --"}),
        ("/api/recruiters", {"email_confidence": "SUPER_HIGH"}),
        ("/api/outreach/contact-today", {"limit": 99999}),
        ("/api/search", {"q": ""}),
    ],
)
def test_invalid_query_parameters_are_rejected(api_client, path: str, params: dict) -> None:
    assert api_client.get(path, params=params).status_code == 422


def test_manual_recruiter_requires_a_source_for_an_email(api_client) -> None:
    """Provenance is mandatory: no address without a source URL."""
    company = api_client.post("/api/companies", json={"company_name": "Prov Co"}).json()
    response = api_client.post(
        "/api/recruiters",
        json={
            "company_id": company["id"],
            "name": "Someone Real",
            "public_professional_email": "someone@provco.example",
        },
    )
    assert response.status_code == 422
    assert "source" in response.json()["detail"].lower()


def test_xss_payload_is_stored_and_returned_as_data_not_markup(api_client) -> None:
    payload = "<script>alert('xss')</script>"
    created = api_client.post("/api/companies", json={"company_name": payload}).json()
    fetched = api_client.get(f"/api/companies/{created['id']}")
    # JSON-encoded, never interpolated into a template server-side.
    assert fetched.json()["company_name"] == payload
    assert fetched.headers["content-type"].startswith("application/json")
    assert fetched.headers["X-Content-Type-Options"] == "nosniff"


# --- Authentication -------------------------------------------------------------


def test_api_key_is_enforced_when_configured(api_client) -> None:
    settings.api_key = "s3cret-test-key"
    try:
        assert api_client.get("/api/companies").status_code == 401
        assert api_client.get("/api/companies", headers={"X-API-Key": "wrong"}).status_code == 401
        ok = api_client.get("/api/companies", headers={"X-API-Key": "s3cret-test-key"})
        assert ok.status_code == 200
    finally:
        settings.api_key = ""


def test_health_stays_reachable_without_a_key(api_client) -> None:
    settings.api_key = "s3cret-test-key"
    try:
        # Liveness must not require the key, or monitoring cannot work.
        assert api_client.get("/api/admin/health").status_code == 200
    finally:
        settings.api_key = ""


# --- Secrets --------------------------------------------------------------------


def test_health_never_returns_secret_values(api_client) -> None:
    settings.api_key = "s3cret-test-key"
    settings.openrouter_api_key = "sk-should-never-appear"
    try:
        body = api_client.get("/api/admin/health").text
        assert "s3cret-test-key" not in body
        assert "sk-should-never-appear" not in body
        # It reports *whether* something is configured, not what it is.
        assert '"ai_configured":true' in body.replace(" ", "")
    finally:
        settings.api_key = ""
        settings.openrouter_api_key = ""


def test_error_responses_do_not_leak_internals(api_client) -> None:
    response = api_client.get("/api/companies/not-an-integer")
    assert response.status_code == 422
    body = response.text
    for leak in ("Traceback", "psycopg2", "sqlalchemy", "/home/", settings.database_url):
        assert leak not in body


def test_database_url_is_never_echoed_by_the_api(api_client) -> None:
    for path in ("/api/admin/health", "/api/admin/overview", "/api/companies"):
        assert settings.database_url not in api_client.get(path).text


def test_no_secrets_are_committed_to_the_repository() -> None:
    """A .env must exist only as an example; real keys never enter git."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    gitignore = (root / ".gitignore").read_text()
    assert ".env" in gitignore

    example = (root / ".env.example").read_text()
    for line in example.splitlines():
        if line.startswith(("OPENROUTER_API_KEY", "EMAIL_VERIFICATION_API_KEY", "API_KEY")):
            assert line.split("=", 1)[1].strip() == "", f"{line} must have an empty placeholder"


def test_ai_provider_errors_do_not_echo_the_key() -> None:
    """A 401 from the provider must not put the key into a log or a response."""
    from app.services.ai.provider import LLMError, OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        api_key="sk-secret-value", base_url="https://127.0.0.1:1", model="x"
    )
    with pytest.raises(LLMError) as excinfo:
        provider.complete([])
    assert "sk-secret-value" not in str(excinfo.value)
