from __future__ import annotations

from typing import Any
from pathlib import Path

import httpx

from app.config import settings


class EulerFaultAgentError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class EulerFaultAgentClient:
    def __init__(self, base_url: str | None = None):
        url = (base_url or settings.EULER_FAULT_AGENT_URL).strip().rstrip("/")
        if not url or "://" not in url:
            raise EulerFaultAgentError("EULER_FAULT_AGENT_URL is invalid")
        self.base_url = url
        self.timeout = httpx.Timeout(
            connect=settings.EULER_FAULT_AGENT_CONNECT_TIMEOUT,
            read=settings.EULER_FAULT_AGENT_READ_TIMEOUT,
            write=settings.EULER_FAULT_AGENT_READ_TIMEOUT,
            pool=settings.EULER_FAULT_AGENT_CONNECT_TIMEOUT,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout, follow_redirects=False) as client:
                response = client.request(method, path, **kwargs)
        except (httpx.HTTPError, OSError) as exc:
            raise EulerFaultAgentError(f"Euler fault-agent connection failed: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:
                detail = response.text
            raise EulerFaultAgentError(str(detail)[:1000] or "Euler fault-agent request failed", response.status_code)
        return response

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/healthz").json()

    def capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/capabilities").json()

    def list_jobs(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self._request("GET", "/fault-jobs", params={"limit": limit, "offset": offset}).json()

    def samples(self) -> dict[str, Any]:
        return self._request("GET", "/samples").json()

    def sample_preview(self, sample_id: str) -> dict[str, Any]:
        return self._request("GET", f"/samples/{sample_id}/preview").json()

    def reproduce_sample(self, sample_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/samples/{sample_id}/reproduce", json=payload).json()

    def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/fault-jobs", json=payload).json()

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/fault-jobs/{job_id}").json()

    def get_events(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/fault-jobs/{job_id}/events").json()

    def get_checkpoints(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/fault-jobs/{job_id}/checkpoints").json()

    def replay(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/fault-jobs/{job_id}/replay", json=payload).json()

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._request("POST", f"/fault-jobs/{job_id}/cancel", json={}).json()

    def artifact(self, job_id: str) -> httpx.Response:
        return self._request("GET", f"/fault-jobs/{job_id}/artifact")

    def save_artifact(self, job_id: str, destination: Path, max_bytes: int) -> None:
        """Bounded streaming download to a caller-owned temporary file."""
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout, follow_redirects=False) as client:
                with client.stream("GET", f"/fault-jobs/{job_id}/artifact") as response:
                    if response.status_code != 200:
                        raise EulerFaultAgentError("Euler artifact is unavailable", response.status_code)
                    total = 0
                    with destination.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise ValueError("Euler artifact exceeds import size limit")
                            handle.write(chunk)
        except (httpx.HTTPError, OSError) as exc:
            raise EulerFaultAgentError(f"Euler artifact download failed: {exc}") from exc
