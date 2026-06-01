from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect as inspect_database
from sqlalchemy import text
from temporalio import activity

from worker_app.agent_pool import choose_available_agent, platform_agent_ids_from_env
from worker_app.collector_client import CollectorClient, should_auto_retry_failure
from worker_app.collector_models import CollectorRunRequest, CollectorRunStatus
from worker_app.comparison_outputs import VehicleSnapshot, generate_comparison_outputs
from worker_app.job_store import ComparisonVehicleInputs, DatabaseJobStore
from worker_app.task_store import CollectionRunRecord, TaskRecord, TaskStore, TaskVehicleRecord


PLATFORM_SERIES_FIELDS = {
    "autohome": "autohome_series_id",
    "dongchedi": "dcd_series_id",
}
COLLECTOR_STAGE_NAMES = {
    "autohome": "collecting_autohome",
    "dongchedi": "collecting_dcd",
}
COLLECTOR_SERVICE_URLS = {
    "autohome": ("AUTOHOME_COLLECTOR_SERVICE_URL", "http://autohome-collector:8100"),
    "dongchedi": ("DCD_COLLECTOR_SERVICE_URL", "http://dongchedi-collector:8100"),
}
SUCCESS_STATUSES = {"completed", "completed_degraded", "succeeded", "success"}
FAILED_STATUSES = {"failed", "cancelled", "cancel_requested"}
ACTIVE_STATUSES = {"queued", "waiting_agent", "running", "retry_wait"}
DEFAULT_COLLECTOR_WAIT_POLL_SECONDS = 5.0
DEFAULT_COLLECTOR_WAIT_TIMEOUT_SECONDS = 2400.0
DEFAULT_COMPARISON_VEHICLE_WAIT_TIMEOUT_SECONDS = 2400.0
DEFAULT_COMPARISON_VEHICLE_WAIT_POLL_SECONDS = 5.0
PLATFORM_LABELS = {
    "autohome": "汽车之家",
    "dongchedi": "懂车帝",
}


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


