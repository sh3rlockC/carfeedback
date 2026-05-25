from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Task, TaskEvent, TaskVehicle
from app.schemas import TaskCreateRequest, TaskCreateResponse, TaskDetailResponse, TaskListItem
from app.services.passphrase import require_passphrase_session
from app.services.task_center import task_detail_payload, task_list_payload
from app.services.task_tokens import generate_task_tokens, verify_task_token
from app.services.task_workflow_client import TaskWorkflowClient, get_task_workflow_client

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _has_passphrase_session(request: Request, settings: Settings) -> bool:
    try:
        require_passphrase_session(request, settings)
    except HTTPException:
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


def _display_name(payload: TaskCreateRequest) -> str:
    if payload.task_type == "single":
        return payload.vehicles[0].query
    return " vs ".join(vehicle.query for vehicle in payload.vehicles)


def _load_task_or_404(db: Session, task_id: str) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return task


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

    tokens = generate_task_tokens()
    task = Task(
        task_type=payload.task_type,
        display_name=_display_name(payload),
        status="queued",
        current_stage="queued",
        view_token_hash=tokens.view_token_hash,
        manage_token_hash=tokens.manage_token_hash,
        manage_token_expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(task)
    db.flush()

    db.add_all(
        [
            TaskVehicle(
                task_id=task.task_id,
                position=index,
                query=vehicle.query,
                model_name=vehicle.query,
                status="queued",
            )
            for index, vehicle in enumerate(payload.vehicles, start=1)
        ]
    )
    db.add(TaskEvent(task_id=task.task_id, event_type="created", payload_json={}))
    db.commit()
    db.refresh(task)

    workflow_client.start_task(task.task_id)

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
    return task_list_payload(tasks)


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
    return task_detail_payload(task)


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
    db.add(TaskEvent(task_id=task.task_id, event_type="cancel_requested", payload_json={}))
    db.commit()
    db.refresh(task)
    return task_detail_payload(task)


@router.post("/{task_id}/retry", response_model=TaskDetailResponse)
def retry_task(
    task_id: str,
    request: Request,
    manage_token: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    require_passphrase_session(request, settings)
    task = _load_task_or_404(db, task_id)
    _require_manage_token(task, manage_token)

    task.status = "queued"
    task.current_stage = "queued"
    db.add(TaskEvent(task_id=task.task_id, event_type="manual_retry_requested", payload_json={}))
    db.commit()
    db.refresh(task)
    return task_detail_payload(task)


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

    db.add(TaskEvent(task_id=task.task_id, event_type="retry_paused", payload_json={}))
    db.commit()
    db.refresh(task)
    return task_detail_payload(task)
