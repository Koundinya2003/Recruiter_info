"""Thin HTTP client for the backend API.

The frontend never touches the database directly — it is just another API
client, which is what keeps a future React/Next.js frontend a drop-in swap.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_TIMEOUT = 120.0


class APIError(Exception):
    """An error returned by the API, with a message safe to show the user."""

    def __init__(self, message: str, *, status_code: int = 0, problems: list[dict] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.problems = problems or []


class APIClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("API_BASE_URL", "http://localhost:8000")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("API_KEY", "")

    # -- plumbing ------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/api{path}"
        try:
            response = httpx.request(
                method, url, headers=self._headers(), timeout=DEFAULT_TIMEOUT, **kwargs
            )
        except httpx.HTTPError as exc:
            raise APIError(
                f"Cannot reach the API at {self.base_url}. Is the backend running?\n\n{exc}"
            ) from exc

        if response.status_code == 204:
            return None
        if response.status_code >= 400:
            detail = "Request failed"
            problems: list[dict] = []
            try:
                payload = response.json()
                detail = payload.get("detail", detail)
                problems = payload.get("problems", [])
                if problems:
                    detail = detail + ": " + "; ".join(
                        f"{p.get('field', '')} {p.get('message', '')}".strip() for p in problems
                    )
            except Exception:  # noqa: BLE001
                detail = response.text[:300] or detail
            raise APIError(str(detail), status_code=response.status_code, problems=problems)
        return response.json()

    def get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, params=clean)

    def post(self, path: str, json: Any = None, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("POST", path, json=json, params=clean)

    def patch(self, path: str, json: Any = None) -> Any:
        return self._request("PATCH", path, json=json)

    def put(self, path: str, json: Any = None) -> Any:
        return self._request("PUT", path, json=json)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    # -- endpoints -----------------------------------------------------------
    def health(self) -> dict:
        return self.get("/admin/health")

    def dashboard(self, limit: int = 15, include_demo: bool = True) -> dict:
        return self.get("/dashboard", limit=limit, include_demo=include_demo)

    def contact_today(self, **filters: Any) -> list[dict]:
        return self.get("/outreach/contact-today", **filters)

    def companies(self, **filters: Any) -> list[dict]:
        return self.get("/companies", **filters)

    def company(self, company_id: int) -> dict:
        return self.get(f"/companies/{company_id}")

    def create_company(self, payload: dict) -> dict:
        return self.post("/companies", json=payload)

    def update_company(self, company_id: int, payload: dict) -> dict:
        return self.patch(f"/companies/{company_id}", json=payload)

    def delete_company(self, company_id: int) -> None:
        self.delete(f"/companies/{company_id}")

    def scan_company(self, company_id: int, payload: dict | None = None) -> dict:
        return self.post(f"/companies/{company_id}/scan", json=payload or {})

    def pause_company(self, company_id: int) -> dict:
        return self.post(f"/companies/{company_id}/pause")

    def resume_company(self, company_id: int) -> dict:
        return self.post(f"/companies/{company_id}/resume")

    def company_history(self, company_id: int) -> list[dict]:
        return self.get(f"/companies/{company_id}/history")

    def jobs(self, **filters: Any) -> list[dict]:
        return self.get("/jobs", **filters)

    def job(self, job_id: int) -> dict:
        return self.get(f"/jobs/{job_id}")

    def rescore_jobs(self, company_id: int | None = None) -> dict:
        return self.post("/jobs/rescore", company_id=company_id)

    def recruiters(self, **filters: Any) -> list[dict]:
        return self.get("/recruiters", **filters)

    def recruiter(self, recruiter_id: int) -> dict:
        return self.get(f"/recruiters/{recruiter_id}")

    def create_recruiter(self, payload: dict) -> dict:
        return self.post("/recruiters", json=payload)

    def verify_recruiter_email(self, recruiter_id: int) -> dict:
        return self.post(f"/recruiters/{recruiter_id}/verify-email")

    def do_not_contact(self, recruiter_id: int, reason: str | None = None) -> dict:
        return self.post(f"/recruiters/{recruiter_id}/do-not-contact", json={"reason": reason})

    def clear_do_not_contact(self, recruiter_id: int) -> dict:
        return self.delete(f"/recruiters/{recruiter_id}/do-not-contact")

    def leads(self, **filters: Any) -> list[dict]:
        return self.get("/outreach/leads", **filters)

    def lead(self, lead_id: int) -> dict:
        return self.get(f"/outreach/leads/{lead_id}")

    def create_lead(self, recruiter_id: int, job_id: int | None = None) -> dict:
        return self.post(
            "/outreach/leads", json={"recruiter_id": recruiter_id, "job_id": job_id}
        )

    def generate_draft(self, lead_id: int, reason: str | None = None, force_offline: bool = False) -> dict:
        return self.post(
            f"/outreach/leads/{lead_id}/draft",
            json={"reason": reason, "force_offline": force_offline},
        )

    def edit_draft(self, lead_id: int, subject: str, body: str) -> dict:
        return self.patch(f"/outreach/leads/{lead_id}/draft", json={"subject": subject, "body": body})

    def approve_lead(self, lead_id: int) -> dict:
        return self.post(f"/outreach/leads/{lead_id}/approve")

    def record_outreach(self, lead_id: int, note: str | None = None) -> dict:
        return self.post(f"/outreach/leads/{lead_id}/record-outreach", json={"note": note})

    def follow_up(self, lead_id: int, note: str | None = None) -> dict:
        return self.post(f"/outreach/leads/{lead_id}/follow-up", json={"note": note})

    def record_response(self, lead_id: int, response: str, note: str | None = None) -> dict:
        return self.post(
            f"/outreach/leads/{lead_id}/response", json={"response": response, "note": note}
        )

    def change_status(self, lead_id: int, status: str, note: str | None = None) -> dict:
        return self.post(f"/outreach/leads/{lead_id}/status", json={"status": status, "note": note})

    def add_note(self, lead_id: int, note: str) -> dict:
        return self.post(f"/outreach/leads/{lead_id}/notes", json={"note": note})

    def lead_history(self, lead_id: int) -> list[dict]:
        return self.get(f"/outreach/leads/{lead_id}/history")

    def profile(self) -> dict:
        return self.get("/settings/profile")

    def save_profile(self, payload: dict) -> dict:
        return self.put("/settings/profile", json=payload)

    def taxonomy(self, kind: str | None = None) -> list[dict]:
        return self.get("/settings/taxonomy", kind=kind)

    def add_term(self, payload: dict) -> dict:
        return self.post("/settings/taxonomy", json=payload)

    def delete_term(self, term_id: int) -> None:
        self.delete(f"/settings/taxonomy/{term_id}")

    def reset_taxonomy(self) -> dict:
        return self.post("/settings/taxonomy/reset")

    def scoring(self) -> dict:
        return self.get("/settings/scoring")

    def save_scoring(self, payload: dict) -> dict:
        return self.put("/settings/scoring", json=payload)

    def reset_scoring(self) -> dict:
        return self.post("/settings/scoring/reset")

    def admin_overview(self) -> dict:
        return self.get("/admin/overview")

    def crawls(self, **filters: Any) -> list[dict]:
        return self.get("/admin/crawls", **filters)

    def crawl_records(self, crawl_id: int, accepted: bool | None = None) -> list[dict]:
        return self.get(f"/admin/crawls/{crawl_id}/records", accepted=accepted)

    def verifications(self, limit: int = 50) -> list[dict]:
        return self.get("/admin/verifications", limit=limit)

    def search(self, q: str) -> dict:
        return self.get("/search", q=q)


def get_client() -> APIClient:
    return APIClient()
