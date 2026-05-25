import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector_service.main import _runs, app, reset_runs_for_tests  # noqa: E402
from collector_service.models import CollectorRunStatus  # noqa: E402


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("FAKE_COLLECTOR_PAGE_SECONDS", raising=False)
    monkeypatch.delenv("FAKE_COLLECTOR_FAIL_CATEGORY", raising=False)
    monkeypatch.delenv("FAKE_COLLECTOR_EMPTY_DATA", raising=False)
    reset_runs_for_tests()
    return TestClient(app)


def run_request(run_id: str = "run-1") -> dict:
    return {
        "run_id": run_id,
        "platform": "autohome",
        "series_id": "s123",
        "mode": "full_refresh",
        "known_links": ["https://example.test/known"],
        "resume_cursor": {"page": 1},
        "max_scan_pages": 3,
        "stop_after_known_pages": 1,
    }


def test_post_runs_creates_run_and_is_idempotent(monkeypatch):
    client = make_client(monkeypatch)

    response = client.post("/runs", json=run_request())
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-1"
    assert body["platform"] == "autohome"
    assert body["status"] == "succeeded"
    assert body["progress_current"] == 3
    assert body["progress_total"] == 3
    assert body["output_path"].endswith("/run-1.json")

    duplicate = client.post("/runs", json=run_request())
    assert duplicate.status_code == 200
    assert duplicate.json() == body


def test_get_run_returns_status(monkeypatch):
    client = make_client(monkeypatch)
    client.post("/runs", json=run_request("run-get"))

    response = client.get("/runs/run-get")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-get"
    assert body["status"] == "succeeded"
    assert body["resume_cursor"] == {"page": 3}


def test_cancel_completed_run_returns_conflict_without_mutating(monkeypatch):
    client = make_client(monkeypatch)
    created = client.post("/runs", json=run_request("run-cancel")).json()

    response = client.post("/runs/run-cancel/cancel")

    assert response.status_code == 409
    assert response.json() == {"detail": "run already reached terminal status"}
    status = client.get("/runs/run-cancel").json()
    assert status["status"] == "succeeded"
    assert len(status["events"]) == len(created["events"])
    assert status["events"][-1]["event_type"] == "run_succeeded"


def test_cancel_non_terminal_run_marks_cancellation_requested(monkeypatch):
    client = make_client(monkeypatch)
    _runs["run-cancel-open"] = CollectorRunStatus(
        run_id="run-cancel-open",
        platform="autohome",
        status="running",
        progress_current=1,
        progress_total=3,
    )

    response = client.post("/runs/run-cancel-open/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancel_requested"
    assert body["events"][-1]["event_type"] == "cancel_requested"


def test_fake_runner_emits_progress_events_and_final_status(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setenv("FAKE_COLLECTOR_PAGE_SECONDS", "0.25")

    response = client.post("/runs", json=run_request("run-progress"))

    assert response.status_code == 200
    body = response.json()
    progress_events = [
        event for event in body["events"] if event["event_type"] == "page_scanned"
    ]
    assert [event["progress_current"] for event in progress_events] == [1, 2, 3]
    assert all(event["progress_total"] == 3 for event in progress_events)
    assert all(
        event["payload"]["page_seconds"] == "0.25" for event in progress_events
    )
    assert body["events"][-1]["event_type"] == "run_succeeded"
    assert body["status"] == "succeeded"


def test_fake_runner_uses_failure_category_from_env(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setenv("FAKE_COLLECTOR_FAIL_CATEGORY", "rate_limited")

    response = client.post("/runs", json=run_request("run-fail"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_category"] == "rate_limited"
    assert body["events"][-1]["event_type"] == "run_failed"


def test_duplicate_run_id_with_different_request_returns_conflict(monkeypatch):
    client = make_client(monkeypatch)
    response = client.post("/runs", json=run_request("run-conflict"))
    assert response.status_code == 200

    conflicting_request = run_request("run-conflict")
    conflicting_request["series_id"] = "different-series"
    response = client.post("/runs", json=conflicting_request)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "run id already exists with different request"
    }


def test_empty_data_env_fails_unless_failure_category_is_set(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setenv("FAKE_COLLECTOR_EMPTY_DATA", "yes")

    response = client.post("/runs", json=run_request("run-empty"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_category"] == "empty_data"

    monkeypatch.setenv("FAKE_COLLECTOR_FAIL_CATEGORY", "parse_error")
    response = client.post("/runs", json=run_request("run-explicit-failure"))
    assert response.json()["failure_category"] == "parse_error"


def test_run_request_validation_rejects_empty_ids_and_negative_counts(monkeypatch):
    client = make_client(monkeypatch)

    invalid = run_request("")
    invalid["series_id"] = ""
    invalid["max_scan_pages"] = -1
    invalid["stop_after_known_pages"] = -1
    response = client.post("/runs", json=invalid)

    assert response.status_code == 422
    invalid_fields = {error["loc"][-1] for error in response.json()["detail"]}
    assert {
        "run_id",
        "series_id",
        "max_scan_pages",
        "stop_after_known_pages",
    }.issubset(invalid_fields)


def test_healthz(monkeypatch):
    client = make_client(monkeypatch)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
