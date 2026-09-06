"""The API, walked the way the product is used.

Search → review a validated job → save it → apply → record outreach →
follow up, checking the dashboard agrees at each step.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.providers.aggregators import TheMuseProvider
from tests.conftest import requires_db

pytestmark = requires_db

QUERY = (
    "Find Associate Product Manager roles for 0-2 years of experience "
    "in Bangalore and Hyderabad."
)


@pytest.fixture
def live_sources(mock_site, monkeypatch):
    monkeypatch.setattr(TheMuseProvider, "BASE", mock_site.url("/api/public/jobs"))
    monkeypatch.setattr(settings, "crawler_allow_private_networks", True)
    return mock_site


class TestBasics:
    def test_health(self, api_client) -> None:
        response = api_client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["database"] == "up"
        assert body["sources_total"] >= 5

    def test_root_points_at_the_docs(self, api_client) -> None:
        assert api_client.get("/").json()["docs"] == "/docs"

    def test_sources_report_their_configuration(self, api_client) -> None:
        sources = api_client.get("/api/search/sources").json()
        assert sources
        for source in sources:
            assert source["label"] and source["coverage"]
            if not source["configured"]:
                assert source["missing_settings"]


class TestParsing:
    def test_a_request_is_echoed_back_as_criteria(self, api_client) -> None:
        response = api_client.post(
            "/api/search/parse", json={"query": QUERY, "use_llm": False}
        )
        assert response.status_code == 200
        parsed = response.json()
        assert parsed["titles"] == ["Associate Product Manager"]
        assert parsed["locations"] == ["Bangalore", "Hyderabad"]
        assert parsed["min_years"] == 0.0
        assert parsed["max_years"] == 2.0
        assert parsed["summary"]

    def test_a_too_short_request_is_rejected(self, api_client) -> None:
        assert api_client.post("/api/search/parse", json={"query": "a"}).status_code == 422


class TestTheWholeWorkflow:
    def test_search_review_apply_outreach_follow_up(self, api_client, live_sources) -> None:
        # 1. Search.
        response = api_client.post(
            "/api/search",
            json={"query": QUERY, "use_llm": False, "providers": ["themuse"], "limit": 20},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["job_ids"], "the search should find the matching posting"
        assert result["funnel"]["raw_found"] >= 1

        # 2. Review what came back.
        jobs = api_client.get(f"/api/search/{result['search_id']}/jobs").json()
        assert jobs
        job = jobs[0]
        assert job["validation"]["status"] in {"VALID", "LIKELY_VALID", "UNVERIFIED"}
        assert job["validation"]["reason"]
        assert job["job_url"].startswith("http")
        assert job["experience_label"]
        assert job["contacts"], "a job should arrive with a way to reach someone"

        # 3. Save it, with the best contact attached.
        contact = next((c for c in job["contacts"] if c["email"]), job["contacts"][0])
        created = api_client.post(
            "/api/applications", json={"job_id": job["id"], "contact_id": contact["id"]}
        )
        assert created.status_code == 201, created.text
        application = created.json()
        assert application["status"] == "SAVED"
        assert application["job_title"] == job["title"]

        # 4. Apply (externally), then say so.
        applied = api_client.post(
            f"/api/applications/{application['id']}/mark-applied"
        ).json()
        assert applied["status"] == "APPLIED"
        assert applied["date_applied"]

        # 5. Record outreach.
        contacted = api_client.post(
            f"/api/applications/{application['id']}/mark-outreach-sent"
        ).json()
        assert contacted["outreach_status"] == "EMAIL_SENT"
        assert contacted["outreach_sent_at"]

        # 6. Set a follow-up and add a note.
        updated = api_client.patch(
            f"/api/applications/{application['id']}",
            json={"follow_up_date": "2020-01-01", "notes": "Ping again next week."},
        ).json()
        assert updated["follow_up_due"] is True
        assert updated["notes"] == "Ping again next week."

        # 7. The dashboard agrees.
        dashboard = api_client.get("/api/dashboard").json()
        assert dashboard["jobs_found"] >= 1
        assert dashboard["saved_jobs"] >= 1
        assert dashboard["applications_submitted"] >= 1
        assert dashboard["outreach_sent"] >= 1
        assert dashboard["follow_ups_due"] >= 1
        assert dashboard["last_search"]["query"] == QUERY

        # 8. The history is auditable.
        history = api_client.get(f"/api/applications/{application['id']}/history").json()
        assert [event["event_type"] for event in history][0] == "CREATED"


class TestJobLibrary:
    def test_jobs_can_be_filtered_and_dismissed(self, api_client, live_sources) -> None:
        api_client.post(
            "/api/search",
            json={"query": QUERY, "use_llm": False, "providers": ["themuse"]},
        )
        jobs = api_client.get("/api/jobs").json()
        assert jobs

        found = api_client.get("/api/jobs", params={"q": "Product"}).json()
        assert found

        job_id = jobs[0]["id"]
        api_client.patch(f"/api/jobs/{job_id}", json={"dismissed": True})
        remaining = [j["id"] for j in api_client.get("/api/jobs").json()]
        assert job_id not in remaining

    def test_counts_endpoint(self, api_client, live_sources) -> None:
        api_client.post(
            "/api/search",
            json={"query": QUERY, "use_llm": False, "providers": ["themuse"]},
        )
        counts = api_client.get("/api/jobs/count").json()
        assert counts["total"] >= 1
        assert counts["confirmed"] <= counts["total"]

    def test_a_missing_job_is_a_404(self, api_client) -> None:
        assert api_client.get("/api/jobs/999999").status_code == 404


class TestSearchHistory:
    def test_searches_are_remembered_and_can_be_forgotten(
        self, api_client, live_sources
    ) -> None:
        result = api_client.post(
            "/api/search",
            json={"query": QUERY, "use_llm": False, "providers": ["themuse"]},
        ).json()

        history = api_client.get("/api/search/history").json()
        assert any(item["raw_query"] == QUERY for item in history)

        run = api_client.get(f"/api/search/runs/{result['run_id']}").json()
        assert run["status"] in {"SUCCESS", "PARTIAL"}
        assert run["finished_at"]

        jobs_before = len(api_client.get("/api/jobs").json())
        api_client.delete(f"/api/search/{result['search_id']}")
        assert api_client.get("/api/search/history").json() == []
        # Deleting a search must not delete the jobs it found.
        assert len(api_client.get("/api/jobs").json()) == jobs_before


class TestProfile:
    def test_defaults_round_trip(self, api_client) -> None:
        api_client.put(
            "/api/profile",
            json={
                "full_name": "Test Candidate",
                "headline": "APM",
                "years_experience": 1.5,
                "default_titles": ["Associate Product Manager"],
                "default_locations": ["Bangalore"],
                "skills": ["SQL"],
            },
        )
        profile = api_client.get("/api/profile").json()
        assert profile["full_name"] == "Test Candidate"
        assert profile["default_locations"] == ["Bangalore"]


class TestGuardrails:
    def test_a_search_with_no_role_is_rejected_not_guessed(self, api_client) -> None:
        response = api_client.post("/api/search", json={"query": "  ", "use_llm": False})
        assert response.status_code == 422

    def test_an_application_for_a_missing_job_is_refused(self, api_client) -> None:
        response = api_client.post("/api/applications", json={"job_id": 999999})
        assert response.status_code == 404
