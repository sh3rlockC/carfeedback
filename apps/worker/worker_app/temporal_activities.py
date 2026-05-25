from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy import inspect as inspect_database
from sqlalchemy import text
from temporalio import activity

from worker_app.collector_client import should_auto_retry_failure
from worker_app.comparison_outputs import VehicleSnapshot, generate_comparison_outputs
from worker_app.job_store import ComparisonVehicleInputs, DatabaseJobStore
from worker_app.task_store import CollectionRunRecord, TaskRecord, TaskStore, TaskVehicleRecord


PLATFORM_SERIES_FIELDS = {
    "autohome": "autohome_series_id",
    "dongchedi": "dcd_series_id",
}
SUCCESS_STATUSES = {"completed", "succeeded", "success"}
FAILED_STATUSES = {"failed", "cancelled", "cancel_requested"}
ACTIVE_STATUSES = {"queued", "waiting_agent", "running", "retry_wait"}
DEFAULT_COLLECTOR_WAIT_POLL_SECONDS = 5.0
DEFAULT_COLLECTOR_WAIT_TIMEOUT_SECONDS = 2400.0
DEFAULT_COMPARISON_VEHICLE_WAIT_TIMEOUT_SECONDS = 2400.0
DEFAULT_COMPARISON_VEHICLE_WAIT_POLL_SECONDS = 5.0


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


def _safe_filename_part(value: str) -> str:
    cleaned = "".join(char if char not in {'/', '\\', ':', '*', '?', '"', '<', '>', '|'} else "_" for char in value.strip())
    return cleaned or "vehicle"


def _comparison_output_dir(comparison_id: str) -> Path:
    artifact_root = os.getenv("ARTIFACT_ROOT", "/srv/koubei/jobs")
    return Path(artifact_root).expanduser().resolve() / comparison_id / "comparisons"


def _comparison_vehicle_to_dict(vehicle: ComparisonVehicleInputs) -> dict[str, Any]:
    return {
        "id": vehicle.id,
        "position": vehicle.position,
        "query": vehicle.query,
        "model_name": vehicle.model_name,
        "status": vehicle.status,
        "source_job_id": vehicle.source_job_id,
        "child_job_id": vehicle.child_job_id,
        "selected_candidates": vehicle.selected_candidates,
    }


def _comparison_vehicle_from_dict(vehicle: dict[str, Any]) -> ComparisonVehicleInputs:
    return ComparisonVehicleInputs(
        id=int(vehicle.get("id") or vehicle.get("vehicle_id") or 0),
        position=int(vehicle.get("position") or 0),
        query=str(vehicle.get("query") or vehicle.get("model_name") or ""),
        model_name=str(vehicle.get("model_name") or vehicle.get("query") or ""),
        status=str(vehicle.get("status") or "queued"),
        source_job_id=str(vehicle["source_job_id"]) if vehicle.get("source_job_id") else None,
        child_job_id=str(vehicle["child_job_id"]) if vehicle.get("child_job_id") else None,
        selected_candidates=dict(vehicle.get("selected_candidates") or {}),
    )


def _snapshot_from_dict(snapshot: dict[str, Any]) -> VehicleSnapshot:
    return VehicleSnapshot(
        model_name=str(snapshot["model_name"]),
        source_job_id=str(snapshot["source_job_id"]),
        final_report_path=Path(str(snapshot["final_report_path"])),
        analysis_facts_path=Path(str(snapshot["analysis_facts_path"])),
        llm_metrics_path=Path(str(snapshot["llm_metrics_path"])) if snapshot.get("llm_metrics_path") else None,
    )


def _copy_snapshot_artifacts(
    *,
    vehicle: dict[str, Any],
    source_job_id: str,
    source_artifacts: dict[str, str],
    output_dir: Path,
) -> tuple[dict[str, Any], list[str]]:
    final_report = Path(source_artifacts["final_report.json"])
    analysis_facts = Path(source_artifacts["analysis_facts.jsonl"])
    if not final_report.exists() or not analysis_facts.exists():
        raise RuntimeError(f"source job missing comparison JSON artifacts: {source_job_id}")

    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = _safe_filename_part(str(vehicle.get("model_name") or vehicle.get("query") or source_job_id))
    final_target = output_dir / f"{prefix}.final_report.json"
    facts_target = output_dir / f"{prefix}.analysis_facts.jsonl"
    shutil.copy2(final_report, final_target)
    shutil.copy2(analysis_facts, facts_target)

    artifact_paths = [str(final_target), str(facts_target)]
    metrics_target = None
    metrics_source_value = source_artifacts.get("llm_metrics.json")
    if metrics_source_value:
        metrics_source = Path(metrics_source_value)
        if metrics_source.exists():
            metrics_target = output_dir / f"{prefix}.llm_metrics.json"
            shutil.copy2(metrics_source, metrics_target)
            artifact_paths.append(str(metrics_target))

    return (
        {
            "model_name": str(vehicle.get("model_name") or vehicle.get("query") or source_job_id),
            "source_job_id": source_job_id,
            "final_report_path": str(final_target),
            "analysis_facts_path": str(facts_target),
            "llm_metrics_path": str(metrics_target) if metrics_target else None,
        },
        artifact_paths,
    )


