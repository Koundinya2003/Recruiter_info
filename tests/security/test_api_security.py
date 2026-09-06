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
    "%'; UPDATE contacts SET email = NULL; --",
    "\\'; TRUNCATE applications; --",
    "'||(SELECT pg_sleep(5))||'",
]

SSRF_PAYLOADS = [
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost:8000/api/health",
    "file:///etc/passwd",
    "http://192.168.0.1/",
    "http://user:pass@example.com/",
    "gopher://127.0.0.1:6379/_INFO",
    "http://[::1]/",
]


def _tables() -> set[str]:
    return set(inspect(get_engine()).get_table_names())


@pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
def test_sql_injection_in_job_filters_is_inert(api_client, payload: str) -> None:
    """The ORM parameterises everything; injection strings are just strings."""
    before = _tables()
    response = api_client.get("/api/jobs", params={"q": payload, "company": payload})
    assert response.status_code == 200
    assert response.json() == []
    assert _tables() == before


@pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
def test_sql_injection_in_application_filters_is_inert(api_client, payload: str) -> None:
    before = _tables()
    response = api_client.get("/api/applications", params={"q": payload})
    assert response.status_code == 200
    assert _tables() == before


def test_sql_injection_in_a_search_request_is_inert(api_client) -> None:
    """A free-text request is parsed, never concatenated into SQL."""
    before = _tables()
    response = api_client.post(
        "/api/search/parse",
        json={"query": "'; DROP TABLE jobs; -- roles in Bangalore", "use_llm": False},
    )
    assert response.status_code == 200
    assert _tables() == before
    with get_engine().connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1


def test_sql_injection_in_a_stored_field_is_stored_literally(
    api_client, session, user, job
) -> None:
    from app.services import applications as tracker

    payload = "'; DROP TABLE applications; --"
    application = tracker.save_job(session, user, job)
    session.commit()

    response = api_client.patch(
        f"/api/applications/{application.id}", json={"notes": payload}
    )
    assert response.status_code == 200
    assert response.json()["notes"] == payload
    assert "applications" in _tables()


@pytest.mark.parametrize("url", SSRF_PAYLOADS)
def test_a_profile_url_cannot_be_an_ssrf_target(api_client, url: str) -> None:
    """A URL from a request body is fetched later, so it is validated now."""
    from app.security.url_guard import is_safe_url

    assert not is_safe_url(url), f"{url} must be refused by the URL policy"


def test_a_search_request_cannot_smuggle_a_url_to_fetch(api_client) -> None:
    """Nothing in a search request becomes a URL the crawler will visit."""
    response = api_client.post(
        "/api/search/parse",
        json={"query": "roles at http://169.254.169.254/latest/meta-data/", "use_llm": False},
    )
    assert response.status_code == 200
    parsed = response.json()
    assert not any("169.254" in company for company in parsed["companies"])


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/jobs", {"limit": 0}),
        ("/api/jobs", {"limit": 99999}),
        ("/api/jobs", {"offset": -1}),
        ("/api/dashboard", {"limit": 0}),
        ("/api/search/history", {"limit": 9999}),
    ],
)
def test_invalid_query_parameters_are_rejected(api_client, path: str, params: dict) -> None:
    assert api_client.get(path, params=params).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"query": ""},
        {"query": "a"},
        {"query": "x" * 5000},
        {"query": "valid query", "limit": -1},
        {"query": "valid query", "min_years": -5},
        {"query": "valid query", "max_years": 500},
    ],
)
def test_invalid_search_payloads_are_rejected(api_client, payload: dict) -> None:
    assert api_client.post("/api/search", json=payload).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"job_id": "not-a-number"},
        {},
        {"job_id": 1, "status": "NOT_A_STATUS"},
        {"job_id": 1, "notes": "x" * 6000},
    ],
)
def test_invalid_application_payloads_are_rejected(api_client, payload: dict) -> None:
    assert api_client.post("/api/applications", json=payload).status_code == 422


def test_an_invalid_contact_email_is_rejected(api_client, session, user, job) -> None:
    from app.services import applications as tracker

    application = tracker.save_job(session, user, job)
    session.commit()
    response = api_client.patch(
        f"/api/applications/{application.id}", json={"contact_email": "not an email"}
    )
    assert response.status_code == 422


def test_xss_payload_is_stored_and_returned_as_data_not_markup(
    api_client, session, user, job
) -> None:
    from app.services import applications as tracker

    application = tracker.save_job(session, user, job)
    session.commit()
    payload = "<script>alert('xss')</script>"

    response = api_client.patch(
        f"/api/applications/{application.id}", json={"notes": payload}
    )
    assert response.status_code == 200
    assert response.json()["notes"] == payload
    # JSON, not HTML: the browser is never asked to parse this as markup.
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_api_key_is_enforced_when_configured(api_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "api_key", "s3cret-test-key")
    assert api_client.get("/api/jobs").status_code == 401
    assert api_client.get("/api/jobs", headers={"X-API-Key": "wrong"}).status_code == 401
    assert (
        api_client.get("/api/jobs", headers={"X-API-Key": "s3cret-test-key"}).status_code == 200
    )


def test_health_stays_reachable_without_a_key(api_client, monkeypatch) -> None:
    """Health is how you find out the service is up; it must not need auth."""
    monkeypatch.setattr(settings, "api_key", "s3cret-test-key")
    assert api_client.get("/api/health").status_code == 200


def test_health_never_returns_secret_values(api_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-should-never-appear")
    monkeypatch.setattr(settings, "adzuna_app_key", "adzuna-should-never-appear")
    body = api_client.get("/api/health").text
    assert "sk-should-never-appear" not in body
    assert "adzuna-should-never-appear" not in body
    assert settings.database_url not in body


def test_sources_report_configuration_without_leaking_credentials(
    api_client, monkeypatch
) -> None:
    """The Sources page says what is missing — never what is set."""
    monkeypatch.setattr(settings, "adzuna_app_id", "id-should-never-appear")
    monkeypatch.setattr(settings, "adzuna_app_key", "key-should-never-appear")
    body = api_client.get("/api/search/sources").text
    assert "id-should-never-appear" not in body
    assert "key-should-never-appear" not in body


def test_error_responses_do_not_leak_internals(api_client) -> None:
    response = api_client.get("/api/jobs/999999")
    assert response.status_code == 404
    body = response.text
    for leak in ("Traceback", "psycopg2", "sqlalchemy", "/home/", settings.database_url):
        assert leak not in body


def test_database_url_is_never_echoed_by_the_api(api_client) -> None:
    for path in ("/api/health", "/api/dashboard", "/api/jobs", "/api/search/sources"):
        assert settings.database_url not in api_client.get(path).text


def test_no_secrets_are_committed_to_the_repository() -> None:
    """A .env must exist only as an example; real keys never enter git."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert ".env" in (root / ".gitignore").read_text()

    example = (root / ".env.example").read_text()
    secret_prefixes = (
        "OPENROUTER_API_KEY",
        "EMAIL_VERIFICATION_API_KEY",
        "API_KEY",
        "ADZUNA_APP_ID",
        "ADZUNA_APP_KEY",
        "THE_MUSE_API_KEY",
        "USAJOBS_API_KEY",
    )
    for line in example.splitlines():
        if line.startswith(secret_prefixes):
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


def test_the_crawler_refuses_private_addresses_by_default() -> None:
    """The production policy must not be relaxed by the test settings."""
    assert settings.crawler_allow_private_networks is False
    assert settings.crawler_respect_robots is True
