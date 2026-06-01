from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import Task
from sqlalchemy.orm import Session

from app.services.eta import TASK_QUEUED_STATUSES, estimate_task_eta, TaskEtaEstimator

TASK_ACTIVE_STATUSES = TASK_QUEUED_STATUSES | {"running"}


def _dt(value):
    return value.isoformat() if value is not None else None


def _datetime_desc_key(value):
    if value is None:
        return (0, 0, 0, 0, 0, 0, 0)
    return (
        -value.year,
        -value.month,
        -value.day,
        -value.hour,
        -value.minute,
        -value.second,
        -value.microsecond,
    )


def _datetime_asc_key(value):
    if value is None:
        return (1, 0, 0, 0, 0, 0, 0)
    return (
        0,
        value.year,
        value.month,
        value.day,
        value.hour,
        value.minute,
        value.second,
        value.microsecond,
    )


def _task_sort_key(task: Task):
    return (task.created_at is None, _datetime_desc_key(task.created_at), task.task_id)


def _position_sort_key(item):
    return (item.position, item.id or 0)


def _created_at_sort_key(item):
    return (*_datetime_asc_key(item.created_at), item.id or 0)


def _eta_reason_label(*, queue_depth: int, eta_confidence: str, current_reason: str | None) -> str | None:
    if current_reason:
        return current_reason
    if queue_depth > 0:
        return f"前方 {queue_depth} 个任务"
    if eta_confidence == "history":
        return "基于历史耗时"
    return None


def _task_base(task: Task, *, eta_seconds: int | None = None, eta_reason: str | None = None) -> dict:
    issue_summary = _task_issue_summary(task)
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "display_name": task.display_name,
        "collection_mode": task.collection_mode,
        "status": task.status,
        "current_stage": task.current_stage,
        "degraded": bool(task.degraded),
        "upgraded_to_full": bool(task.upgraded_to_full),
        "issue_summary": issue_summary,
        "eta_seconds": task.eta_seconds if eta_seconds is None else eta_seconds,
        "eta_reason": task.eta_reason if eta_reason is None else eta_reason,
        "created_at": _dt(task.created_at),
        "completed_at": _dt(task.completed_at),
    }


def _queue_depth(tasks: list[Task], task: Task) -> int:
    if task.status not in TASK_QUEUED_STATUSES:
        return 0
    current_key = (*_datetime_asc_key(task.created_at), task.task_id)
    return sum(
        1
        for candidate in tasks
        if candidate.task_id != task.task_id
        and candidate.status in TASK_ACTIVE_STATUSES
        and (*_datetime_asc_key(candidate.created_at), candidate.task_id) < current_key
    )


def task_list_payload(tasks: list[Task], db: Session | None = None) -> list[dict]:
    sorted_tasks = sorted(tasks, key=_task_sort_key)
    if db is None:
        return [_task_base(task) for task in sorted_tasks]

    estimator = TaskEtaEstimator(db)
    return [
        _task_base(
            task,
            eta_seconds=(eta := estimate_task_eta(db, task, queue_depth=_queue_depth(tasks, task), estimator=estimator)).estimated_remaining_seconds,
            eta_reason=_eta_reason_label(
                queue_depth=_queue_depth(tasks, task),
                eta_confidence=eta.eta_confidence,
                current_reason=task.eta_reason,
            ),
        )
        for task in sorted_tasks
    ]


def _event_payload(event) -> dict[str, Any]:
    return event.payload_json if isinstance(event.payload_json, dict) else {}


def _failed_platforms_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("results") if isinstance(payload.get("results"), dict) else payload
    failed = (results.get("failed_platforms") if isinstance(results, dict) else []) or []
    return [item for item in failed if isinstance(item, dict)]


def _task_failed_platforms(task: Task) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for event in sorted(task.events, key=_created_at_sort_key):
        failures.extend(_failed_platforms_from_payload(_event_payload(event)))
    return failures


def _platform_name(failure: dict[str, Any]) -> str | None:
    platform = failure.get("platform")
    return str(platform) if platform else None


def _failure_category(failure: dict[str, Any]) -> str | None:
    category = failure.get("failure_category") or failure.get("error_code")
    return str(category) if category else None


def _failure_message(failure: dict[str, Any]) -> str | None:
    message = failure.get("message") or failure.get("error_message") or failure.get("reason")
    return str(message) if message else None


