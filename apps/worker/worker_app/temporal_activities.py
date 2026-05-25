from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from temporalio import activity

from worker_app.collector_client import should_auto_retry_failure
from worker_app.task_store import CollectionRunRecord, TaskRecord, TaskStore, TaskVehicleRecord


PLATFORM_SERIES_FIELDS = {
    "autohome": "autohome_series_id",
    "dongchedi": "dcd_series_id",
}
SUCCESS_STATUSES = {"completed", "succeeded", "success"}
FAILED_STATUSES = {"failed", "cancelled", "cancel_requested"}
ACTIVE_STATUSES = {"queued", "waiting_agent", "running", "retry_wait"}
DEFAULT_COLLECTOR_WAIT_POLL_SECONDS = 5.0
DEFAULT_COLLECTOR_WAIT_TIMEOUT_SECONDS = 2700.0


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _vehicle_to_dict(vehicle: TaskVehicleRecord) -> dict[str, Any]:
    return {
        "id": vehicle.id,
        "position": vehicle.position,
        "query": vehicle.query,
        "model_name": vehicle.model_name,
        "autohome_series_id": vehicle.autohome_series_id,
        "dcd_series_id": vehicle.dcd_series_id,
        "status": vehicle.status,
        "result_snapshot_json": vehicle.result_snapshot_json,
    }


def _task_to_dict(task: TaskRecord) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "display_name": task.display_name,
        "status": task.status,
        "current_stage": task.current_stage,
        "collection_mode": task.collection_mode,
        "degraded": task.degraded,
        "upgraded_to_full": task.upgraded_to_full,
        "vehicles": [_vehicle_to_dict(vehicle) for vehicle in task.vehicles],
    }


def _run_to_dict(run: CollectionRunRecord) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "platform": run.platform,
        "query_key": run.query_key,
        "model_name": run.model_name,
        "series_id": run.series_id,
        "status": run.status,
        "mode": run.mode,
        "shared_by_task_ids": run.shared_by_task_ids,
        "failure_category": run.failure_category,
        "output_path": run.output_path,
    }


def _first_vehicle(payload: dict[str, Any]) -> dict[str, Any] | None:
    vehicles = payload.get("vehicles") or []
    if not vehicles:
        return None
    vehicle = vehicles[0]
    return vehicle if isinstance(vehicle, dict) else None


