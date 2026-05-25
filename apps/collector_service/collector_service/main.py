import json

from fastapi import FastAPI, HTTPException

from collector_service.fake_runner import create_initial_status, run_fake_collector
from collector_service.models import CollectorEvent, CollectorRunRequest, CollectorRunStatus


app = FastAPI(title="Collector Service", version="0.1.0")
_runs: dict[str, CollectorRunStatus] = {}
_run_fingerprints: dict[str, str] = {}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def reset_runs_for_tests() -> None:
    _runs.clear()
    _run_fingerprints.clear()


def _request_fingerprint(request: CollectorRunRequest) -> str:
    return json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/runs", response_model=CollectorRunStatus)
def create_run(request: CollectorRunRequest) -> CollectorRunStatus:
    existing = _runs.get(request.run_id)
    if existing is not None:
        if _run_fingerprints.get(request.run_id) != _request_fingerprint(request):
            raise HTTPException(
                status_code=409,
                detail="run id already exists with different request",
            )
        return existing

    status = create_initial_status(request)
    _runs[request.run_id] = status
    _run_fingerprints[request.run_id] = _request_fingerprint(request)
    return run_fake_collector(request, status)


@app.get("/runs/{run_id}", response_model=CollectorRunStatus)
def get_run(run_id: str) -> CollectorRunStatus:
    status = _runs.get(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="run not found")
    return status


@app.post("/runs/{run_id}/cancel", response_model=CollectorRunStatus)
def cancel_run(run_id: str) -> CollectorRunStatus:
    status = _runs.get(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="run not found")
    if status.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="run already reached terminal status",
        )

    status.status = "cancel_requested"
    status.events.append(
        CollectorEvent(
            event_type="cancel_requested",
            message="Collector run cancellation requested.",
            progress_current=status.progress_current,
            progress_total=status.progress_total,
        )
    )
    return status