def _unique_strings(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _vehicle_missing_platforms(vehicle) -> list[str]:
    missing: list[str] = []
    enabled_platforms = vehicle.enabled_platforms or ["autohome", "dongchedi"]
    if "autohome" in enabled_platforms and not vehicle.autohome_series_id:
        missing.append("autohome")
    if "dongchedi" in enabled_platforms and not vehicle.dcd_series_id:
        missing.append("dongchedi")
    return missing


def _vehicle_issue(task: Task, vehicle) -> dict[str, Any]:
    failures = _task_failed_platforms(task)
    categories = _unique_strings([_failure_category(failure) for failure in failures])
    messages = _unique_strings([_failure_message(failure) for failure in failures])
    failed_platforms = _unique_strings([_platform_name(failure) for failure in failures])
    missing_platforms = _vehicle_missing_platforms(vehicle)
    if not missing_platforms:
        missing_platforms = failed_platforms

    return {
        "error_code": categories[0] if categories else None,
        "error_message": "；".join(messages) if messages else None,
        "missing_platforms": missing_platforms,
    }


def _event_summary(event) -> str | None:
    payload = _event_payload(event)
    failures = _failed_platforms_from_payload(payload)
    if failures:
        categories = _unique_strings([_failure_category(failure) for failure in failures])
        platforms = _unique_strings([_platform_name(failure) for failure in failures])
        category = categories[0] if categories else "failed"
        return f"{category}: {'、'.join(platforms)}" if platforms else category
    error_code = payload.get("error_code")
    error_message = payload.get("error_message") or payload.get("error")
    if error_code and error_message:
        return f"{error_code}: {error_message}"
    if error_code:
        return str(error_code)
    if error_message:
        return str(error_message)
    return None


def _task_issue_summary(task: Task) -> str | None:
    failures = _task_failed_platforms(task)
    if failures:
        categories = _unique_strings([_failure_category(failure) for failure in failures])
        platforms = _unique_strings([_platform_name(failure) for failure in failures])
        category = categories[0] if categories else "failed"
        return f"{category}: {'、'.join(platforms)}" if platforms else category
    if task.degraded:
        return "部分车型或平台结果降级"
    return None


def task_detail_payload(task: Task, db: Session | None = None) -> dict:
    queue_depth = 0
    eta_seconds = task.eta_seconds
    eta_reason = task.eta_reason
    if db is not None:
        active_tasks = db.query(Task).all()
        queue_depth = _queue_depth(active_tasks, task)
        eta = estimate_task_eta(db, task, queue_depth=queue_depth)
        eta_seconds = eta.estimated_remaining_seconds
        eta_reason = _eta_reason_label(
            queue_depth=queue_depth,
            eta_confidence=eta.eta_confidence,
            current_reason=task.eta_reason,
        )

    payload = _task_base(task, eta_seconds=eta_seconds, eta_reason=eta_reason)
    payload["vehicles"] = [
        {
            "task_vehicle_id": vehicle.id,
            "position": vehicle.position,
            "query": vehicle.query,
            "model_name": vehicle.model_name,
            "autohome_series_id": vehicle.autohome_series_id,
            "dcd_series_id": vehicle.dcd_series_id,
            "enabled_platforms": vehicle.enabled_platforms or ["autohome", "dongchedi"],
            "status": vehicle.status,
            "result_snapshot": vehicle.result_snapshot_json or {},
            **_vehicle_issue(task, vehicle),
        }
        for vehicle in sorted(task.vehicles, key=_position_sort_key)
    ]
    payload["events"] = [
        {
            "event_id": event.id,
            "event_type": event.event_type,
            "payload": event.payload_json or {},
            "summary": _event_summary(event),
            "created_at": _dt(event.created_at),
        }
        for event in sorted(task.events, key=_created_at_sort_key)
    ]
    payload["artifacts"] = [
        {
            "artifact_id": artifact.id,
            "artifact_type": artifact.artifact_type,
            "path": artifact.path,
            "downloadable": artifact.downloadable,
            "url": f"/api/tasks/{task.task_id}/artifacts/{artifact.id}" if artifact.downloadable else None,
            "created_at": _dt(artifact.created_at),
        }
        for artifact in sorted(task.artifacts, key=_created_at_sort_key)
    ]
    return payload