class TaskActivities:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def _store(self) -> TaskStore:
        if not self.database_url:
            raise RuntimeError("database_url is required for this activity")
        return TaskStore(self.database_url)

    @activity.defn
    async def load_task(self, task_id: str) -> dict:
        if not self.database_url:
            return {"task_id": task_id, "status": "loaded", "vehicles": [], "collection_mode": "incremental"}
        return _task_to_dict(self._store().load_task(task_id))

    @activity.defn
    async def resolve_vehicle_inputs(self, payload: dict[str, Any]) -> dict:
        task_id = str(payload["task_id"])
        mode = str(payload.get("mode") or "incremental")
        vehicle = _first_vehicle(payload)
        if vehicle is None:
            return {
                "ok": False,
                "platforms": {},
                "failed_platforms": [
                    {
                        "platform": "all",
                        "failure_category": "vehicle_input_missing",
                        "retryable": False,
                        "message": "single vehicle task has no vehicle input",
                    }
                ],
            }

        platforms: dict[str, dict[str, Any]] = {}
        failed_platforms: list[dict[str, Any]] = []
        for platform, series_field in PLATFORM_SERIES_FIELDS.items():
            series_id = str(vehicle.get(series_field) or "").strip()
            if not series_id:
                failed_platforms.append(
                    {
                        "platform": platform,
                        "failure_category": "series_not_found",
                        "retryable": False,
                        "message": f"missing {series_field} for first vehicle",
                    }
                )
                continue
            platforms[platform] = {
                "task_id": task_id,
                "platform": platform,
                "query_key": str(vehicle.get("query") or vehicle.get("model_name") or "").strip(),
                "model_name": str(vehicle.get("model_name") or vehicle.get("query") or "").strip(),
                "series_id": series_id,
                "mode": mode,
            }
        return {"ok": bool(platforms), "platforms": platforms, "failed_platforms": failed_platforms}

    @activity.defn
    async def create_or_join_collection_run(self, payload: dict[str, Any]) -> dict:
        if not self.database_url:
            platform = str(payload["platform"])
            return {
                "run_id": f"{payload['task_id']}_{platform}",
                "platform": platform,
                "status": "queued",
                "shared_by_task_ids": [str(payload["task_id"])],
            }
        run = self._store().create_or_join_collection_run(
            platform=str(payload["platform"]),
            query_key=str(payload["query_key"]),
            model_name=str(payload["model_name"]),
            series_id=str(payload["series_id"]),
            mode=str(payload.get("mode") or "incremental"),
            task_id=str(payload["task_id"]),
        )
        return _run_to_dict(run)

    @activity.defn
    async def wait_for_collection_run(self, payload: dict[str, Any]) -> dict:
        results = await self.wait_for_collection_runs(
            {"task_id": payload.get("task_id"), "run_ids": [payload["run_id"]]}
        )
        return results

    @activity.defn
    async def wait_for_collection_runs(self, payload: dict[str, Any]) -> dict:
        if not self.database_url:
            return {"successful_platforms": [], "failed_platforms": [], "pending_platforms": [], "runs": {}}
        run_ids = [str(run_id) for run_id in payload.get("run_ids", [])]
        timeout_seconds = max(0.0, _env_float("COLLECTOR_WAIT_TIMEOUT_SECONDS", DEFAULT_COLLECTOR_WAIT_TIMEOUT_SECONDS))
        poll_seconds = max(0.0, _env_float("COLLECTOR_WAIT_POLL_SECONDS", DEFAULT_COLLECTOR_WAIT_POLL_SECONDS))
        deadline = time.monotonic() + timeout_seconds
        while True:
            result = self._collection_run_results(run_ids)
            if not result["pending_platforms"]:
                return result
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                return result
            await asyncio.sleep(min(poll_seconds, remaining_seconds))

    def _collection_run_results(self, run_ids: list[str]) -> dict[str, Any]:
        runs = self._store().load_collection_runs(run_ids)
        successful_platforms: list[str] = []
        failed_platforms: list[dict[str, Any]] = []
        pending_platforms: list[str] = []
        run_payloads: dict[str, dict[str, Any]] = {}
        for run in runs:
            run_payload = _run_to_dict(run)
            run_payloads[run.platform] = run_payload
            if run.status in SUCCESS_STATUSES:
                successful_platforms.append(run.platform)
            elif run.status in FAILED_STATUSES:
                failed_platforms.append(
                    {
                        "platform": run.platform,
                        "run_id": run.run_id,
                        "failure_category": run.failure_category,
                        "retryable": should_auto_retry_failure(run.failure_category),
                    }
                )
            elif run.status in ACTIVE_STATUSES:
                pending_platforms.append(run.platform)
        return {
            "successful_platforms": successful_platforms,
            "failed_platforms": failed_platforms,
            "pending_platforms": pending_platforms,
            "runs": run_payloads,
        }

    @activity.defn
    async def import_run_rows_to_corpus(self, payload: dict[str, Any]) -> dict:
        if not self.database_url:
            return {"imported_rows": 0, "platforms": {}}
        from worker_app.corpus import read_workbook_rows, upsert_platform_rows

        task = payload.get("task") or {}
        vehicle = _first_vehicle(task) or {}
        platform_results: dict[str, dict[str, Any]] = {}
        imported_rows = 0
        for run in (payload.get("results") or {}).get("runs", {}).values():
            if not isinstance(run, dict) or not run.get("output_path"):
                continue
            output_path = Path(str(run["output_path"]))
            rows = read_workbook_rows(output_path)
            result = upsert_platform_rows(
                database_url=self.database_url,
                query=str(vehicle.get("query") or run.get("query_key") or ""),
                model_name=str(vehicle.get("model_name") or run.get("model_name") or ""),
                platform=str(run["platform"]),
                series_id=str(run["series_id"]),
                job_id=str(payload["task_id"]),
                rows=rows,
            )
            imported_rows += result.inserted_count + result.updated_count
            platform_results[str(run["platform"])] = {
                "inserted_count": result.inserted_count,
                "updated_count": result.updated_count,
                "total_count": result.total_count,
            }
        return {"imported_rows": imported_rows, "platforms": platform_results}

    @activity.defn
    async def export_vehicle_workbooks(self, payload: dict[str, Any]) -> dict:
        task = payload.get("task") or {}
        vehicle = _first_vehicle(task)
        if not self.database_url or vehicle is None:
            return {"artifact_paths": [], "skipped": True}
        from worker_app.corpus import sync_vehicle_corpus_files

        corpus_root = os.getenv("KOUBEI_CORPUS_ROOT") or "/tmp/vehicle-koubei-corpus"
        result = sync_vehicle_corpus_files(
            database_url=self.database_url,
            corpus_root=corpus_root,
            query=str(vehicle.get("query") or ""),
            model_name=str(vehicle.get("model_name") or ""),
            autohome_series_id=str(vehicle.get("autohome_series_id") or ""),
            dongchedi_series_id=str(vehicle.get("dcd_series_id") or ""),
        )
        artifact_paths = [result["manifest_path"]]
        artifact_paths.extend(platform["raw_path"] for platform in result.get("platforms", {}).values())
        return {"artifact_paths": artifact_paths, "corpus": result}

    @activity.defn
    async def run_postprocess(self, payload: dict[str, Any]) -> dict:
        return {"artifact_paths": [], "skipped": True, "reason": "postprocess bridge integration deferred"}

    @activity.defn
    async def run_llm_report(self, payload: dict[str, Any]) -> dict:
        return {"artifact_paths": [], "skipped": True, "reason": "llm report integration deferred"}

    @activity.defn
    async def publish_degraded_result(self, payload: dict[str, Any]) -> dict:
        if self.database_url:
            self._store().publish_degraded_result(str(payload["task_id"]), payload)
        return {"task_id": str(payload["task_id"]), "status": "completed_degraded"}

    @activity.defn
    async def publish_full_result(self, payload: dict[str, Any]) -> dict:
        if self.database_url:
            self._store().publish_full_result(str(payload["task_id"]), payload)
        return {"task_id": str(payload["task_id"]), "status": "completed"}

    @activity.defn
    async def schedule_retry(self, payload: dict[str, Any]) -> dict:
        if self.database_url:
            self._store().schedule_retry(str(payload["task_id"]), payload)
        return {"task_id": str(payload["task_id"]), "scheduled": True}

    @activity.defn
    async def retry_failed_platforms(self, payload: dict[str, Any]) -> dict:
        if self.database_url:
            self._store().append_task_event(str(payload["task_id"]), "retry_attempted", payload)
        return {"successful_platforms": [], "failed_platforms": payload.get("failed_platforms", []), "runs": {}}

    @activity.defn
    async def mark_task_failed(self, payload: dict[str, Any]) -> dict:
        if self.database_url:
            self._store().mark_task_failed(str(payload["task_id"]), payload)
        return {"task_id": str(payload["task_id"]), "status": "failed"}

    @activity.defn
    async def cancel_task(self, payload: dict[str, Any]) -> dict:
        if not self.database_url:
            return {"detached_runs": [], "cancelled_run_ids": []}
        return self._store().cancel_task_and_detach_runs(str(payload["task_id"]))
