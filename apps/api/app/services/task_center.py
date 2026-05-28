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


def _run_sort_key(item):
    run = item.run
    return (run.platform if run is not None else "", *_datetime_asc_key(run.created_at if run is not None else None), item.id or 0)


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
    payload["collection_runs"] = [
        {
            "run_id": link.run.run_id,
            "platform": link.run.platform,
            "query_key": link.run.query_key,
            "model_name": link.run.model_name,
            "series_id": link.run.series_id,
            "status": link.run.status,
            "mode": link.run.mode,
            "agent_id": link.run.agent_id,
            "failure_category": link.run.failure_category,
            "output_path": link.run.output_path,
            "created_at": _dt(link.run.created_at),
            "started_at": _dt(link.run.started_at),
            "finished_at": _dt(link.run.finished_at),
            "events": [
                {
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "payload": event.payload_json or {},
                    "created_at": _dt(event.created_at),
                }
                for event in sorted(link.run.collector_events, key=_created_at_sort_key)
            ],
        }
        for link in sorted(task.collection_run_links, key=_run_sort_key)
        if link.run is not None
    ]
    return payload
