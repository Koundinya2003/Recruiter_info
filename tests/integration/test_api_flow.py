"""The end-to-end workflow through the HTTP API."""

from __future__ import annotations

import pytest

from tests.conftest import requires_db

pytestmark = requires_db


def _company(client, **overrides):
    payload = {
        "company_name": "Testly Payments",
        "company_domain": "testly.example",
        "industry": "Fintech",
        "priority": "HIGH",
    }
    payload.update(overrides)
    response = client.post("/api/companies", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _seeded(client, session):
    """A company with a scored job and a contactable recruiter."""
    from app.db.base import utcnow
    from app.models.company import Company
    from app.models.enums import EmailConfidence, SourceType
    from app.models.job import Job
    from app.models.recruiter import Recruiter
    from app.services.recruiters.linking import link_recruiters_to_jobs
    from app.services.scan import rescore_company_recruiters
    from app.services.scoring.context import build_context
    from app.services.scoring.hiring_activity import refresh_company_hiring_activity
    from app.services.scoring.job_relevance import apply_job_score
    from app.utils.text import content_fingerprint

    company_json = _company(client)
    company = session.get(Company, company_json["id"])

    job = Job(
        company_id=company.id,
        title="Product Analyst",
        normalized_title="product analyst",
        description="Own product analytics, run A/B testing, write SQL, build dashboards.",
        location="Bangalore, India",
        normalized_location="bangalore india",
        job_url="https://testly.example/careers/product-analyst",
        canonical_url="https://testly.example/careers/product-analyst",
        content_hash=content_fingerprint("testly", "product analyst", "bangalore india"),
        source=SourceType.CAREER_PAGE_JSONLD,
        posted_at=utcnow(),
    )
    session.add(job)
    recruiter = Recruiter(
        company_id=company.id,
        name="Casey Talent",
        normalized_name="casey talent",
        company_name=company.company_name,
        title="Talent Acquisition Partner, Product",
        public_professional_email="casey.talent@testly.example",
        email_source_url="https://testly.example/careers",
        email_source_type=SourceType.COMPANY_TEAM_PAGE,
        email_confidence=EmailConfidence.HIGH,
        email_confidence_score=100.0,
    )
    session.add(recruiter)
    session.flush()

    context = build_context(session, company.user_id)
    apply_job_score(job, context)
    rescore_company_recruiters(session, company)
    link_recruiters_to_jobs(session, company.id)
    refresh_company_hiring_activity(session, company)
    session.commit()
    return company_json, job.id, recruiter.id


# --- Company management ---------------------------------------------------------


def test_health_reports_configuration(api_client) -> None:
    body = api_client.get("/api/admin/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["ai_provider"] == "offline_template"  # no key configured in tests
    assert body["auth_enabled"] is False


def test_company_lifecycle(api_client, session) -> None:
    created = _company(api_client)
    assert created["priority"] == "HIGH"

    duplicate = api_client.post("/api/companies", json={"company_name": "Testly Payments"})
    assert duplicate.status_code == 409

    paused = api_client.post(f"/api/companies/{created['id']}/pause").json()
    assert paused["active"] is False
    resumed = api_client.post(f"/api/companies/{created['id']}/resume").json()
    assert resumed["active"] is True

    updated = api_client.patch(
        f"/api/companies/{created['id']}", json={"priority": "CRITICAL"}
    ).json()
    assert updated["priority"] == "CRITICAL"

    detail = api_client.get(f"/api/companies/{created['id']}").json()
    assert "stats" in detail and "hiring_activity_breakdown" in detail

    assert api_client.delete(f"/api/companies/{created['id']}").status_code == 204
    assert api_client.get(f"/api/companies/{created['id']}").status_code == 404


def test_company_scan_records_a_crawl_run(api_client, session) -> None:
    """Even a scan that finds nothing must leave an auditable record."""
    created = _company(api_client, career_page_url="https://example.com/careers")
    result = api_client.post(f"/api/companies/{created['id']}/scan", json={}).json()
    assert result["crawl_run_ids"]
    crawls = api_client.get("/api/admin/crawls").json()
    assert crawls
    assert all(c["status"] != "RUNNING" for c in crawls)


def test_scan_without_career_page_says_so(api_client, session) -> None:
    created = _company(api_client)
    result = api_client.post(f"/api/companies/{created['id']}/scan", json={}).json()
    assert any("career page" in note.lower() for note in result["notes"])


# --- The full outreach workflow -------------------------------------------------


def test_full_workflow(api_client, session) -> None:
    company, job_id, recruiter_id = _seeded(api_client, session)

    # Relevance is explained, not asserted.
    job = api_client.get(f"/api/jobs/{job_id}").json()
    assert job["relevance"]["total"] >= 80
    assert job["relevance"]["reasons"]
    assert job["relevance"]["components"]

    # Every contact detail carries its source.
    recruiter = api_client.get(f"/api/recruiters/{recruiter_id}").json()
    assert recruiter["email_source_url"]
    assert recruiter["email_is_inferred"] is False
    assert recruiter["relevance"]["reasons"]

    # Ranked shortlist.
    today = api_client.get("/api/outreach/contact-today").json()
    assert today
    top = today[0]
    assert top["reasons"]
    assert top["band"]

    # Queue it.
    lead = api_client.post(
        "/api/outreach/leads", json={"recruiter_id": recruiter_id, "job_id": job_id}
    ).json()
    assert lead["status"] == "NEW"

    duplicate = api_client.post(
        "/api/outreach/leads", json={"recruiter_id": recruiter_id, "job_id": job_id}
    )
    assert duplicate.status_code == 409

    # Draft — never auto-approved.
    draft = api_client.post(f"/api/outreach/leads/{lead['id']}/draft", json={}).json()
    assert draft["approved"] is False
    assert draft["body"]

    # Cannot record outreach before approval.
    early = api_client.post(f"/api/outreach/leads/{lead['id']}/record-outreach", json={})
    assert early.status_code == 409

    # Edit, then approve.
    edited = api_client.patch(
        f"/api/outreach/leads/{lead['id']}/draft",
        json={"subject": "Product Analyst role", "body": draft["body"] + "\n\nBest, Test"},
    ).json()
    assert edited["draft_approved"] is False

    approved = api_client.post(f"/api/outreach/leads/{lead['id']}/approve", json={}).json()
    assert approved["draft_approved"] is True
    assert approved["status"] == "APPROVED"

    # Record it once.
    recorded = api_client.post(
        f"/api/outreach/leads/{lead['id']}/record-outreach", json={"channel": "email"}
    ).json()
    assert recorded["status"] == "CONTACTED"
    assert recorded["contacted_at"]

    # Not twice.
    again = api_client.post(f"/api/outreach/leads/{lead['id']}/record-outreach", json={})
    assert again.status_code == 409
    assert "already contacted" in again.json()["detail"]

    # And no longer recommended.
    after = api_client.get("/api/outreach/contact-today").json()
    assert not any(
        o["recruiter_id"] == recruiter_id and o["job_id"] == job_id for o in after
    )

    # Response and full history.
    replied = api_client.post(
        f"/api/outreach/leads/{lead['id']}/response", json={"response": "POSITIVE"}
    ).json()
    assert replied["status"] == "REPLIED"

    history = api_client.get(f"/api/outreach/leads/{lead['id']}/history").json()
    kinds = [event["event_type"] for event in history]
    assert kinds[0] == "LEAD_CREATED"
    for expected in ["DRAFT_GENERATED", "DRAFT_EDITED", "DRAFT_APPROVED", "CONTACT_RECORDED"]:
        assert expected in kinds


def test_do_not_contact_removes_and_blocks(api_client, session) -> None:
    company, job_id, recruiter_id = _seeded(api_client, session)
    assert api_client.get("/api/outreach/contact-today").json()

    marked = api_client.post(
        f"/api/recruiters/{recruiter_id}/do-not-contact", json={"reason": "Asked not to be contacted"}
    )
    assert marked.status_code == 200

    assert api_client.get("/api/outreach/contact-today").json() == []
    blocked = api_client.post("/api/outreach/leads", json={"recruiter_id": recruiter_id})
    assert blocked.status_code == 409
    assert "DO NOT CONTACT" in blocked.json()["detail"]

    draft_blocked = api_client.post(f"/api/outreach/leads/{1}/draft", json={})
    assert draft_blocked.status_code in {404, 409}


def test_verification_endpoint_records_result(api_client, session) -> None:
    company, job_id, recruiter_id = _seeded(api_client, session)
    result = api_client.post(f"/api/recruiters/{recruiter_id}/verify-email").json()
    assert result["provider"] == "dns"
    assert result["status"] in {"valid", "invalid", "risky", "unknown"}

    detail = api_client.get(f"/api/recruiters/{recruiter_id}").json()
    assert detail["verification_history"]


def test_verify_without_an_address_is_refused(api_client, session) -> None:
    from app.models.company import Company
    from app.models.recruiter import Recruiter

    created = _company(api_client)
    company = session.get(Company, created["id"])
    recruiter = Recruiter(
        company_id=company.id,
        name="No Email",
        normalized_name="no email",
        company_name=company.company_name,
        title="Recruiter",
    )
    session.add(recruiter)
    session.commit()

    response = api_client.post(f"/api/recruiters/{recruiter.id}/verify-email")
    assert response.status_code == 422


# --- Search, filters, settings --------------------------------------------------


def test_search_spans_entities(api_client, session) -> None:
    _seeded(api_client, session)
    results = api_client.get("/api/search", params={"q": "product"}).json()
    assert results["jobs"]
    assert "companies" in results and "recruiters" in results


def test_job_filters(api_client, session) -> None:
    _seeded(api_client, session)
    assert api_client.get("/api/jobs", params={"min_relevance": 200}).status_code == 422
    high = api_client.get("/api/jobs", params={"min_relevance": 80}).json()
    assert high
    fresh = api_client.get("/api/jobs", params={"max_age_hours": 24}).json()
    assert fresh
    none_left = api_client.get("/api/jobs", params={"q": "zzzz-no-such-job"}).json()
    assert none_left == []


def test_profile_and_taxonomy_round_trip(api_client) -> None:
    saved = api_client.put(
        "/api/settings/profile",
        json={
            "full_name": "Test Candidate",
            "headline": "product analyst",
            "years_experience": 3,
            "skills": ["SQL", "SQL", "  "],
            "target_roles": ["Product Analyst"],
            "target_industries": ["Fintech"],
            "preferred_locations": ["Remote"],
        },
    ).json()
    assert saved["skills"] == ["SQL"]  # de-duplicated and cleaned

    term = api_client.post(
        "/api/settings/taxonomy",
        json={"kind": "ROLE", "term": "Revenue Analyst", "aliases": ["rev analyst"], "weight": 0.8},
    ).json()
    assert term["term"] == "Revenue Analyst"

    duplicate = api_client.post(
        "/api/settings/taxonomy", json={"kind": "ROLE", "term": "Revenue Analyst"}
    )
    assert duplicate.status_code == 409

    assert api_client.delete(f"/api/settings/taxonomy/{term['id']}").status_code == 204


def test_scoring_weights_are_configurable(api_client) -> None:
    defaults = api_client.get("/api/settings/scoring").json()
    assert defaults["job"]["role_match"] == 30

    updated = api_client.put(
        "/api/settings/scoring", json={"job_weights": {"role_match": 45}}
    ).json()
    assert updated["job"]["role_match"] == 45

    invalid = api_client.put("/api/settings/scoring", json={"job_weights": {"role_match": 900}})
    assert invalid.status_code == 422

    reset = api_client.post("/api/settings/scoring/reset").json()
    assert reset["job"]["role_match"] == 30


def test_rescore_endpoint(api_client, session) -> None:
    _seeded(api_client, session)
    result = api_client.post("/api/jobs/rescore").json()
    assert result["jobs_rescored"] >= 1


def test_dashboard_returns_stats_and_opportunities(api_client, session) -> None:
    _seeded(api_client, session)
    body = api_client.get("/api/dashboard").json()
    assert body["stats"]["relevant_jobs"] >= 1
    assert body["stats"]["recruiters_found"] >= 1
    assert body["opportunities"]
    assert body["generated_at"]


def test_admin_overview(api_client, session) -> None:
    _seeded(api_client, session)
    body = api_client.get("/api/admin/overview").json()
    for key in ("recent_crawls", "sources_checked", "verification_results", "rate_limit_events"):
        assert key in body


@pytest.mark.parametrize("path", ["/api/companies/9999", "/api/jobs/9999", "/api/recruiters/9999", "/api/outreach/leads/9999"])
def test_missing_records_return_404(api_client, path: str) -> None:
    assert api_client.get(path).status_code == 404
