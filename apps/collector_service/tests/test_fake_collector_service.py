import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector_service.main import _run_requests, _runs, _wait_for_result_artifacts, app, reset_runs_for_tests  # noqa: E402
from collector_service.models import CollectorRunRequest, CollectorRunStatus  # noqa: E402


def make_client(monkeypatch) -> TestClient:
    monkeypatch.delenv("FAKE_COLLECTOR_PAGE_SECONDS", raising=False)
    monkeypatch.delenv("FAKE_COLLECTOR_FAIL_CATEGORY", raising=False)
    monkeypatch.delenv("FAKE_COLLECTOR_EMPTY_DATA", raising=False)
    reset_runs_for_tests()
    return TestClient(app)


def run_request(run_id: str = "run-1") -> dict:
    return {
        "run_id": run_id,
        "task_id": "task_1",
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


def test_post_runs_creates_run_and_is_idempotent(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "run-1.xlsx"

    def fake_collector(request):
        output_path.write_text("raw", encoding="utf-8")
        return {"output_path": str(output_path), "resume_cursor": {"page": 3}}

    monkeypatch.setattr("collector_service.main.run_collector", fake_collector)
    monkeypatch.setenv("COLLECTOR_SERVICE_INLINE", "true")
    client = make_client(monkeypatch)

    response = client.post("/runs", json=run_request())
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-1"
    assert body["platform"] == "autohome"
    assert body["status"] == "succeeded"
    assert body["progress_current"] == 1
    assert body["progress_total"] == 1
    assert body["output_path"] == str(output_path)

    duplicate = client.post("/runs", json=run_request())
    assert duplicate.status_code == 200
    assert duplicate.json() == body


def test_get_run_returns_status(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "run-get.xlsx"

    def fake_collector(request):
        output_path.write_text("raw", encoding="utf-8")
        return {"output_path": str(output_path), "resume_cursor": {"page": 3}}

    monkeypatch.setattr("collector_service.main.run_collector", fake_collector)
    monkeypatch.setenv("COLLECTOR_SERVICE_INLINE", "true")
    client = make_client(monkeypatch)
    client.post("/runs", json=run_request("run-get"))

    response = client.get("/runs/run-get")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run-get"
    assert body["status"] == "succeeded"
    assert body["resume_cursor"] == {"page": 3}


def test_get_run_refreshes_progress_from_progress_file(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    client = make_client(monkeypatch)
    request = CollectorRunRequest(**run_request("run-progress-file"))
    progress_dir = tmp_path / request.task_id / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "collecting_autohome.progress.json").write_text(
        '{"overall":{"current":42,"total":100,"percent":42}}',
        encoding="utf-8",
    )
    _runs[request.run_id] = CollectorRunStatus(
        run_id=request.run_id,
        platform=request.platform,
        status="running",
        progress_current=0,
        progress_total=1,
    )
    _run_requests[request.run_id] = request

    response = client.get(f"/runs/{request.run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["progress_current"] == 42
    assert body["progress_total"] == 100


def test_cancel_completed_run_returns_conflict_without_mutating(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "run-cancel.xlsx"

    def fake_collector(request):
        output_path.write_text("raw", encoding="utf-8")
        return {"output_path": str(output_path)}

    monkeypatch.setattr("collector_service.main.run_collector", fake_collector)
    monkeypatch.setenv("COLLECTOR_SERVICE_INLINE", "true")
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


def test_runner_failure_is_recorded_with_failure_category(monkeypatch):
    def failing_collector(request):
        raise RuntimeError("collector broke")

    monkeypatch.setattr("collector_service.main.run_collector", failing_collector)
    monkeypatch.setenv("COLLECTOR_SERVICE_INLINE", "true")
    client = make_client(monkeypatch)

    response = client.post("/runs", json=run_request("run-progress"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_category"] == "worker_error"
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


def test_runner_failure_category_uses_error_code_when_available(monkeypatch):
    class MissingArtifactsError(Exception):
        error_code = "OPENCLAW_ARTIFACTS_MISSING"

    def failing_collector(request):
        raise MissingArtifactsError("collector did not produce output")

    monkeypatch.setattr("collector_service.main.run_collector", failing_collector)
    monkeypatch.setenv("COLLECTOR_SERVICE_INLINE", "true")
    client = make_client(monkeypatch)

    response = client.post("/runs", json=run_request("run-empty"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_category"] == "collector_missing_result"


def test_service_waits_for_output_and_artifact_paths_before_success(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "raw.xlsx"
    validation_path = tmp_path / "raw.validation.json"
    attempts = {"count": 0}

    def fake_exists(path: str) -> bool:
        attempts["count"] += 1
        if attempts["count"] >= 2:
            output_path.write_text("raw", encoding="utf-8")
            validation_path.write_text("{}", encoding="utf-8")
        return Path(path).exists()

    monkeypatch.setenv("COLLECTOR_SERVICE_ARTIFACT_WAIT_SECONDS", "1")
    monkeypatch.setenv("COLLECTOR_SERVICE_ARTIFACT_POLL_SECONDS", "0")
    monkeypatch.setattr("collector_service.main.os.path.exists", fake_exists)

    result = _wait_for_result_artifacts(
        {
            "output_path": str(output_path),
            "artifact_paths": [str(output_path), str(validation_path)],
        }
    )

    assert result["output_path"] == str(output_path)
    assert output_path.exists()
    assert validation_path.exists()


def test_run_request_validation_rejects_empty_ids_and_negative_counts(monkeypatch):
    client = make_client(monkeypatch)

    invalid = run_request("")
    invalid["series_id"] = ""
    invalid["max_scan_pages"] = -1
    invalid["stop_after_known_pages"] = -1
    response = client.post("/runs", json=invalid)

    assert response.status_code == 422
    invalid_fields = {error["loc"][-1] for error in response.json()["detail"]}
    assert {"run_id", "series_id", "max_scan_pages", "stop_after_known_pages"}.issubset(invalid_fields)


def test_healthz(monkeypatch):
    client = make_client(monkeypatch)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
