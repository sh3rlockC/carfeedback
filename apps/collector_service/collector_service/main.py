from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import os
from threading import RLock
import time
from typing import Any

from fastapi import FastAPI, HTTPException

from collector_service.models import CollectorEvent, CollectorRunRequest, CollectorRunStatus


app = FastAPI(title="Collector Service", version="0.1.0")
_runs: dict[str, CollectorRunStatus] = {}
_run_requests: dict[str, CollectorRunRequest] = {}
_run_fingerprints: dict[str, str] = {}
_futures: dict[str, Future] = {}
_lock = RLock()
_executor = ThreadPoolExecutor(max_workers=int(os.getenv("COLLECTOR_SERVICE_MAX_WORKERS", "2")))
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resource_guard_enabled() -> bool:
    return _env_bool("COLLECTOR_SERVICE_RESOURCE_GUARD_ENABLED", False)


def reset_runs_for_tests() -> None:
    with _lock:
        _runs.clear()
        _run_requests.clear()
        _run_fingerprints.clear()
        _futures.clear()


def run_collector(request: CollectorRunRequest) -> dict[str, Any]:
    from collector_service.real_runner import run_real_collector

    return run_real_collector(request)


def _request_fingerprint(request: CollectorRunRequest) -> str:
    return json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _initial_status(request: CollectorRunRequest) -> CollectorRunStatus:
    return CollectorRunStatus(
        run_id=request.run_id,
        platform=request.platform,
        status="queued",
        progress_current=0,
        progress_total=1,
        resume_cursor=dict(request.resume_cursor),
        events=[
            CollectorEvent(
                event_type="run_queued",
                message="Collector run queued.",
                progress_current=0,
                progress_total=1,
            )
        ],
    )


def _progress_path(request: CollectorRunRequest) -> str:
    stage_name = "collecting_autohome" if request.platform == "autohome" else "collecting_dcd"
    artifact_root = os.getenv("ARTIFACT_ROOT", "/srv/koubei/jobs")
    return os.path.join(artifact_root, request.task_id, "progress", f"{stage_name}.progress.json")


def _numeric_progress(payload: dict[str, Any]) -> tuple[int, int] | None:
    source = payload.get("overall") if isinstance(payload.get("overall"), dict) else payload
    current = source.get("current")
    total = source.get("total")
    percent = source.get("percent")
    if isinstance(current, int) and isinstance(total, int) and total > 0:
        return max(current, 0), max(total, 1)
    if isinstance(percent, (int, float)):
        normalized = max(0, min(100, int(percent)))
        return normalized, 100
    return None


def _refresh_progress_from_file(status: CollectorRunStatus, request: CollectorRunRequest) -> None:
    if status.status not in {"queued", "running", "cancel_requested"}:
        return
    try:
        with open(_progress_path(request), encoding="utf-8") as progress_file:
            payload = json.load(progress_file)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    progress = _numeric_progress(payload)
    if progress is None:
        return
    status.progress_current, status.progress_total = progress


def _failure_category(error: BaseException) -> str:
    error_code = str(getattr(error, "error_code", "") or "").strip().upper()
    if error_code in {"TIMEOUT", "OPENCLAW_TIMEOUT"}:
        return "timeout"
    if error_code in {"NETWORK_ERROR", "CONNECTION_ERROR"}:
        return "network_error"
    if error_code in {"OPENCLAW_ARTIFACTS_MISSING", "ARTIFACTS_MISSING"}:
        return "collector_missing_result"
    if error_code in {"CONTRACT_ERROR", "SCHEMA_CHANGED"}:
        return "schema_changed"
    if error_code in {"CONFIG_ERROR", "COLLECTOR_VERSION_MISMATCH"}:
        return "config_error"
    message = str(error).lower()
    if "rate" in message and "limit" in message:
        return "rate_limited"
    if "busy" in message or "agent" in message and "available" in message:
        return "agent_busy"
    return "worker_error"


def _collector_resource_decision(request: CollectorRunRequest):
    from worker_app.openclaw_resource_gate import (
        OpenClawResourceSettings,
        decide_openclaw_admission,
        read_openclaw_resource_snapshot,
    )
    from worker_app.task_store import TaskStore

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for collector resource guard")

    store = TaskStore(database_url)
    # The worker has already marked this run as running before submitting it to
    # the collector service. Subtract it so the second-stage guard evaluates the
    # capacity needed to start this run now, not a phantom extra run.
    active_collections = max(store.count_running_collection_runs() - 1, 0)
    return decide_openclaw_admission(
        active_collections=active_collections,
        snapshot=read_openclaw_resource_snapshot(),
        settings=OpenClawResourceSettings.from_env(),
    )


def _fail_db_collection_run_for_resource_pressure(request: CollectorRunRequest) -> dict[str, Any]:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        return {"db_run_failed": False, "db_error_message": "DATABASE_URL is not configured"}
    try:
        from worker_app.task_store import TaskStore

        TaskStore(database_url).fail_collection_run(request.run_id, failure_category="resource_pressure")
        return {"db_run_failed": True}
    except Exception as exc:
        return {
            "db_run_failed": False,
            "db_error_code": getattr(exc, "error_code", exc.__class__.__name__),
            "db_error_message": str(exc) or exc.__class__.__name__,
        }


