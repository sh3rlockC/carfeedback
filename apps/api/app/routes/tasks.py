from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Task, TaskArtifact, TaskEvent, TaskVehicle
from app.schemas import TaskCreateRequest, TaskCreateResponse, TaskDetailResponse, TaskListItem
from app.services.confirmed_vehicle_series import query_key, upsert_confirmed_vehicle_series
from app.services.passphrase import require_passphrase_session
from app.services.task_center import task_detail_payload, task_list_payload
from app.services.task_load import project_task_load
from app.services.task_tokens import generate_task_tokens, verify_task_token
from app.services.task_workflow_client import TaskWorkflowClient, get_task_workflow_client

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
PLATFORMS = ("autohome", "dongchedi")


def _has_passphrase_session(request: Request, settings: Settings) -> bool:
    try:
        require_passphrase_session(request, settings)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_401_UNAUTHORIZED:
            raise
        return False
    return True


def _require_manage_token(task: Task, manage_token: str | None) -> None:
    if not manage_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="valid manage token required")
    if not verify_task_token(
        manage_token,
        task.manage_token_hash,
        expires_at=task.manage_token_expires_at,
        revoked_at=task.manage_token_revoked_at,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="valid manage token required")


def _validate_vehicle_count(payload: TaskCreateRequest) -> None:
    vehicle_count = len(payload.vehicles)
    if payload.task_type == "single" and vehicle_count != 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="single tasks require exactly one vehicle")
    if payload.task_type == "comparison" and not 2 <= vehicle_count <= 5:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="comparison tasks require 2 to 5 vehicles")


def _unique_platforms(platforms: list[str]) -> list[str]:
    selected: list[str] = []
    for platform in platforms:
        if platform not in selected:
            selected.append(platform)
    return selected


def _candidate_for_platform(vehicle, platform: str):
    selected = vehicle.selected_candidates
    return getattr(selected, platform) if selected is not None else None


def _require_vehicle_candidates(payload: TaskCreateRequest) -> None:
    normalized_queries = [query_key(vehicle.query) for vehicle in payload.vehicles]
    if payload.task_type == "comparison" and len(set(normalized_queries)) != len(normalized_queries):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="comparison vehicles must be distinct")
    for vehicle in payload.vehicles:
        enabled_platforms = _unique_platforms([platform for platform in vehicle.enabled_platforms if platform in PLATFORMS])
        if not enabled_platforms:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="at least one platform must be enabled")
        for platform in enabled_platforms:
            candidate = _candidate_for_platform(vehicle, platform)
            if candidate is None or not str(candidate.series_id or "").strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"confirmed {platform} series_id required")


def _display_name(payload: TaskCreateRequest) -> str:
    if payload.task_type == "single":
        return payload.vehicles[0].query
    return " vs ".join(vehicle.query for vehicle in payload.vehicles)


