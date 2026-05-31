from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.collector_client import (  # noqa: E402
    AUTO_RETRY_FAILURES,
    STOP_RETRY_FAILURES,
    CollectorClient,
    is_stop_retry_failure,
    should_auto_retry_failure,
)
from worker_app.collector_models import CollectorRunRequest  # noqa: E402


def run_request(run_id: str = "run-1") -> CollectorRunRequest:
    return CollectorRunRequest(
        run_id=run_id,
        task_id="task-1",
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="s123",
        mode="full_refresh",
        known_links=["https://example.test/known"],
        resume_cursor={"page": 1},
        max_scan_pages=3,
        stop_after_known_pages=1,
    )


def status_payload(run_id: str = "run-1", status: str = "succeeded") -> dict:
    return {
        "run_id": run_id,
        "platform": "autohome",
        "status": status,
        "progress_current": 3,
        "progress_total": 3,
        "events": [
            {
                "event_type": "run_succeeded",
                "message": "run completed",
                "progress_current": 3,
                "progress_total": 3,
                "payload": {"output_path": "/tmp/run-1.json"},
            }
        ],
        "resume_cursor": {"page": 3},
        "failure_category": None,
        "output_path": "/tmp/run-1.json",
    }


def test_submit_run_sends_expected_json_and_parses_status() -> None:
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/runs"
        assert request.url.host == "collector.test"
        assert json.loads(request.content) == {
            "run_id": "run-1",
            "task_id": "task-1",
            "platform": "autohome",
            "query_key": "测试车",
            "model_name": "测试车",
            "series_id": "s123",
            "mode": "full_refresh",
            "known_links": ["https://example.test/known"],
            "resume_cursor": {"page": 1},
            "max_scan_pages": 3,
            "stop_after_known_pages": 1,
        }
        return httpx.Response(200, json=status_payload())

    client = CollectorClient(
        "https://collector.test/", transport=httpx.MockTransport(handler)
    )

    status = client.submit_run(run_request())

    assert len(seen_requests) == 1
    assert status.run_id == "run-1"
    assert status.status == "succeeded"
    assert status.events[0].event_type == "run_succeeded"
    assert status.output_path == "/tmp/run-1.json"


def test_submit_run_sends_assigned_agent_id_when_present() -> None:
    seen_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_payloads.append(json.loads(request.content))
        return httpx.Response(200, json=status_payload("run-agent"))

    client = CollectorClient(
        "https://collector.test/", transport=httpx.MockTransport(handler)
    )
    request = run_request("run-agent")
    request.agent_id = "autohome-3"

    status = client.submit_run(request)

    assert status.run_id == "run-agent"
    assert seen_payloads[0]["agent_id"] == "autohome-3"


def test_get_run_sends_expected_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/runs/run-get"
        return httpx.Response(200, json=status_payload("run-get"))

    client = CollectorClient(
        "https://collector.test", transport=httpx.MockTransport(handler)
    )

    status = client.get_run("run-get")

    assert status.run_id == "run-get"


def test_cancel_run_sends_expected_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/runs/run-cancel/cancel"
        return httpx.Response(
            200, json=status_payload("run-cancel", "cancel_requested")
        )

    client = CollectorClient(
        "https://collector.test", transport=httpx.MockTransport(handler)
    )

    status = client.cancel_run("run-cancel")

    assert status.run_id == "run-cancel"
    assert status.status == "cancel_requested"


def test_conflict_response_propagates_as_http_status_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            request=request,
            json={"detail": "run id already exists with different request"},
        )

    client = CollectorClient(
        "https://collector.test", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.submit_run(run_request())

    assert exc_info.value.response.status_code == 409


@pytest.mark.parametrize("category", sorted(AUTO_RETRY_FAILURES))
def test_auto_retry_failures_are_retryable(category: str) -> None:
    assert should_auto_retry_failure(category) is True
    assert is_stop_retry_failure(category) is False


@pytest.mark.parametrize("category", sorted(STOP_RETRY_FAILURES))
def test_stop_retry_failures_are_not_retryable(category: str) -> None:
    assert should_auto_retry_failure(category) is False
    assert is_stop_retry_failure(category) is True


@pytest.mark.parametrize("category", [None, "parse_error"])
def test_unknown_failure_categories_are_not_retryable(category: str | None) -> None:
    assert should_auto_retry_failure(category) is False
    assert is_stop_retry_failure(category) is False


def test_run_request_validation_rejects_empty_ids_and_invalid_scan_counts() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CollectorRunRequest(
            run_id="",
            task_id="",
            platform="autohome",
            query_key="",
            model_name="",
            series_id="",
            mode="full_refresh",
            max_scan_pages=0,
            stop_after_known_pages=-1,
        )

    invalid_fields = {error["loc"][0] for error in exc_info.value.errors()}
    assert {
        "run_id",
        "task_id",
        "query_key",
        "model_name",
        "series_id",
        "max_scan_pages",
        "stop_after_known_pages",
    }.issubset(invalid_fields)
