from __future__ import annotations

from datetime import datetime

from app.models import Task


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


def _task_sort_key(task: Task):
    return (task.created_at is None, _datetime_desc_key(task.created_at), task.task_id)


def _position_sort_key(item):
    return (item.position, item.id or 0)


def _created_at_sort_key(item):
    return (item.created_at is None, item.created_at or datetime.min, item.id or 0)


def _task_base(task: Task) -> dict:
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "display_name": task.display_name,
        "status": task.status,
        "current_stage": task.current_stage,
        "degraded": bool(task.degraded),
        "upgraded_to_full": bool(task.upgraded_to_full),
        "eta_seconds": task.eta_seconds,
        "eta_reason": task.eta_reason,
        "created_at": _dt(task.created_at),
        "completed_at": _dt(task.completed_at),
    }


def task_list_payload(tasks: list[Task]) -> list[dict]:
    return [_task_base(task) for task in sorted(tasks, key=_task_sort_key)]


def task_detail_payload(task: Task) -> dict:
    payload = _task_base(task)
    payload["vehicles"] = [
        {
            "task_vehicle_id": vehicle.id,
            "position": vehicle.position,
            "query": vehicle.query,
            "model_name": vehicle.model_name,
            "autohome_series_id": vehicle.autohome_series_id,
            "dcd_series_id": vehicle.dcd_series_id,
            "status": vehicle.status,
        }
        for vehicle in sorted(task.vehicles, key=_position_sort_key)
    ]
    payload["events"] = [
        {
            "event_id": event.id,
            "event_type": event.event_type,
            "payload": event.payload_json or {},
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
            "created_at": _dt(artifact.created_at),
        }
        for artifact in sorted(task.artifacts, key=_created_at_sort_key)
    ]
    return payload
