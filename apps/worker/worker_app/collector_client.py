from __future__ import annotations

import httpx

from worker_app.collector_models import CollectorRunRequest, CollectorRunStatus


AUTO_RETRY_FAILURES = {
    "agent_busy",
    "timeout",
    "network_error",
    "target_5xx",
    "anti_bot_or_captcha",
    "page_load_error",
    "empty_data",
    "collector_missing_result",
    "collector_pending_timeout",
    "collector_progress_stalled",
    "rate_limited",
    "resource_pressure",
}
STOP_RETRY_FAILURES = {
    "series_not_found",
    "schema_changed",
    "config_error",
    "collector_version_mismatch",
    "output_schema_mismatch",
}


def should_auto_retry_failure(category: str | None) -> bool:
    return category in AUTO_RETRY_FAILURES


def is_stop_retry_failure(category: str | None) -> bool:
    return category in STOP_RETRY_FAILURES


class CollectorClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def submit_run(self, request: CollectorRunRequest) -> CollectorRunStatus:
        with self._client() as client:
            response = client.post(
                f"{self.base_url}/runs", json=request.model_dump(mode="json", exclude_none=True)
            )
            response.raise_for_status()
            return CollectorRunStatus.model_validate(response.json())

    def get_run(self, run_id: str) -> CollectorRunStatus:
        with self._client() as client:
            response = client.get(f"{self.base_url}/runs/{run_id}")
            response.raise_for_status()
            return CollectorRunStatus.model_validate(response.json())

    def cancel_run(self, run_id: str) -> CollectorRunStatus:
        with self._client() as client:
            response = client.post(f"{self.base_url}/runs/{run_id}/cancel")
            response.raise_for_status()
            return CollectorRunStatus.model_validate(response.json())

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=self.timeout_seconds)