def _load_task_or_404(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return task


def _sync_single_task_vehicle_status(task: Task, status: str) -> None:
    if task.task_type == "comparison":
        return
    for vehicle in task.vehicles:
        vehicle.status = status


@router.post("", response_model=TaskCreateResponse)
def create_task(
    payload: TaskCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    workflow_client: TaskWorkflowClient = Depends(get_task_workflow_client),
) -> TaskCreateResponse:
    require_passphrase_session(request, settings)
    _validate_vehicle_count(payload)
    _require_vehicle_candidates(payload)

    tokens = generate_task_tokens()
    task = Task(
        task_type=payload.task_type,
        display_name=_display_name(payload),
        collection_mode=payload.collection_mode,
        status="queued",
        current_stage="queued",
        view_token_hash=tokens.view_token_hash,
        manage_token_hash=tokens.manage_token_hash,
        manage_token_expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(task)
    db.flush()

    task_vehicles: list[TaskVehicle] = []
    for index, vehicle in enumerate(payload.vehicles, start=1):
        enabled_platforms = _unique_platforms([platform for platform in vehicle.enabled_platforms if platform in PLATFORMS])
        selected_autohome = _candidate_for_platform(vehicle, "autohome")
        selected_dongchedi = _candidate_for_platform(vehicle, "dongchedi")
        task_vehicles.append(
            TaskVehicle(
                task_id=task.task_id,
                position=index,
                query=vehicle.query,
                model_name=vehicle.model_name or vehicle.query,
                autohome_series_id=(
                    str(selected_autohome.series_id).strip()
                    if "autohome" in enabled_platforms and selected_autohome is not None and selected_autohome.series_id
                    else None
                ),
                dcd_series_id=(
                    str(selected_dongchedi.series_id).strip()
                    if "dongchedi" in enabled_platforms and selected_dongchedi is not None and selected_dongchedi.series_id
                    else None
                ),
                enabled_platforms=enabled_platforms,
                status="queued",
            )
        )
        cache_platforms = set(vehicle.cache_confirmed_platforms or [])
        cache_candidates = {
            platform: candidate
            for platform, candidate in (("autohome", selected_autohome), ("dongchedi", selected_dongchedi))
            if platform in cache_platforms and platform in enabled_platforms and candidate is not None and candidate.series_id
        }
        if cache_candidates:
            upsert_confirmed_vehicle_series(db, query=vehicle.query, selected_candidates=cache_candidates)

    db.add_all(task_vehicles)
    db.add(
        TaskEvent(
            task_id=task.task_id,
            event_type="created",
            payload_json={
                "collection_mode": payload.collection_mode,
                "vehicles": [
                    {
                        "query": vehicle.query,
                        "enabled_platforms": _unique_platforms([platform for platform in vehicle.enabled_platforms if platform in PLATFORMS]),
                    }
                    for vehicle in payload.vehicles
                ]
            },
        )
    )
    db.commit()
    db.refresh(task)

    try:
        workflow_client.start_task(task.task_id, task.task_type)
    except Exception as exc:
        task.status = "failed"
        task.current_stage = "workflow_start_failed"
        db.add(
            TaskEvent(
                task_id=task.task_id,
                event_type="workflow_start_failed",
                payload_json={"error": str(exc)},
            )
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="task workflow unavailable",
        ) from exc

    return TaskCreateResponse(
        task_id=task.task_id,
        status=task.status,
        view_url=f"/tasks/{task.task_id}?view_token={tokens.view_token}",
        manage_url=f"/tasks/{task.task_id}/manage?manage_token={tokens.manage_token}",
    )


@router.get("", response_model=list[TaskListItem])
def list_tasks(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    require_passphrase_session(request, settings)
    tasks = db.query(Task).all()
    return task_list_payload(tasks, db)


@router.get("/load")
def get_task_load(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    require_passphrase_session(request, settings)
    return project_task_load(db)


@router.get("/{task_id}", response_model=TaskDetailResponse)
def get_task(
    task_id: str,
    request: Request,
    view_token: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    task = _load_task_or_404(db, task_id)
    if not _has_passphrase_session(request, settings):
        if not view_token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="passphrase session required")
        if not verify_task_token(
            view_token,
            task.view_token_hash,
            expires_at=None,
            revoked_at=task.view_token_revoked_at,
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="valid view token required")
    return task_detail_payload(task, db)


@router.get("/{task_id}/artifacts/{artifact_id}")
def download_task_artifact(
    task_id: str,
    artifact_id: int,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    require_passphrase_session(request, settings)
    artifact = db.get(TaskArtifact, artifact_id)
    if artifact is None or artifact.task_id != task_id or not artifact.downloadable:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")
    path = Path(artifact.path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact file missing")
    return FileResponse(path)


@router.post("/{task_id}/cancel", response_model=TaskDetailResponse)
def cancel_task(
    task_id: str,
    request: Request,
    manage_token: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    require_passphrase_session(request, settings)
    task = _load_task_or_404(db, task_id)
    _require_manage_token(task, manage_token)

    task.status = "cancelled"
    task.current_stage = "cancelled"
    _sync_single_task_vehicle_status(task, "cancelled")
    db.add(TaskEvent(task_id=task.task_id, event_type="cancel_requested", payload_json={}))
    db.commit()
    db.refresh(task)
    return task_detail_payload(task, db)


@router.post("/{task_id}/retry", response_model=TaskDetailResponse)
def retry_task(
    task_id: str,
    request: Request,
    manage_token: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    workflow_client: TaskWorkflowClient = Depends(get_task_workflow_client),
) -> dict:
    require_passphrase_session(request, settings)
    task = _load_task_or_404(db, task_id)
    _require_manage_token(task, manage_token)

    task.status = "queued"
    task.current_stage = "queued"
    _sync_single_task_vehicle_status(task, "queued")
    db.add(TaskEvent(task_id=task.task_id, event_type="manual_retry_requested", payload_json={}))
    db.commit()
    db.refresh(task)

    try:
        workflow_client.start_task(task.task_id, task.task_type)
    except Exception as exc:
        task.status = "failed"
        task.current_stage = "workflow_start_failed"
        _sync_single_task_vehicle_status(task, "failed")
        db.add(
            TaskEvent(
                task_id=task.task_id,
                event_type="workflow_start_failed",
                payload_json={"error": str(exc)},
            )
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="task workflow unavailable",
        ) from exc
    return task_detail_payload(task, db)


@router.post("/{task_id}/pause-retry", response_model=TaskDetailResponse)
def pause_retry_task(
    task_id: str,
    request: Request,
    manage_token: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    require_passphrase_session(request, settings)
    task = _load_task_or_404(db, task_id)
    _require_manage_token(task, manage_token)

    task.status = "retry_paused"
    task.current_stage = "retry_paused"
    _sync_single_task_vehicle_status(task, "retry_paused")
    db.add(TaskEvent(task_id=task.task_id, event_type="retry_paused", payload_json={}))
    db.commit()
    db.refresh(task)
    return task_detail_payload(task, db)
