from __future__ import annotations

from app.models import Task


def _dt(value):
    return value.isoformat() if value is not None else None


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
    return [_task_base(task) for task in tasks]


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
        for vehicle in sorted(task.vehicles, key=lambda item: item.position)
    ]
    payload["events"] = [
        {
            "event_id": event.id,
            "event_type": event.event_type,
            "payload": event.payload_json or {},
            "created_at": _dt(event.created_at),
        }
        for event in sorted(task.events, key=lambda item: item.created_at)
    ]
    payload["artifacts"] = [
        {
            "artifact_id": artifact.id,
            "artifact_type": artifact.artifact_type,
            "path": artifact.path,
            "downloadable": artifact.downloadable,
            "created_at": _dt(artifact.created_at),
        }
        for artifact in sorted(task.artifacts, key=lambda item: item.created_at)
    ]
    return payload