def _vehicle_to_dict(vehicle: TaskVehicleRecord) -> dict[str, Any]:
    return {
        "id": vehicle.id,
        "position": vehicle.position,
        "query": vehicle.query,
        "model_name": vehicle.model_name,
        "autohome_series_id": vehicle.autohome_series_id,
        "dcd_series_id": vehicle.dcd_series_id,
        "enabled_platforms": vehicle.enabled_platforms,
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


def _task_output_dir(task_id: str) -> Path:
    artifact_root = os.getenv("ARTIFACT_ROOT", "/srv/koubei/jobs")
    return Path(artifact_root).expanduser().resolve() / task_id / "outputs" / "ai"


def _comparison_vehicle_to_dict(vehicle: ComparisonVehicleInputs) -> dict[str, Any]:
    return {
        "id": vehicle.id,
        "legacy_comparison_vehicle_id": vehicle.id,
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


def _native_comparison_vehicle_to_dict(vehicle: TaskVehicleRecord) -> dict[str, Any]:
    selected_candidates: dict[str, dict[str, Any]] = {}
    if vehicle.autohome_series_id:
        selected_candidates["autohome"] = {
            "series_id": vehicle.autohome_series_id,
            "title": vehicle.model_name,
            "source": "task_vehicle",
        }
    if vehicle.dcd_series_id:
        selected_candidates["dongchedi"] = {
            "series_id": vehicle.dcd_series_id,
            "title": vehicle.model_name,
            "source": "task_vehicle",
        }
    return {
        "id": vehicle.id,
        "native_task_vehicle_id": vehicle.id,
        "position": vehicle.position,
        "query": vehicle.query,
        "model_name": vehicle.model_name,
        "status": vehicle.status,
        "source_job_id": vehicle.result_snapshot_json.get("source_job_id"),
        "child_job_id": vehicle.result_snapshot_json.get("child_task_id"),
        "child_task_id": vehicle.result_snapshot_json.get("child_task_id"),
        "autohome_series_id": vehicle.autohome_series_id,
        "dcd_series_id": vehicle.dcd_series_id,
        "enabled_platforms": vehicle.enabled_platforms,
        "selected_candidates": selected_candidates,
    }


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
    used_names: set[str] = set()
    for source_value in source_paths:
        source = Path(source_value)
        if not source.exists() or not source.name.lower().endswith((".xlsx", ".png")):
            continue
        target = vehicle_dir / source.name
        if target.name in used_names:
            index = 2
            while target.name in used_names:
                target = vehicle_dir / f"{source.stem}_{index}{source.suffix}"
                index += 1
        used_names.add(target.name)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def _platform_status_message(status: str, platform: str, row_count: int) -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    if status == "current_collected":
        return f"{label}已完成本轮新增检查，报告使用历史库与本轮新增评论，共 {row_count} 条。"
    if status == "historical_unchecked":
        return f"{label}本轮采集未成功，报告使用已入库历史评论，共 {row_count} 条；未查新增。"
    if status == "checked_empty":
        return f"{label}已完成本轮新增检查，但历史库和新增评论均为空。"
    return f"{label}本轮采集未成功且没有可用历史评论，未纳入报告。"


def _augment_report_with_platform_status(report: dict[str, Any], platform_statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not platform_statuses:
        return report
    normalized = dict(report)
    normalized["platform_source_status"] = platform_statuses
    status_blocks = []
    for platform in ("autohome", "dongchedi"):
        status = platform_statuses.get(platform)
        if not status:
            continue
        status_blocks.append(
            {
                "title": f"{PLATFORM_LABELS.get(platform, platform)}：{status.get('label') or status.get('status')}",
                "summary": _platform_status_message(
                    str(status.get("status") or ""),
                    platform,
                    int(status.get("row_count") or 0),
                ),
                "evidence_ids": [],
            }
        )
    existing_blocks = normalized.get("platform_difference_blocks")
    if not isinstance(existing_blocks, list):
        existing_blocks = []
    normalized["platform_difference_blocks"] = [*status_blocks, *existing_blocks]
    return normalized


def _write_one_pager_workbook(final_report_path: Path, output_path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    report = json.loads(final_report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        report = {}

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "一页纸"
    sheet.append(["模块", "标题", "内容"])
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    def append_row(section: str, title: str, content: str) -> None:
        sheet.append([section, title, content])

    append_row("标题", str(report.get("headline") or ""), str(report.get("executive_summary") or ""))
    for index, item in enumerate(report.get("boss_brief") or [], start=1):
        append_row("管理层摘要", f"摘要 {index}", str(item))
    for section, key in (
        ("数据状态", "platform_difference_blocks"),
        ("核心优势", "strength_blocks"),
        ("核心短板", "weakness_blocks"),
        ("行动建议", "action_blocks"),
    ):
        blocks = report.get(key) or []
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            append_row(section, str(block.get("title") or ""), str(block.get("summary") or ""))

    sheet.column_dimensions["A"].width = 16
    sheet.column_dimensions["B"].width = 28
    sheet.column_dimensions["C"].width = 100
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def _path_if_file(path: str | Path | None) -> str | None:
    if not path:
        return None
    candidate = Path(path)
    return str(candidate) if candidate.is_file() else None


def _unique_bundle_entries(entries: list[tuple[str | Path, str]]) -> list[tuple[str | Path, str]]:
    seen: set[str] = set()
    unique: list[tuple[str | Path, str]] = []
    for path, arcname in entries:
        if arcname in seen:
            continue
        seen.add(arcname)
        unique.append((path, arcname))
    return unique


def _table_columns(store: DatabaseJobStore, table_name: str) -> set[str]:
    try:
        return {column["name"] for column in inspect_database(store.engine).get_columns(table_name)}
    except Exception:
        return set()


def _db_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _json_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _failed_platforms_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("results") if isinstance(payload.get("results"), dict) else payload
    failed = (results.get("failed_platforms") if isinstance(results, dict) else []) or []
    return [item for item in failed if isinstance(item, dict)]


def _task_failure_summary(store: DatabaseJobStore, task_id: str) -> dict[str, Any]:
    event_columns = _table_columns(store, "task_events")
    if not {"task_id", "payload_json"}.issubset(event_columns):
        return {}
    with store.engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT payload_json
                FROM task_events
                WHERE task_id = :task_id
                ORDER BY id ASC
                """
            ),
            {"task_id": task_id},
        ).mappings().all()

    failures: list[dict[str, Any]] = []
    for row in rows:
        failures.extend(_failed_platforms_from_payload(_json_payload(row["payload_json"])))
    if not failures:
        return {}

    categories = [
        str(item.get("failure_category") or item.get("error_code"))
        for item in failures
        if item.get("failure_category") or item.get("error_code")
    ]
    messages = [
        str(item.get("message") or item.get("error_message") or item.get("reason"))
        for item in failures
        if item.get("message") or item.get("error_message") or item.get("reason")
    ]
    platforms = [str(item.get("platform")) for item in failures if item.get("platform")]
    return {
        "error_code": categories[0] if categories else None,
        "error_message": "；".join(messages) if messages else None,
        "missing_platforms": platforms,
    }


class TaskActivities:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    def _store(self) -> TaskStore:
        if not self.database_url:
            raise RuntimeError("database_url is required for this activity")
        return TaskStore(self.database_url)

    def _mark_task_running(self, task_id: str | None, *, stage: str = "running") -> None:
        if not task_id or not self.database_url:
            return
        self._store().mark_task_stage(task_id, stage, "running")

    def _real_single_task_pipeline_enabled(self) -> bool:
        return _env_bool("TEMPORAL_REAL_SINGLE_TASK_PIPELINE_ENABLED", False)

    def _stage_error_failure_category(self, error_code: str | None) -> str:
        normalized = (error_code or "").strip().upper()
        if normalized == "TIMEOUT":
            return "timeout"
        if normalized == "NETWORK_ERROR":
            return "network_error"
        if normalized in {"OPENCLAW_ARTIFACTS_MISSING", "ARTIFACTS_MISSING"}:
            return "collector_missing_result"
        if normalized == "CONTRACT_ERROR":
            return "schema_changed"
        if normalized == "PARSING_ERROR":
            return "page_load_error"
        return "worker_error"

    def _collector_error_failure_category(self, exc: BaseException) -> str:
        category = self._stage_error_failure_category(getattr(exc, "error_code", None))
        if category != "worker_error":
            return category
        message = str(exc).lower()
        if "rate" in message and "limit" in message:
            return "rate_limited"
        if "busy" in message or ("agent" in message and "available" in message):
            return "agent_busy"
        if "timeout" in message or "timed out" in message:
            return "timeout"
        if "connect" in message or "network" in message:
            return "network_error"
        return category

    def _fail_pending_collection_runs(self, run_ids: list[str], *, task_id: str | None = None) -> None:
        store = self._store()
        for run in store.load_collection_runs(run_ids):
            if run.status not in ACTIVE_STATUSES:
                continue
            failed = store.fail_collection_run(run.run_id, failure_category="collector_pending_timeout")
            store.record_collector_event(
                failed.run_id,
                "collector_pending_timeout",
                {
                    "task_id": task_id,
                    "platform": failed.platform,
                    "agent_id": run.agent_id,
                    "failure_category": "collector_pending_timeout",
                    "message": "Collector run did not reach a terminal status before wait timeout.",
                },
            )

    def _task_job_paths(self, task_id: str):
        from worker_app.artifacts import ensure_job_dirs

        artifact_root = os.getenv("ARTIFACT_ROOT", "/srv/koubei/jobs")
        return ensure_job_dirs(artifact_root, task_id)

    def _build_stage_command(
        self,
        *,
        task_id: str,
        model_name: str,
        autohome_series_id: str,
        dongchedi_series_id: str,
        collection_plan: dict[str, Any] | None,
        stage_name: str,
        single_platform_stage: str | None = None,
    ):
        from worker_app.stages import build_stage_commands, resolve_stage_command

        job_paths = self._task_job_paths(task_id)
        commands = build_stage_commands(
            job_paths=job_paths,
            model_name=model_name,
            autohome_series_id=autohome_series_id,
            dongchedi_series_id=dongchedi_series_id,
            collection_plan=collection_plan,
        )
        command = next(command for command in commands if command.name == stage_name)
        return resolve_stage_command(command, single_platform_stage=single_platform_stage), job_paths

    def _run_stage_command(self, *, task_id: str, stage_command) -> dict[str, Any]:
        from worker_app.openclaw_runner import build_stage_runner
        from worker_app.progress import ProgressSink

        job_paths = self._task_job_paths(task_id)
        progress_sink = ProgressSink(
            job_id=task_id,
            progress_path=job_paths.progress / "progress.json",
            stages=[stage_command.name],
        )
        result = build_stage_runner()(stage_command, job_paths, progress_sink)
        return {
            "status": result.status,
            "artifact_paths": list(result.artifact_paths),
            "output_metadata": dict(result.output_metadata),
        }

    async def _run_stage_command_async(self, *, task_id: str, stage_command) -> dict[str, Any]:
        return await asyncio.to_thread(self._run_stage_command, task_id=task_id, stage_command=stage_command)

    def _collector_client(self, platform: str) -> CollectorClient:
        env_name, default_url = COLLECTOR_SERVICE_URLS.get(platform, (f"{platform.upper()}_COLLECTOR_SERVICE_URL", ""))
        base_url = os.getenv(env_name, default_url).strip()
        if not base_url:
            raise RuntimeError(f"collector service URL is not configured for platform: {platform}")
        timeout_seconds = max(1.0, _env_float("COLLECTOR_CLIENT_TIMEOUT_SECONDS", 30.0))
        return CollectorClient(base_url, timeout_seconds=timeout_seconds)

    def _collector_request(self, run: CollectionRunRecord, owner_task_id: str) -> CollectorRunRequest:
        agent_id = None if run.agent_id and run.agent_id.startswith("collector-service:") else run.agent_id
        return CollectorRunRequest(
            run_id=run.run_id,
            task_id=owner_task_id,
            agent_id=agent_id,
            platform=run.platform,  # type: ignore[arg-type]
            query_key=run.query_key,
            model_name=run.model_name,
            series_id=run.series_id,
            mode=run.mode,  # type: ignore[arg-type]
            resume_cursor={},
            max_scan_pages=10,
            stop_after_known_pages=2,
        )

    def _apply_collector_status(
        self,
        run: CollectionRunRecord,
        status: CollectorRunStatus,
        *,
        owner_task_id: str | None,
        event_type: str,
    ) -> None:
        store = self._store()
        store.record_collector_event(
            run.run_id,
            event_type,
            {
                "task_id": owner_task_id,
                "platform": run.platform,
                "collector_status": status.status,
                "progress_current": status.progress_current,
                "progress_total": status.progress_total,
                "output_path": status.output_path,
                "failure_category": status.failure_category,
            },
        )
        if status.status == "succeeded":
            if not status.output_path:
                store.fail_collection_run(run.run_id, failure_category="collector_missing_result", resume_cursor=status.resume_cursor)
                store.record_collector_event(
                    run.run_id,
                    "collector_failed",
                    {
                        "task_id": owner_task_id,
                        "platform": run.platform,
                        "failure_category": "collector_missing_result",
                        "error_message": "collector succeeded without output_path",
                    },
                )
                return
            store.complete_collection_run(
                run.run_id,
                output_path=status.output_path,
                resume_cursor=status.resume_cursor,
            )
            store.record_collector_event(
                run.run_id,
                "collector_succeeded",
                {
                    "task_id": owner_task_id,
                    "platform": run.platform,
                    "output_path": status.output_path,
                },
            )
        elif status.status in {"failed", "cancelled", "cancel_requested"}:
            failure_category = status.failure_category or ("cancelled" if status.status != "failed" else "worker_error")
            store.fail_collection_run(run.run_id, failure_category=failure_category, resume_cursor=status.resume_cursor)
            store.record_collector_event(
                run.run_id,
                "collector_failed",
                {
                    "task_id": owner_task_id,
                    "platform": run.platform,
                    "failure_category": failure_category,
                    "collector_status": status.status,
                },
            )

    def _claim_run_agent(self, store: TaskStore, run: CollectionRunRecord) -> CollectionRunRecord | None:
        configured_agents = platform_agent_ids_from_env()
        platform_agents = configured_agents.get(run.platform, [])
        if not platform_agents:
            return store.start_collection_run(run.run_id, agent_id=f"collector-service:{run.platform}")

        busy_agents = store.running_agent_ids_by_platform()
        agent_id = choose_available_agent(run.platform, configured_agents, busy_agents)
        if agent_id is None:
            store.mark_collection_run_waiting_agent(run.run_id)
            return None
        claimed = store.claim_collection_run_with_agent(run.run_id, agent_id)
        if claimed is None:
            store.mark_collection_run_waiting_agent(run.run_id)
        return claimed

    def _claim_collection_run_for_dispatch(
        self,
        store: TaskStore,
        queued_run: CollectionRunRecord,
        *,
        task_id: str | None = None,
    ) -> tuple[CollectionRunRecord, str | None] | None:
        owner_task_id = task_id or (queued_run.shared_by_task_ids[0] if queued_run.shared_by_task_ids else None)
        started = self._claim_run_agent(store, queued_run)
        if started is None:
            if queued_run.status != "waiting_agent":
                store.record_collector_event(
                    queued_run.run_id,
                    "collector_waiting_agent",
                    {
                        "task_id": owner_task_id,
                        "platform": queued_run.platform,
                        "message": "No platform OpenClaw agent is currently available.",
                    },
                )
            return None
        if started.status != "running":
            return None
        return started, owner_task_id

    def _submit_collection_run(self, started: CollectionRunRecord, *, owner_task_id: str | None = None) -> None:
        store = self._store()
        stage_name = COLLECTOR_STAGE_NAMES.get(started.platform, f"collecting_{started.platform}")
        if owner_task_id:
            store.mark_task_stage(owner_task_id, stage_name, "running")

        try:
            if owner_task_id is None:
                raise RuntimeError(f"collection run has no owner task: {started.run_id}")
            request = self._collector_request(started, owner_task_id)
            status = self._collector_client(started.platform).submit_run(request)
            store.record_collector_event(
                started.run_id,
                "collector_submitted",
                {
                    "task_id": owner_task_id,
                    "platform": started.platform,
                    "mode": started.mode,
                    "series_id": started.series_id,
                    "agent_id": started.agent_id,
                    "collector_status": status.status,
                },
            )
            if status.status in {"succeeded", "failed", "cancelled", "cancel_requested"}:
                self._apply_collector_status(started, status, owner_task_id=owner_task_id, event_type="collector_status_polled")
        except Exception as exc:
            failure_category = self._collector_error_failure_category(exc)
            store.fail_collection_run(started.run_id, failure_category=failure_category)
            store.record_collector_event(
                started.run_id,
                "collector_failed",
                {
                    "task_id": owner_task_id,
                    "platform": started.platform,
                    "failure_category": failure_category,
                    "error_code": getattr(exc, "error_code", exc.__class__.__name__),
                    "error_message": str(exc) or exc.__class__.__name__,
                },
            )

    def _dispatch_collection_run(self, queued_run: CollectionRunRecord, *, task_id: str | None = None) -> None:
        if not self.database_url:
            return
        store = self._store()
        claimed = self._claim_collection_run_for_dispatch(store, queued_run, task_id=task_id)
        if claimed is None:
            return
        started, owner_task_id = claimed
        self._submit_collection_run(started, owner_task_id=owner_task_id)

    async def _dispatch_pending_collection_runs(self, task_id: str | None, run_ids: list[str]) -> None:
        if not self.database_url:
            return
        store = self._store()
        pending = [
            run
            for run in store.load_collection_runs(run_ids)
            if run.status in {"queued", "waiting_agent", "retry_wait"}
        ]
        claimed_runs: list[tuple[CollectionRunRecord, str | None]] = []
        for run in pending:
            claimed = await asyncio.to_thread(self._claim_collection_run_for_dispatch, store, run, task_id=task_id)
            if claimed is not None:
                claimed_runs.append(claimed)
        if claimed_runs:
            await asyncio.gather(
                *(
                    asyncio.to_thread(self._submit_collection_run, started, owner_task_id=owner_task_id)
                    for started, owner_task_id in claimed_runs
                )
            )

    def _poll_collection_run(self, run: CollectionRunRecord, *, task_id: str | None = None) -> None:
        owner_task_id = task_id or (run.shared_by_task_ids[0] if run.shared_by_task_ids else None)
        try:
            status = self._collector_client(run.platform).get_run(run.run_id)
            self._apply_collector_status(run, status, owner_task_id=owner_task_id, event_type="collector_status_polled")
        except Exception as exc:
            failure_category = self._collector_error_failure_category(exc)
            store = self._store()
            store.fail_collection_run(run.run_id, failure_category=failure_category)
            store.record_collector_event(
                run.run_id,
                "collector_failed",
                {
                    "task_id": owner_task_id,
                    "platform": run.platform,
                    "failure_category": failure_category,
                    "error_code": getattr(exc, "error_code", exc.__class__.__name__),
                    "error_message": str(exc) or exc.__class__.__name__,
                },
            )

    async def _poll_running_collection_runs(self, task_id: str | None, run_ids: list[str]) -> None:
        if not self.database_url:
            return
        running = [run for run in self._store().load_collection_runs(run_ids) if run.status == "running"]
        if running:
            await asyncio.gather(
                *(asyncio.to_thread(self._poll_collection_run, run, task_id=task_id) for run in running)
            )

    def _record_single_task_download_artifacts(self, payload: dict[str, Any]) -> None:
        if not self.database_url:
            return
        task_id = str(payload["task_id"])
        task = payload.get("task") or {}
        vehicle = _first_vehicle(task)
        if vehicle is None:
            return
        report_paths = [
            str(path)
            for path in (
                (payload.get("report_result") or {}).get("artifact_paths") or []
            )
            if path
        ]
        final_report_path = next((path for path in report_paths if path.endswith("final_report.json")), None)
        summary_path = next(
            (
                path
                for path in report_paths
                if path.endswith(".xlsx") and "词云词项清单" not in Path(path).name
            ),
            None,
        )
        if not final_report_path or not summary_path:
            return

        from sqlalchemy.orm import sessionmaker
        from worker_app.corpus import export_vehicle_merged_raw_workbook
        from worker_app.task_artifacts import create_single_task_downloads, record_task_artifacts

        download_dir = _task_output_dir(task_id).parent / "downloads"
        model_name = str(vehicle.get("model_name") or vehicle.get("query") or task_id)
        merged_raw_path = download_dir / f"{_safe_filename_part(model_name)}_merged_raw.xlsx"
        export_result = payload.get("export_result") if isinstance(payload.get("export_result"), dict) else {}
        report_platforms = [
            str(platform)
            for platform in export_result.get("report_platforms") or vehicle.get("enabled_platforms") or ["autohome", "dongchedi"]
            if str(platform) in {"autohome", "dongchedi"}
        ]
        export_vehicle_merged_raw_workbook(
            database_url=self.database_url,
            query=str(vehicle.get("query") or model_name),
            autohome_series_id=str(vehicle.get("autohome_series_id") or ""),
            dongchedi_series_id=str(vehicle.get("dcd_series_id") or ""),
            output_path=merged_raw_path,
            platforms=report_platforms,
        )
        one_pager_path = download_dir / f"{_safe_filename_part(model_name)}_one_pager.xlsx"
        _write_one_pager_workbook(Path(final_report_path), one_pager_path)

        platform_artifacts = export_result.get("platform_artifacts") if isinstance(export_result.get("platform_artifacts"), dict) else {}
        bundle_entries: list[tuple[str | Path, str]] = []
        for platform in ("autohome", "dongchedi"):
            artifact = platform_artifacts.get(platform) if isinstance(platform_artifacts.get(platform), dict) else {}
            platform_dir = platform
            for key, filename in (
                ("raw_path", "raw.xlsx"),
                ("validation_path", "validation.json"),
                ("progress_path", "progress.json"),
                ("failed_pages_path", "failed-pages.json"),
                ("status_path", "source_status.json"),
            ):
                path = _path_if_file(artifact.get(key))
                if path:
                    bundle_entries.append((path, f"{platform_dir}/{filename}"))

        terms_path = next((path for path in report_paths if path.endswith("词云词项清单.xlsx")), None)
        image_paths = [path for path in report_paths if path.lower().endswith(".png")]
        bundle_entries.extend(
            [
                (final_report_path, "report/final_report.json"),
                (summary_path, "report/summary.xlsx"),
                (one_pager_path, "report/one_pager.xlsx"),
                (merged_raw_path, "merged_raw.xlsx"),
            ]
        )
        if terms_path:
            bundle_entries.append((terms_path, "report/wordcloud_terms.xlsx"))
        for image_path in image_paths:
            bundle_entries.append((image_path, f"report/{Path(image_path).name}"))

        records = create_single_task_downloads(
            task_id=task_id,
            output_dir=download_dir,
            merged_raw_path=merged_raw_path,
            business_files=[summary_path, final_report_path],
            one_pager_path=one_pager_path,
            bundle_entries=_unique_bundle_entries(bundle_entries),
        )
        session = sessionmaker(bind=self._store().engine, future=True)()
        try:
            record_task_artifacts(session, records)
        finally:
            session.close()

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
                "task_type": "comparison",
                "status": "loaded",
                "start_date": None,
                "end_date": None,
                "passphrase_version": "",
                "vehicles": [],
            }
        if comparison_id.startswith("task_"):
            task = self._store().load_task(comparison_id)
            if task.task_type != "comparison":
                raise RuntimeError(f"task is not a comparison: {comparison_id}")
            self._store().mark_task_stage(comparison_id, "collecting_models", "running")
            return {
                "comparison_id": task.task_id,
                "task_id": task.task_id,
                "task_type": task.task_type,
                "status": "running",
                "start_date": None,
                "end_date": None,
                "passphrase_version": "",
                "vehicles": [_native_comparison_vehicle_to_dict(vehicle) for vehicle in task.vehicles],
            }
        store = DatabaseJobStore(self.database_url)
        inputs = store.fetch_comparison_inputs(comparison_id)
        store.mark_comparison_running(comparison_id)
        return {
            "comparison_id": inputs.comparison_id,
            "task_type": "legacy_comparison",
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

        task = payload.get("task") or {}
        child_task_id = self._store().ensure_comparison_child_task(
            vehicle,
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
        task = await self._wait_for_task_terminal(store, source_job_id)
        if task is not None:
            return self._vehicle_result_from_task(
                store=store,
                comparison_id=comparison_id,
                vehicle=vehicle,
                vehicle_id=vehicle_id,
                model_name=model_name,
                task_id=source_job_id,
                task=task,
                reused=reused,
            )

        job = await self._wait_for_job_terminal(store, source_job_id)
        if job is not None and str(job.get("status")) not in {"completed", "completed_degraded"}:
            self._mark_comparison_vehicle_status(
                store=store,
                vehicle=vehicle,
                vehicle_id=vehicle_id,
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
            self._mark_comparison_vehicle_status(
                store=store,
                vehicle=vehicle,
                vehicle_id=vehicle_id,
                status="excluded",
                error_code="missing_snapshot",
                error_message=reason,
            )
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
        self._mark_comparison_vehicle_status(
            store=store,
            vehicle=vehicle,
            vehicle_id=vehicle_id,
            status=status,
            source_job_id=source_job_id,
            child_task_id=source_job_id if source_job_id.startswith("task_") else None,
        )
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

    def _mark_comparison_vehicle_status(
        self,
        *,
        store: DatabaseJobStore,
        vehicle: dict[str, Any],
        vehicle_id: int,
        status: str,
        source_job_id: str | None = None,
        child_task_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        native_vehicle_id = vehicle.get("native_task_vehicle_id")
        if native_vehicle_id:
            TaskStore(self.database_url).mark_task_vehicle_status(
                int(native_vehicle_id),
                status=status,
                source_job_id=source_job_id,
                child_task_id=child_task_id,
                error_code=error_code,
                error_message=error_message,
            )
            return
        legacy_vehicle_id = int(vehicle.get("legacy_comparison_vehicle_id") or vehicle_id)
        store.mark_comparison_vehicle_status(
            legacy_vehicle_id,
            status=status,
            source_job_id=source_job_id,
            child_job_id=child_task_id,
            error_code=error_code,
            error_message=error_message,
        )

    def _vehicle_result_from_task(
        self,
        *,
        store: DatabaseJobStore,
        comparison_id: str,
        vehicle: dict[str, Any],
        vehicle_id: int,
        model_name: str,
        task_id: str,
        task: dict[str, Any],
        reused: bool,
    ) -> dict[str, Any]:
        if str(task.get("status")) not in {"completed", "completed_degraded"}:
            failure = _task_failure_summary(store, task_id)
            error_code = str(failure.get("error_code") or task.get("error_code") or "collection_failed")
            error_message = str(failure.get("error_message") or task.get("error_message") or "vehicle collection failed")
            self._mark_comparison_vehicle_status(
                store=store,
                vehicle=vehicle,
                vehicle_id=vehicle_id,
                status="excluded",
                error_code=error_code,
                error_message=error_message,
            )
            return {
                "vehicle_id": vehicle_id,
                "model_name": model_name,
                "source_job_id": task_id,
                "usable": False,
                "reason": error_message,
                "error_code": error_code,
                "error_message": error_message,
                "missing_platforms": list(failure.get("missing_platforms") or []),
                "reused": reused,
            }

        source_artifacts = self._task_source_artifacts(store, task_id)
        if "final_report.json" not in source_artifacts or "analysis_facts.jsonl" not in source_artifacts:
            reason = f"source task missing comparison JSON artifacts: {task_id}"
            self._mark_comparison_vehicle_status(
                store=store,
                vehicle=vehicle,
                vehicle_id=vehicle_id,
                status="excluded",
                error_code="missing_snapshot",
                error_message=reason,
            )
            return {
                "vehicle_id": vehicle_id,
                "model_name": model_name,
                "source_job_id": task_id,
                "usable": False,
                "reason": reason,
                "reused": reused,
            }

        snapshot, artifact_paths = _copy_snapshot_artifacts(
            vehicle=vehicle,
            source_job_id=task_id,
            source_artifacts=source_artifacts,
            output_dir=_comparison_output_dir(comparison_id),
        )
        artifact_paths.extend(
            _copy_downloadable_artifacts(
                source_paths=self._task_downloadable_artifacts(store, task_id),
                output_dir=_comparison_output_dir(comparison_id),
                model_name=model_name,
            )
        )
        status = "reused" if reused else "completed"
        self._mark_comparison_vehicle_status(
            store=store,
            vehicle=vehicle,
            vehicle_id=vehicle_id,
            status=status,
            source_job_id=task_id,
            child_task_id=task_id,
        )
        upgraded_to_full = _db_truthy(task.get("upgraded_to_full")) or self._task_upgraded_to_full(store, task_id)
        degraded = str(task.get("status")) == "completed_degraded" or (
            _db_truthy(task.get("degraded")) and not upgraded_to_full
        )
        return {
            "vehicle_id": vehicle_id,
            "model_name": model_name,
            "source_job_id": task_id,
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

    async def _wait_for_task_terminal(self, store: DatabaseJobStore, task_id: str) -> dict[str, Any] | None:
        columns = _table_columns(store, "tasks")
        selected_columns = [
            column
            for column in ("status", "degraded", "upgraded_to_full", "error_code", "error_message")
            if column in columns
        ]
        if "status" not in selected_columns:
            return None
        timeout_seconds = max(
            0.0,
            _env_float("COMPARISON_VEHICLE_WAIT_TIMEOUT_SECONDS", DEFAULT_COMPARISON_VEHICLE_WAIT_TIMEOUT_SECONDS),
        )
        poll_seconds = max(
            0.0,
            _env_float("COMPARISON_VEHICLE_WAIT_POLL_SECONDS", DEFAULT_COMPARISON_VEHICLE_WAIT_POLL_SECONDS),
        )
        deadline = time.monotonic() + timeout_seconds
        while True:
            with store.engine.begin() as conn:
                row = conn.execute(
                    text(
                        f"""
                        SELECT {", ".join(selected_columns)}
                        FROM tasks
                        WHERE task_id = :task_id
                        """
                    ),
                    {"task_id": task_id},
                ).mappings().first()
            if row is None:
                return None
            task = dict(row)
            if str(task.get("status")) in {"completed", "completed_degraded", "failed", "cancelled", "cancel_requested"}:
                return task
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                task["status"] = "failed"
                task["error_code"] = "vehicle_result_timeout"
                task["error_message"] = "vehicle result did not finish before comparison timeout"
                return task
            await asyncio.sleep(min(poll_seconds, remaining_seconds))

    def _task_source_artifacts(self, store: DatabaseJobStore, task_id: str) -> dict[str, str]:
        artifacts = self._task_artifacts_by_suffix(store, task_id)
        if "final_report.json" in artifacts and "analysis_facts.jsonl" in artifacts:
            return artifacts
        snapshot = self._task_result_snapshot(store, task_id)
        if snapshot:
            if snapshot.get("final_report_path"):
                artifacts["final_report.json"] = str(snapshot["final_report_path"])
            if snapshot.get("analysis_facts_path"):
                artifacts["analysis_facts.jsonl"] = str(snapshot["analysis_facts_path"])
            if snapshot.get("llm_metrics_path"):
                artifacts["llm_metrics.json"] = str(snapshot["llm_metrics_path"])
        return artifacts

    def _task_result_snapshot(self, store: DatabaseJobStore, task_id: str) -> dict[str, Any]:
        if not {"task_id", "result_snapshot_json"}.issubset(_table_columns(store, "task_vehicles")):
            return {}
        with store.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT result_snapshot_json
                    FROM task_vehicles
                    WHERE task_id = :task_id
                    ORDER BY position ASC, id ASC
                    LIMIT 1
                    """
                ),
                {"task_id": task_id},
            ).mappings().first()
        if row is None:
            return {}
        payload = row["result_snapshot_json"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return {}
        if not isinstance(payload, dict):
            return {}
        snapshot = payload.get("comparison_snapshot") or payload.get("snapshot") or payload
        return dict(snapshot) if isinstance(snapshot, dict) else {}

    def _task_artifacts_by_suffix(self, store: DatabaseJobStore, task_id: str) -> dict[str, str]:
        if not {"task_id", "path"}.issubset(_table_columns(store, "task_artifacts")):
            return {}
        with store.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT path
                    FROM task_artifacts
                    WHERE task_id = :task_id
                    ORDER BY id ASC
                    """
                ),
                {"task_id": task_id},
            ).mappings().all()
        artifacts: dict[str, str] = {}
        for row in rows:
            path = str(row["path"])
            for suffix in ("final_report.json", "analysis_facts.jsonl", "llm_metrics.json"):
                if path.endswith(suffix):
                    artifacts[suffix] = path
        return artifacts

    def _task_downloadable_artifacts(self, store: DatabaseJobStore, task_id: str) -> list[str]:
        if not {"task_id", "path"}.issubset(_table_columns(store, "task_artifacts")):
            return []
        with store.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT path
                    FROM task_artifacts
                    WHERE task_id = :task_id
                    ORDER BY id ASC
                    """
                ),
                {"task_id": task_id},
            ).mappings().all()
        return [str(row["path"]) for row in rows if str(row["path"]).lower().endswith((".xlsx", ".png"))]

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
            if comparison_id.startswith("task_"):
                self._store().publish_comparison_result(comparison_id, payload, status=status)
                return {"comparison_id": comparison_id, "status": status}
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
    async def mark_comparison_comparing(self, payload: dict[str, Any]) -> dict:
        comparison_id = str(payload["comparison_id"])
        if self.database_url:
            if comparison_id.startswith("task_"):
                self._store().mark_task_stage(comparison_id, "comparing", "running")
                return {"comparison_id": comparison_id, "status": "running", "current_stage": "comparing"}
            DatabaseJobStore(self.database_url).mark_comparison_comparing(comparison_id)
        return {"comparison_id": comparison_id, "status": "running", "current_stage": "comparing"}

    @activity.defn
    async def regenerate_comparison_after_upgrade(self, payload: dict[str, Any]) -> dict:
        comparison_id = str(payload["comparison_id"])
        report = await self.generate_comparison_report(payload)
        report_json = dict(report.get("report_json") or {})
        report_json["upgraded"] = True
        report_json["upgraded_vehicle_ids"] = list(payload.get("upgraded_vehicle_ids") or [])
        if self.database_url:
            if comparison_id.startswith("task_"):
                self._store().publish_comparison_result(
                    comparison_id,
                    {**payload, "report": {**report, "report_json": report_json}, "degraded": bool(report.get("degraded"))},
                    status="completed",
                )
                return {
                    "comparison_id": comparison_id,
                    "status": "completed_upgraded",
                    "report_json": report_json,
                    "artifact_paths": list(report.get("artifact_paths") or []),
                    "upgraded": True,
                }
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

        raw_enabled_platforms = vehicle.get("enabled_platforms") or list(PLATFORM_SERIES_FIELDS)
        enabled_platforms = [
            str(platform)
            for platform in raw_enabled_platforms
            if str(platform) in PLATFORM_SERIES_FIELDS
        ]
        if not enabled_platforms:
            return {
                "ok": False,
                "platforms": {},
                "failed_platforms": [
                    {
                        "platform": "all",
                        "failure_category": "platform_selection_missing",
                        "retryable": False,
                        "message": "single vehicle task has no enabled platform",
                    }
                ],
                "enabled_platforms": [],
            }

        platforms: dict[str, dict[str, Any]] = {}
        failed_platforms: list[dict[str, Any]] = []
        for platform, series_field in PLATFORM_SERIES_FIELDS.items():
            if platform not in enabled_platforms:
                continue
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
        return {"ok": bool(platforms), "platforms": platforms, "failed_platforms": failed_platforms, "enabled_platforms": enabled_platforms}

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
        task_id = str(payload.get("task_id") or "") or None
        run_ids = [str(run_id) for run_id in payload.get("run_ids", [])]
        timeout_seconds = max(0.0, _env_float("COLLECTOR_WAIT_TIMEOUT_SECONDS", DEFAULT_COLLECTOR_WAIT_TIMEOUT_SECONDS))
        poll_seconds = max(0.0, _env_float("COLLECTOR_WAIT_POLL_SECONDS", DEFAULT_COLLECTOR_WAIT_POLL_SECONDS))
        deadline = time.monotonic() + timeout_seconds
        self._mark_task_running(task_id)
        while True:
            await self._dispatch_pending_collection_runs(task_id, run_ids)
            await self._poll_running_collection_runs(task_id, run_ids)
            result = self._collection_run_results(run_ids)
            if not result["pending_platforms"]:
                return result
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                self._fail_pending_collection_runs(run_ids, task_id=task_id)
                return self._collection_run_results(run_ids)
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
        from worker_app.corpus import PLATFORM_HEADERS, export_platform_workbook, sync_vehicle_corpus_files

        corpus_root = os.getenv("KOUBEI_CORPUS_ROOT") or "/tmp/vehicle-koubei-corpus"
        result = sync_vehicle_corpus_files(
            database_url=self.database_url,
            corpus_root=corpus_root,
            query=str(vehicle.get("query") or ""),
            model_name=str(vehicle.get("model_name") or ""),
            autohome_series_id=str(vehicle.get("autohome_series_id") or ""),
            dongchedi_series_id=str(vehicle.get("dcd_series_id") or ""),
        )
        task_id = str(payload["task_id"])
        model_name = str(vehicle.get("model_name") or vehicle.get("query") or task_id)
        job_paths = self._task_job_paths(task_id)
        enabled_platforms = {
            str(platform)
            for platform in (vehicle.get("enabled_platforms") or ["autohome", "dongchedi"])
            if str(platform) in {"autohome", "dongchedi"}
        } or {"autohome", "dongchedi"}
        successful_platforms = {
            str(platform)
            for platform in ((payload.get("results") or {}).get("successful_platforms") or [])
            if str(platform) in {"autohome", "dongchedi"}
        }
        failed_platforms = {
            str(item.get("platform"))
            for item in ((payload.get("results") or {}).get("failed_platforms") or [])
            if isinstance(item, dict) and item.get("platform")
        }
        raw_artifacts: list[str] = []
        exposed_platforms: dict[str, dict[str, Any]] = {}
        platform_statuses: dict[str, dict[str, Any]] = {}
        platform_artifacts: dict[str, dict[str, str]] = {}
        report_platforms: list[str] = []

        for platform in ("autohome", "dongchedi"):
            if platform not in enabled_platforms:
                continue
            series_id = str(vehicle.get(PLATFORM_SERIES_FIELDS[platform]) or "")
            raw_path = (
                job_paths.outputs.raw / f"ZJ{model_name}原始口碑.xlsx"
                if platform == "autohome"
                else job_paths.outputs.raw / f"DCD口碑_{model_name}.xlsx"
            )
            row_count = export_platform_workbook(
                database_url=self.database_url,
                query=str(vehicle.get("query") or model_name),
                platform=platform,
                series_id=series_id,
                output_path=raw_path,
                headers=PLATFORM_HEADERS[platform],
            )
            status_name = "current_collected" if platform in successful_platforms else "historical_unchecked"
            if platform in successful_platforms and row_count == 0:
                status_name = "checked_empty"
            if platform not in successful_platforms and row_count == 0:
                status_name = "unavailable"
                raw_path.unlink(missing_ok=True)
            else:
                raw_artifacts.append(str(raw_path))
                if row_count > 0:
                    report_platforms.append(platform)
                if platform in result.get("platforms", {}):
                    exposed_platforms[platform] = {
                        **dict(result["platforms"][platform]),
                        "raw_path": str(raw_path),
                        "row_count": row_count,
                    }

            label = "已查新增" if status_name in {"current_collected", "checked_empty"} else "未查新增"
            if status_name == "unavailable":
                label = "无可用历史"
            status_payload = {
                "platform": platform,
                "platform_label": PLATFORM_LABELS.get(platform, platform),
                "status": status_name,
                "label": label,
                "row_count": row_count,
                "current_collection_succeeded": platform in successful_platforms,
                "current_collection_failed": platform in failed_platforms,
            }
            status_path = job_paths.outputs.raw / platform / "source_status.json"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            platform_statuses[platform] = status_payload

            validation_path = raw_path.with_suffix(".validation.json")
            progress_path = job_paths.progress / f"{COLLECTOR_STAGE_NAMES[platform]}.progress.json"
            failed_pages_path = raw_path.with_suffix(".failed-pages.json")
            artifact_payload = {"status_path": str(status_path)}
            if raw_path.is_file():
                artifact_payload["raw_path"] = str(raw_path)
            if validation_path.is_file():
                artifact_payload["validation_path"] = str(validation_path)
            if progress_path.is_file():
                artifact_payload["progress_path"] = str(progress_path)
            if failed_pages_path.is_file():
                artifact_payload["failed_pages_path"] = str(failed_pages_path)
            platform_artifacts[platform] = artifact_payload

        artifact_paths = [result["manifest_path"]]
        artifact_paths.extend(platform["raw_path"] for platform in exposed_platforms.values())
        artifact_paths.extend(raw_artifacts)
        for artifact in platform_artifacts.values():
            artifact_paths.extend(str(path) for path in artifact.values() if path)
        artifact_paths = list(dict.fromkeys(artifact_paths))
        return {
            "artifact_paths": artifact_paths,
            "corpus": {**result, "platforms": exposed_platforms},
            "task_raw_paths": raw_artifacts,
            "report_platforms": report_platforms,
            "platform_statuses": platform_statuses,
            "platform_artifacts": platform_artifacts,
        }

    @activity.defn
    async def run_postprocess(self, payload: dict[str, Any]) -> dict:
        task_id = str(payload["task_id"])
        if self.database_url:
            self._store().mark_task_stage(task_id, "postprocessing", "running")
        task = payload.get("task") or {}
        vehicle = _first_vehicle(task) or {}
        export_result = payload.get("export_result") if isinstance(payload.get("export_result"), dict) else {}
        available_platforms = {str(platform) for platform in export_result.get("report_platforms") or []}
        if not available_platforms:
            available_platforms = {str(platform) for platform in (payload.get("results") or {}).get("successful_platforms") or []}
        if not available_platforms:
            return {"artifact_paths": [], "skipped": True, "source": "no_available_corpus"}
        if self._real_single_task_pipeline_enabled() and {"autohome", "dongchedi"}.issubset(available_platforms):
            stage_command, _job_paths = self._build_stage_command(
                task_id=task_id,
                model_name=str(vehicle.get("model_name") or vehicle.get("query") or task_id),
                autohome_series_id=str(vehicle.get("autohome_series_id") or ""),
                dongchedi_series_id=str(vehicle.get("dcd_series_id") or ""),
                collection_plan=None,
                stage_name="postprocessing",
            )
            if stage_command is not None:
                result = await self._run_stage_command_async(task_id=task_id, stage_command=stage_command)
                return {"artifact_paths": list(result["artifact_paths"]), "skipped": False, "fallback": False, **result}

        output_dir = _task_output_dir(task_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        facts_path = output_dir / "analysis_facts.jsonl"
        fact = {
            "comment_id": f"{task_id}:fallback",
            "task_id": task_id,
            "model_name": str(vehicle.get("model_name") or vehicle.get("query") or task_id),
            "source": "temporal_fallback_postprocess",
            "fallback": True,
            "date": datetime.now(UTC).date().isoformat(),
            "section_facts": {
                "positive": "",
                "negative": "",
            },
            "results_summary": {
                "successful_platforms": list((payload.get("results") or {}).get("successful_platforms") or []),
                "failed_platforms": list((payload.get("results") or {}).get("failed_platforms") or []),
            },
        }
        facts_path.write_text(json.dumps(fact, ensure_ascii=False) + "\n", encoding="utf-8")
        return {
            "artifact_paths": [str(facts_path)],
            "skipped": False,
            "fallback": True,
            "source": "temporal_fallback_postprocess",
        }

    @activity.defn
    async def run_llm_report(self, payload: dict[str, Any]) -> dict:
        task_id = str(payload["task_id"])
        if self.database_url:
            self._store().mark_task_stage(task_id, "generating_hermes_outputs", "running")
        task = payload.get("task") or {}
        vehicle = _first_vehicle(task) or {}
        results = payload.get("results") or {}
        export_result = payload.get("export_result") if isinstance(payload.get("export_result"), dict) else {}
        available_platforms = {str(platform) for platform in export_result.get("report_platforms") or []}
        if not available_platforms:
            available_platforms = {str(platform) for platform in results.get("successful_platforms") or []}
        if not available_platforms:
            return {"artifact_paths": [], "skipped": True, "source": "no_available_corpus"}
        platform_statuses = {
            str(platform): dict(status)
            for platform, status in (export_result.get("platform_statuses") or {}).items()
            if isinstance(status, dict)
        }
        single_platform_stage = None
        if available_platforms == {"autohome"}:
            single_platform_stage = "collecting_autohome"
        elif available_platforms == {"dongchedi"}:
            single_platform_stage = "collecting_dcd"
        if self._real_single_task_pipeline_enabled() and available_platforms:
            stage_command, _job_paths = self._build_stage_command(
                task_id=task_id,
                model_name=str(vehicle.get("model_name") or vehicle.get("query") or task_id),
                autohome_series_id=str(vehicle.get("autohome_series_id") or ""),
                dongchedi_series_id=str(vehicle.get("dcd_series_id") or ""),
                collection_plan=None,
                stage_name="generating_hermes_outputs",
                single_platform_stage=single_platform_stage,
            )
            if stage_command is not None:
                stage_result = await self._run_stage_command_async(task_id=task_id, stage_command=stage_command)
                final_report_path = next(
                    (path for path in stage_result["artifact_paths"] if path.endswith("final_report.json")),
                    None,
                )
                report_json = {}
                if final_report_path:
                    report_json = json.loads(Path(final_report_path).read_text(encoding="utf-8"))
                    report_json = _augment_report_with_platform_status(report_json, platform_statuses)
                    Path(final_report_path).write_text(json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8")
                return {
                    "artifact_paths": list(stage_result["artifact_paths"]),
                    "skipped": False,
                    "fallback": False,
                    "report_json": report_json,
                    **stage_result,
                }

        output_dir = _task_output_dir(task_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "final_report.json"
        model_name = str(vehicle.get("model_name") or vehicle.get("query") or task_id)
        report = {
            "headline": f"{model_name} 口碑摘要",
            "task_id": task_id,
            "model_name": model_name,
            "source": "temporal_fallback_report",
            "fallback": True,
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": "基础口碑结果已生成，完整 LLM 摘要待后续增强。",
            "platform_counts": {
                "successful": len(list(results.get("successful_platforms") or [])),
                "failed": len(list(results.get("failed_platforms") or [])),
            },
        }
        report = _augment_report_with_platform_status(report, platform_statuses)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "artifact_paths": [str(report_path)],
            "skipped": False,
            "fallback": True,
            "report_json": report,
            "source": "temporal_fallback_report",
        }

    @activity.defn
    async def publish_degraded_result(self, payload: dict[str, Any]) -> dict:
        if self.database_url:
            self._store().publish_degraded_result(str(payload["task_id"]), payload)
            self._record_single_task_download_artifacts(payload)
        return {"task_id": str(payload["task_id"]), "status": "completed_degraded"}

    @activity.defn
    async def publish_full_result(self, payload: dict[str, Any]) -> dict:
        if self.database_url:
            self._store().publish_full_result(str(payload["task_id"]), payload)
            self._record_single_task_download_artifacts(payload)
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
    async def is_retry_paused(self, payload: dict[str, Any]) -> dict:
        if not self.database_url:
            return {"paused": False}
        return {"paused": self._store().is_retry_paused(str(payload["task_id"]))}

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
