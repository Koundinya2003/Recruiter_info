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
        return self.get("/health")

    def dashboard(self, limit: int = 8) -> dict:
        return self.get("/dashboard", limit=limit)

    # --- Search -------------------------------------------------------------
    def parse_query(self, query: str, use_llm: bool = True) -> dict:
        return self.post("/search/parse", json={"query": query, "use_llm": use_llm})

    def run_search(self, payload: dict) -> dict:
        return self.post("/search", json=payload)

    def search_jobs(self, search_id: int) -> list[dict]:
        return self.get(f"/search/{search_id}/jobs")

    def search_history(self, limit: int = 20) -> list[dict]:
        return self.get("/search/history", limit=limit)

    def search_run(self, run_id: int) -> dict:
        return self.get(f"/search/runs/{run_id}")

    def delete_search(self, search_id: int) -> dict:
        return self.delete(f"/search/{search_id}")

    def sources(self) -> list[dict]:
        return self.get("/search/sources")

    # --- Jobs ---------------------------------------------------------------
    def jobs(self, **filters: Any) -> list[dict]:
        return self.get("/jobs", **filters)

    def job(self, job_id: int) -> dict:
        return self.get(f"/jobs/{job_id}")

    def job_counts(self) -> dict:
        return self.get("/jobs/count")

    def update_job(self, job_id: int, payload: dict) -> dict:
        return self.patch(f"/jobs/{job_id}", json=payload)

    def revalidate_job(self, job_id: int) -> dict:
        return self.post(f"/jobs/{job_id}/revalidate")

    def delete_job(self, job_id: int) -> dict:
        return self.delete(f"/jobs/{job_id}")

    # --- Contacts -----------------------------------------------------------
    def contacts(self, **filters: Any) -> list[dict]:
        return self.get("/contacts", **filters)

    def job_contacts(self, job_id: int) -> list[dict]:
        return self.get(f"/contacts/job/{job_id}")

    def discover_contacts(self, job_id: int) -> list[dict]:
        return self.post(f"/contacts/job/{job_id}/discover")

    def verify_contact_email(self, contact_id: int) -> dict:
        return self.post(f"/contacts/{contact_id}/verify-email")

    def archive_contact(self, contact_id: int) -> dict:
        return self.delete(f"/contacts/{contact_id}")

    # --- Applications -------------------------------------------------------
    def applications(self, **filters: Any) -> list[dict]:
        return self.get("/applications", **filters)

    def application(self, application_id: int) -> dict:
        return self.get(f"/applications/{application_id}")

    def create_application(self, payload: dict) -> dict:
        return self.post("/applications", json=payload)

    def update_application(self, application_id: int, payload: dict) -> dict:
        return self.patch(f"/applications/{application_id}", json=payload)

    def mark_applied(self, application_id: int) -> dict:
        return self.post(f"/applications/{application_id}/mark-applied")

    def mark_outreach_sent(self, application_id: int, channel: str = "EMAIL_SENT") -> dict:
        return self.post(
            f"/applications/{application_id}/mark-outreach-sent", channel=channel
        )

    def application_history(self, application_id: int) -> list[dict]:
        return self.get(f"/applications/{application_id}/history")

    def delete_application(self, application_id: int) -> dict:
        return self.delete(f"/applications/{application_id}")

    # --- Profile ------------------------------------------------------------
    def profile(self) -> dict:
        return self.get("/profile")

    def save_profile(self, payload: dict) -> dict:
        return self.put("/profile", json=payload)


def get_client() -> APIClient:
    return APIClient()