def _copy_downloadable_artifacts(*, source_paths: list[str], output_dir: Path, model_name: str) -> list[str]:
    vehicle_dir = output_dir / _safe_filename_part(model_name)
    vehicle_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source_value in source_paths:
        source = Path(source_value)
        if not source.exists() or not source.name.lower().endswith((".xlsx", ".png")):
            continue
        target = vehicle_dir / source.name
        if target.exists():
            index = 2
            while target.exists():
                target = vehicle_dir / f"{source.stem}_{index}{source.suffix}"
                index += 1
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def _table_columns(store: DatabaseJobStore, table_name: str) -> set[str]:
    try:
        return {column["name"] for column in inspect_database(store.engine).get_columns(table_name)}
    except Exception:
        return set()


def _db_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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
    async def load_comparison_task(self, comparison_id: str) -> dict:
        if not self.database_url:
            return {
                "comparison_id": comparison_id,
                "status": "loaded",
                "start_date": None,
                "end_date": None,
                "passphrase_version": "",
                "vehicles": [],
            }
        store = DatabaseJobStore(self.database_url)
        inputs = store.fetch_comparison_inputs(comparison_id)
        store.mark_comparison_running(comparison_id)
        return {
            "comparison_id": inputs.comparison_id,
            "status": "running",
            "start_date": inputs.start_date,
            "end_date": inputs.end_date,
            "passphrase_version": inputs.passphrase_version,
            "vehicles": [_comparison_vehicle_to_dict(vehicle) for vehicle in inputs.vehicles],
        }

    @activity.defn
    async def ensure_vehicle_subworkflow(self, payload: dict[str, Any]) -> dict:
        comparison_id = str(payload["comparison_id"])
        vehicle = dict(payload.get("vehicle") or {})
        if not self.database_url:
            vehicle_id = vehicle.get("id") or vehicle.get("vehicle_id") or vehicle.get("position") or "vehicle"
            child_task_id = str(vehicle.get("child_job_id") or f"{comparison_id}_{vehicle_id}")
            return {
                "comparison_id": comparison_id,
                "child_task_id": child_task_id,
                "child_workflow_id": f"SingleVehicleTaskWorkflow:{child_task_id}",
                "status": "queued",
            }

        store = DatabaseJobStore(self.database_url)
        task = payload.get("task") or {}
        child_task_id = store.ensure_comparison_child_job(
            _comparison_vehicle_from_dict(vehicle),
            passphrase_version=str(task.get("passphrase_version") or ""),
        )
        return {
            "comparison_id": comparison_id,
            "child_task_id": child_task_id,
            "child_workflow_id": f"SingleVehicleTaskWorkflow:{child_task_id}",
            "status": "running",
        }

    @activity.defn
    async def wait_for_vehicle_results(self, payload: dict[str, Any]) -> dict:
        comparison_id = str(payload["comparison_id"])
        vehicle = dict(payload.get("vehicle") or {})
        subworkflow = dict(payload.get("subworkflow") or {})
        reused = bool(payload.get("reused"))
        source_job_id = str(vehicle.get("source_job_id") or subworkflow.get("source_job_id") or subworkflow.get("child_task_id") or "")
        vehicle_id = int(vehicle.get("id") or vehicle.get("vehicle_id") or 0)
        model_name = str(vehicle.get("model_name") or vehicle.get("query") or source_job_id or vehicle_id)

        if not source_job_id:
            return {
                "vehicle_id": vehicle_id,
                "model_name": model_name,
                "usable": False,
                "reason": "vehicle_source_missing",
                "reused": reused,
            }
        if not self.database_url:
            return {
                "vehicle_id": vehicle_id,
                "model_name": model_name,
                "source_job_id": source_job_id,
                "usable": True,
                "reused": reused,
                "degraded": False,
                "incomplete_sources": [],
                "labels": [],
                "snapshot": {
                    "model_name": model_name,
                    "source_job_id": source_job_id,
                    "final_report_path": "",
                    "analysis_facts_path": "",
                    "llm_metrics_path": None,
                },
                "artifact_paths": [],
            }

        store = DatabaseJobStore(self.database_url)
        job = await self._wait_for_job_terminal(store, source_job_id)
        if job is not None and str(job.get("status")) not in {"completed", "completed_degraded"}:
            store.mark_comparison_vehicle_status(
                vehicle_id,
                status="excluded",
                error_code=str(job.get("error_code") or "collection_failed"),
                error_message=str(job.get("error_message") or "vehicle collection failed"),
            )
            return {
                "vehicle_id": vehicle_id,
                "model_name": model_name,
                "source_job_id": source_job_id,
                "usable": False,
                "reason": str(job.get("error_message") or "vehicle collection failed"),
                "reused": reused,
            }

        source_artifacts = store.comparison_source_artifacts(source_job_id)
        if "final_report.json" not in source_artifacts or "analysis_facts.jsonl" not in source_artifacts:
            reason = f"source job missing comparison JSON artifacts: {source_job_id}"
            store.mark_comparison_vehicle_status(vehicle_id, status="excluded", error_code="missing_snapshot", error_message=reason)
            return {
                "vehicle_id": vehicle_id,
                "model_name": model_name,
                "source_job_id": source_job_id,
                "usable": False,
                "reason": reason,
                "reused": reused,
            }

        output_dir = _comparison_output_dir(comparison_id)
        snapshot, artifact_paths = _copy_snapshot_artifacts(
            vehicle=vehicle,
            source_job_id=source_job_id,
            source_artifacts=source_artifacts,
            output_dir=output_dir,
        )
        artifact_paths.extend(
            _copy_downloadable_artifacts(
                source_paths=store.comparison_downloadable_artifacts(source_job_id),
                output_dir=output_dir,
                model_name=model_name,
            )
        )
        status = "reused" if reused else "completed"
        store.mark_comparison_vehicle_status(vehicle_id, status=status, source_job_id=source_job_id)
        degraded = bool(job and str(job.get("status")) == "completed_degraded")
        upgraded_to_full = bool(job and _db_truthy(job.get("upgraded_to_full"))) or self._task_upgraded_to_full(store, source_job_id)
        return {
            "vehicle_id": vehicle_id,
            "model_name": model_name,
            "source_job_id": source_job_id,
            "usable": True,
            "reused": reused,
            "degraded": degraded,
            "upgraded_to_full": upgraded_to_full,
            "incomplete_sources": ["partial_collection"] if degraded else [],
            "labels": ["incomplete_source"] if degraded else [],
            "snapshot": snapshot,
            "artifact_paths": artifact_paths,
        }

    async def _wait_for_job_terminal(self, store: DatabaseJobStore, job_id: str) -> dict[str, Any] | None:
        timeout_seconds = max(
            0.0,
            _env_float("COMPARISON_VEHICLE_WAIT_TIMEOUT_SECONDS", DEFAULT_COMPARISON_VEHICLE_WAIT_TIMEOUT_SECONDS),
        )
        poll_seconds = max(
            0.0,
            _env_float("COMPARISON_VEHICLE_WAIT_POLL_SECONDS", DEFAULT_COMPARISON_VEHICLE_WAIT_POLL_SECONDS),
        )
        columns = _table_columns(store, "jobs")
        selected_columns = [
            column
            for column in ("status", "degraded", "upgraded_to_full", "error_code", "error_message")
            if column in columns
        ]
        if "status" not in selected_columns:
            return None
        deadline = time.monotonic() + timeout_seconds
        while True:
            with store.engine.begin() as conn:
                row = conn.execute(
                    text(
                        f"""
                        SELECT {", ".join(selected_columns)}
                        FROM jobs
                        WHERE job_id = :job_id
                        """
                    ),
                    {"job_id": job_id},
                ).mappings().first()
            if row is None:
                return None
            job = dict(row)
            if str(job.get("status")) in SUCCESS_STATUSES | FAILED_STATUSES:
                return job
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                job["status"] = "failed"
                job["error_code"] = "vehicle_result_timeout"
                job["error_message"] = "vehicle result did not finish before comparison timeout"
                return job
            await asyncio.sleep(min(poll_seconds, remaining_seconds))

    def _task_upgraded_to_full(self, store: DatabaseJobStore, task_id: str) -> bool:
        task_columns = _table_columns(store, "tasks")
        if "upgraded_to_full" in task_columns and "task_id" in task_columns:
            with store.engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT upgraded_to_full
                        FROM tasks
                        WHERE task_id = :task_id
                        """
                    ),
                    {"task_id": task_id},
                ).mappings().first()
            if row is not None and _db_truthy(row["upgraded_to_full"]):
                return True

        event_columns = _table_columns(store, "task_events")
        if {"task_id", "event_type"}.issubset(event_columns):
            with store.engine.begin() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM task_events
                        WHERE task_id = :task_id AND event_type = 'upgraded_to_full'
                        LIMIT 1
                        """
                    ),
                    {"task_id": task_id},
                ).first()
            return row is not None
        return False

    @activity.defn
    async def generate_comparison_report(self, payload: dict[str, Any]) -> dict:
        comparison_id = str(payload["comparison_id"])
        task = dict(payload.get("task") or {})
        vehicles = [dict(vehicle) for vehicle in payload.get("vehicles", []) if isinstance(vehicle, dict)]
        excluded = [dict(item) for item in payload.get("excluded", []) if isinstance(item, dict)]
        if not self.database_url:
            return {
                "report_json": {
                    "headline": "竞品口碑对比",
                    "vehicle_count": len(vehicles),
                    "vehicles": vehicles,
                    "excluded_vehicles": excluded,
                },
                "artifact_paths": [],
                "degraded": bool(excluded),
            }

        snapshots = [_snapshot_from_dict(dict(vehicle["snapshot"])) for vehicle in vehicles if isinstance(vehicle.get("snapshot"), dict)]
        result = generate_comparison_outputs(
            snapshots=snapshots,
            output_dir=_comparison_output_dir(comparison_id),
            start_date=task.get("start_date"),
            end_date=task.get("end_date"),
            env=dict(os.environ),
        )
        artifact_paths = [*sum((list(vehicle.get("artifact_paths") or []) for vehicle in vehicles), []), *list(result["artifact_paths"])]
        report_json = dict(result["report_json"])
        if excluded:
            report_json["excluded_vehicles"] = excluded
        for report_vehicle, source_vehicle in zip(report_json.get("vehicles", []), vehicles, strict=False):
            if source_vehicle.get("labels"):
                report_vehicle["labels"] = source_vehicle["labels"]
            if source_vehicle.get("incomplete_sources"):
                report_vehicle["incomplete_sources"] = source_vehicle["incomplete_sources"]
        return {
            "report_json": report_json,
            "artifact_paths": artifact_paths,
            "degraded": bool(excluded) or bool(result.get("degraded")) or any(vehicle.get("degraded") for vehicle in vehicles),
        }

    @activity.defn
    async def publish_comparison_result(self, payload: dict[str, Any]) -> dict:
        comparison_id = str(payload["comparison_id"])
        status = str(payload.get("status") or "completed")
        if self.database_url:
            store = DatabaseJobStore(self.database_url)
            if status == "failed":
                store.mark_comparison_failed(
                    comparison_id,
                    error_code=str(payload.get("error_code") or "comparison_failed"),
                    error_message=str(payload.get("error_message") or "comparison failed"),
                )
            else:
                report = dict(payload.get("report") or {})
                store.mark_comparison_completed(
                    comparison_id,
                    report_json=dict(report.get("report_json") or {}),
                    artifact_paths=list(report.get("artifact_paths") or []),
                    degraded=bool(payload.get("degraded")),
                )
        return {"comparison_id": comparison_id, "status": status}

    @activity.defn
    async def regenerate_comparison_after_upgrade(self, payload: dict[str, Any]) -> dict:
        comparison_id = str(payload["comparison_id"])
        report = await self.generate_comparison_report(payload)
        report_json = dict(report.get("report_json") or {})
        report_json["upgraded"] = True
        report_json["upgraded_vehicle_ids"] = list(payload.get("upgraded_vehicle_ids") or [])
        if self.database_url:
            DatabaseJobStore(self.database_url).mark_comparison_completed(
                comparison_id,
                report_json=report_json,
                artifact_paths=list(report.get("artifact_paths") or []),
                degraded=bool(report.get("degraded")),
            )
        return {
            "comparison_id": comparison_id,
            "status": "completed_upgraded",
            "report_json": report_json,
            "artifact_paths": list(report.get("artifact_paths") or []),
            "upgraded": True,
        }

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