def _wait_for_result_artifacts(result: dict[str, Any]) -> dict[str, Any]:
    output_path = str(result.get("output_path") or "").strip()
    artifact_paths = [str(path) for path in result.get("artifact_paths") or [] if path]
    required_paths = list(dict.fromkeys([path for path in [output_path, *artifact_paths] if path]))
    if not required_paths:
        return result

    timeout_seconds = max(0.0, _env_float("COLLECTOR_SERVICE_ARTIFACT_WAIT_SECONDS", 30.0))
    poll_seconds = max(0.0, _env_float("COLLECTOR_SERVICE_ARTIFACT_POLL_SECONDS", 0.5))
    deadline = time.monotonic() + timeout_seconds
    while True:
        missing = [path for path in required_paths if not os.path.exists(path)]
        if not missing:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            error = RuntimeError("collector result artifacts missing: " + ", ".join(missing))
            setattr(error, "error_code", "ARTIFACTS_MISSING")
            raise error
        time.sleep(min(poll_seconds, remaining))


def _append_event(status: CollectorRunStatus, event_type: str, message: str, payload: dict[str, Any] | None = None) -> None:
    status.events.append(
        CollectorEvent(
            event_type=event_type,
            message=message,
            progress_current=status.progress_current,
            progress_total=status.progress_total or 1,
            payload=payload or {},
        )
    )


def _execute_run(request: CollectorRunRequest) -> None:
    with _lock:
        status = _runs.get(request.run_id)
        if status is None or status.status == "cancel_requested":
            if status is not None:
                status.status = "cancelled"
                _append_event(status, "run_cancelled", "Collector run cancelled before start.")
            return

    if _resource_guard_enabled():
        try:
            decision = _collector_resource_decision(request)
        except BaseException as exc:
            with _lock:
                status = _runs[request.run_id]
                status.status = "failed"
                status.failure_category = _failure_category(exc)
                _append_event(
                    status,
                    "run_failed",
                    "Collector resource guard failed.",
                    {
                        "error_code": getattr(exc, "error_code", exc.__class__.__name__),
                        "error_message": str(exc) or exc.__class__.__name__,
                        "failure_category": status.failure_category,
                    },
                )
            return
        if not decision.allowed:
            payload = {
                "task_id": request.task_id,
                "platform": request.platform,
                "mode": request.mode,
                "series_id": request.series_id,
                **decision.event_payload(),
                **_fail_db_collection_run_for_resource_pressure(request),
            }
            with _lock:
                status = _runs[request.run_id]
                status.status = "failed"
                status.failure_category = "resource_pressure"
                _append_event(
                    status,
                    "resource_pressure",
                    "Collector run deferred because host memory or swap watermarks are unhealthy.",
                    payload,
                )
            return

    with _lock:
        status = _runs.get(request.run_id)
        if status is None or status.status == "cancel_requested":
            if status is not None:
                status.status = "cancelled"
                _append_event(status, "run_cancelled", "Collector run cancelled before start.")
            return
        status.status = "running"
        status.progress_current = 0
        status.progress_total = 1
        _append_event(
            status,
            "run_started",
            "Collector run started.",
            {"task_id": request.task_id, "mode": request.mode, "series_id": request.series_id},
        )

    try:
        result = _wait_for_result_artifacts(run_collector(request))
    except BaseException as exc:
        with _lock:
            status = _runs[request.run_id]
            status.status = "failed"
            status.failure_category = _failure_category(exc)
            _append_event(
                status,
                "run_failed",
                "Collector run failed.",
                {
                    "error_code": str(getattr(exc, "error_code", exc.__class__.__name__)),
                    "error_message": str(exc) or exc.__class__.__name__,
                    "failure_category": status.failure_category,
                },
            )
        return

    with _lock:
        status = _runs[request.run_id]
        if status.status == "cancel_requested":
            status.status = "cancelled"
            _append_event(status, "run_cancelled", "Collector run cancelled after execution.")
            return
        status.status = "succeeded"
        status.progress_current = 1
        status.progress_total = 1
        status.output_path = str(result.get("output_path") or "")
        status.resume_cursor = dict(result.get("resume_cursor") or {})
        _append_event(
            status,
            "run_succeeded",
            "Collector run succeeded.",
            {"output_path": status.output_path, "artifact_paths": list(result.get("artifact_paths") or [])},
        )


def _start_run(request: CollectorRunRequest) -> None:
    if os.getenv("COLLECTOR_SERVICE_INLINE", "").strip().lower() in {"1", "true", "yes", "on"}:
        _execute_run(request)
        return
    _futures[request.run_id] = _executor.submit(_execute_run, request)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=CollectorRunStatus)
def create_run(request: CollectorRunRequest) -> CollectorRunStatus:
    with _lock:
        existing = _runs.get(request.run_id)
        if existing is not None:
            if _run_fingerprints.get(request.run_id) != _request_fingerprint(request):
                raise HTTPException(
                    status_code=409,
                    detail="run id already exists with different request",
                )
            return existing

        status = _initial_status(request)
        _runs[request.run_id] = status
        _run_requests[request.run_id] = request
        _run_fingerprints[request.run_id] = _request_fingerprint(request)

    _start_run(request)
    with _lock:
        _refresh_progress_from_file(_runs[request.run_id], request)
        return _runs[request.run_id]


@app.get("/runs/{run_id}", response_model=CollectorRunStatus)
def get_run(run_id: str) -> CollectorRunStatus:
    with _lock:
        status = _runs.get(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail="run not found")
        request = _run_requests.get(run_id)
        if request is not None:
            _refresh_progress_from_file(status, request)
        return status


@app.post("/runs/{run_id}/cancel", response_model=CollectorRunStatus)
def cancel_run(run_id: str) -> CollectorRunStatus:
    with _lock:
        status = _runs.get(run_id)
        if status is None:
            raise HTTPException(status_code=404, detail="run not found")
        if status.status in TERMINAL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="run already reached terminal status",
            )

        status.status = "cancel_requested"
        _append_event(status, "cancel_requested", "Collector run cancellation requested.")
        future = _futures.get(run_id)
        if future is not None:
            future.cancel()
        return status
