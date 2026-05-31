from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ComparisonArtifact, ComparisonJob, ComparisonVehicle, Job, JobArtifact, JobStageRun, Task
from app.schemas import (
    ArtifactItem,
    ComparisonArtifactItem,
    ComparisonExcludedVehicleResponse,
    ComparisonProgressResponse,
    ComparisonResultResponse,
    ComparisonVehicleProgress,
)
from app.services.confirmed_vehicle_series import query_key
from app.services.eta import EtaEstimate, estimate_full_job_seconds, estimate_job_progress_eta
from app.services.stage_progress import read_stage_progress

REUSABLE_JOB_STATUSES = {"completed", "completed_degraded"}
COMPARISON_TERMINAL_STATUSES = {"completed", "completed_degraded", "failed", "cancelled", "expired"}
REQUIRED_REUSE_SUFFIXES = ("final_report.json", "analysis_facts.jsonl")
OPTION_LOOKBACK_LIMIT = 100
COMPARISON_SUMMARY_SECONDS = 600
AVAILABLE_VEHICLE_STATUSES = {"completed", "reused"}
EXCLUDED_VEHICLE_STATUSES = {"excluded", "failed"}
PLATFORM_LABELS = {
    "autohome": "autohome",
    "dongchedi": "dongchedi",
}


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _finished_or_created(job: Job) -> datetime | None:
    return _as_aware_utc(job.finished_at) or _as_aware_utc(job.created_at)


def _artifact_by_suffix(db: Session, job_id: str, suffix: str) -> JobArtifact | None:
    artifacts = (
        db.query(JobArtifact)
        .filter(JobArtifact.job_id == job_id)
        .order_by(JobArtifact.id.asc())
        .all()
    )
    for artifact in artifacts:
        path = Path(artifact.artifact_path)
        if artifact.artifact_path.endswith(suffix) and path.exists():
            return artifact
    return None


def reusable_job_artifacts(db: Session, job_id: str) -> dict[str, JobArtifact] | None:
    required = {suffix: _artifact_by_suffix(db, job_id, suffix) for suffix in REQUIRED_REUSE_SUFFIXES}
    if any(artifact is None for artifact in required.values()):
        return None
    artifacts: dict[str, JobArtifact] = {suffix: artifact for suffix, artifact in required.items() if artifact is not None}
    metrics = _artifact_by_suffix(db, job_id, "llm_metrics.json")
    if metrics is not None:
        artifacts["llm_metrics.json"] = metrics
    return artifacts


def is_reusable_job(db: Session, settings: Settings, job_id: str) -> bool:
    job = db.get(Job, job_id)
    if job is None or job.status not in REUSABLE_JOB_STATUSES:
        return False
    finished = _finished_or_created(job)
    if finished is None:
        return False
    if finished < datetime.now(UTC) - timedelta(days=settings.job_artifact_retention_days):
        return False
    return reusable_job_artifacts(db, job_id) is not None


def find_reusable_jobs(db: Session, settings: Settings, query: str, limit: int = 5) -> list[dict[str, Any]]:
    key = query_key(query)
    cutoff = datetime.now(UTC) - timedelta(days=settings.job_artifact_retention_days)
    candidates = (
        db.query(Job)
        .filter(Job.status.in_(tuple(REUSABLE_JOB_STATUSES)))
        .order_by(Job.finished_at.desc().nullslast(), Job.created_at.desc())
        .limit(OPTION_LOOKBACK_LIMIT)
        .all()
    )
    options: list[dict[str, Any]] = []
    for job in candidates:
        if key not in {query_key(job.query), query_key(job.model_name)}:
            continue
        finished = _finished_or_created(job)
        if finished is None or finished < cutoff:
            continue
        if reusable_job_artifacts(db, job.job_id) is None:
            continue
        options.append(
            {
                "job_id": job.job_id,
                "model_name": job.model_name,
                "finished_at": finished,
                "source": "recent_result",
            }
        )
        if len(options) >= limit:
            break
    return options


def empty_vehicle_resolve(query: str) -> dict[str, Any]:
    return {
        "query": query,
        "autohome": {"best": None, "candidates": []},
        "dongchedi": {"best": None, "candidates": []},
    }


def comparison_artifact_item(artifact: ComparisonArtifact, comparison_id: str) -> ComparisonArtifactItem:
    return ComparisonArtifactItem(
        id=artifact.id,
        type=artifact.artifact_type,
        path=artifact.artifact_path,
        url=artifact.artifact_url or f"/api/comparisons/{comparison_id}/artifacts/{artifact.id}",
        source_stage=artifact.source_stage,
    )


def artifact_item(artifact: JobArtifact, job_id: str) -> ArtifactItem:
    return ArtifactItem(
        id=artifact.id,
        type=artifact.artifact_type,
        path=artifact.artifact_path,
        url=artifact.artifact_url or f"/api/jobs/{job_id}/artifacts/{artifact.id}",
        source_stage=artifact.source_stage,
    )


def _missing_platforms_from_candidates(candidates: dict[str, Any] | None) -> list[str]:
    selected = candidates if isinstance(candidates, dict) else {}
    missing: list[str] = []
    for platform in PLATFORM_LABELS:
        candidate = selected.get(platform)
        series_id = candidate.get("series_id") if isinstance(candidate, dict) else None
        if not str(series_id or "").strip():
            missing.append(platform)
    return missing


def _excluded_from_vehicle(vehicle: ComparisonVehicle) -> ComparisonExcludedVehicleResponse:
    return ComparisonExcludedVehicleResponse(
        vehicle_id=vehicle.id,
        position=vehicle.position,
        query=vehicle.query,
        model_name=vehicle.model_name,
        status=vehicle.status or "excluded",
        source_job_id=vehicle.source_job_id,
        child_task_id=vehicle.child_job_id,
        error_code=vehicle.error_code,
        error_message=vehicle.error_message,
        missing_platforms=_missing_platforms_from_candidates(vehicle.selected_candidates),
    )


def _excluded_from_report_item(index: int, item: Any) -> ComparisonExcludedVehicleResponse | None:
    if not isinstance(item, dict):
        return None
    model_name = str(item.get("model_name") or item.get("query") or f"车型 {index}")
    reason = item.get("reason") or item.get("error_message")
    missing_platforms = item.get("missing_platforms") if isinstance(item.get("missing_platforms"), list) else []
    return ComparisonExcludedVehicleResponse(
        position=index,
        query=str(item.get("query") or model_name),
        model_name=model_name,
        status=str(item.get("status") or "excluded"),
        source_job_id=str(item["source_job_id"]) if item.get("source_job_id") else None,
        child_task_id=str(item["child_task_id"]) if item.get("child_task_id") else None,
        error_code=str(item["error_code"]) if item.get("error_code") else None,
        error_message=str(reason) if reason else None,
        missing_platforms=[str(platform) for platform in missing_platforms],
    )


def _excluded_vehicle_payloads(comparison: ComparisonJob) -> list[ComparisonExcludedVehicleResponse]:
    vehicles = sorted(comparison.vehicles, key=lambda item: item.position)
    excluded = [
        _excluded_from_vehicle(vehicle)
        for vehicle in vehicles
        if vehicle.status in EXCLUDED_VEHICLE_STATUSES or vehicle.error_code or vehicle.error_message
    ]
    if excluded:
        return excluded

    report_excluded = []
    report_json = comparison.report_json if isinstance(comparison.report_json, dict) else {}
    for index, item in enumerate(report_json.get("excluded_vehicles") or [], start=1):
        parsed = _excluded_from_report_item(index, item)
        if parsed is not None:
            report_excluded.append(parsed)
    return report_excluded


def _available_vehicle_count(comparison: ComparisonJob) -> int:
    return sum(1 for vehicle in comparison.vehicles if vehicle.status in AVAILABLE_VEHICLE_STATUSES)


def _requested_vehicle_count(comparison: ComparisonJob) -> int:
    return max(int(comparison.vehicle_count or 0), len(comparison.vehicles))


def _stage_items_with_live_progress(settings: Settings, job: Job, stage_runs: list[JobStageRun]) -> list[SimpleNamespace]:
    items: list[SimpleNamespace] = []
    for stage in stage_runs:
        progress_percent, _message = read_stage_progress(settings, job.job_id, stage.stage_name, stage.status)
        items.append(
            SimpleNamespace(
                name=stage.stage_name,
                status=stage.status,
                progress_percent=progress_percent,
            )
        )
    return items


def _vehicle_eta(db: Session, settings: Settings, vehicle: ComparisonVehicle) -> EtaEstimate:
    if vehicle.status in {"reused", "completed"}:
        return EtaEstimate(0, 0, "预计剩余 0 分钟", "done")
    if vehicle.status in {"failed", "excluded"}:
        return EtaEstimate(0, 0, "预计剩余 0 分钟", "done")
    if vehicle.child_job_id:
        job = db.get(Job, vehicle.child_job_id)
        if job is not None:
            stage_runs = (
                db.query(JobStageRun)
                .filter(JobStageRun.job_id == job.job_id)
                .order_by(JobStageRun.id.asc())
                .all()
            )
            return estimate_job_progress_eta(db, job, _stage_items_with_live_progress(settings, job, stage_runs))
        task = db.get(Task, vehicle.child_job_id)
        if task is not None:
            return _task_eta(db, task)
    return estimate_full_job_seconds(db)


def _task_eta(db: Session, task: Task) -> EtaEstimate:
    if task.status in COMPARISON_TERMINAL_STATUSES or task.current_stage in COMPARISON_TERMINAL_STATUSES:
        return EtaEstimate(0, 0, "预计剩余 0 分钟", "done")
    if task.eta_seconds is not None:
        seconds = max(0, int(task.eta_seconds))
        minutes = ceil(seconds / 60)
        return EtaEstimate(
            seconds,
            minutes,
            f"预计剩余 {minutes} 分钟",
            "history" if task.eta_reason else "fallback",
        )
    return estimate_full_job_seconds(db)


def comparison_progress_payload(db: Session, settings: Settings, comparison: ComparisonJob) -> ComparisonProgressResponse:
    vehicles = sorted(comparison.vehicles, key=lambda item: item.position)
    vehicle_payloads: list[ComparisonVehicleProgress] = []
    vehicle_path_seconds = 0
    confidence = "fallback"
    completed_count = 0

    for vehicle in vehicles:
        eta = _vehicle_eta(db, settings, vehicle)
        if eta.estimated_remaining_seconds is not None:
            vehicle_path_seconds = max(vehicle_path_seconds, eta.estimated_remaining_seconds)
        if eta.eta_confidence == "history":
            confidence = "history"
        if vehicle.status in {"reused", "completed"}:
            completed_count += 1
        vehicle_payloads.append(
            ComparisonVehicleProgress(
                position=vehicle.position,
                query=vehicle.query,
                model_name=vehicle.model_name,
                status=vehicle.status,
                source_job_id=vehicle.source_job_id,
                child_job_id=vehicle.child_job_id,
                error_code=vehicle.error_code,
                error_message=vehicle.error_message,
                missing_platforms=_missing_platforms_from_candidates(vehicle.selected_candidates),
                **eta.as_dict(),
            )
        )

    if comparison.status in COMPARISON_TERMINAL_STATUSES or comparison.current_stage in COMPARISON_TERMINAL_STATUSES:
        total_seconds = 0
        confidence = "done"
    elif comparison.current_stage != "comparing":
        total_seconds = vehicle_path_seconds + COMPARISON_SUMMARY_SECONDS
    else:
        total_seconds = COMPARISON_SUMMARY_SECONDS

    minutes = ceil(total_seconds / 60)
    status_to_percent = {
        "queued": 5,
        "collecting_models": 20 + int((completed_count / max(len(vehicles), 1)) * 55),
        "comparing": 88,
        "completed": 100,
        "completed_degraded": 100,
        "failed": 100,
        "expired": 100,
    }
    overall_percent = status_to_percent.get(comparison.current_stage, status_to_percent.get(comparison.status, 0))
    message = {
        "queued": "竞品对比任务已创建，等待执行",
        "collecting_models": "正在补齐车型采集结果",
        "comparing": "正在生成竞品对比",
        "completed": "竞品对比已完成",
        "completed_degraded": "竞品对比已完成，部分车型已排除",
        "failed": "竞品对比失败",
        "expired": "竞品对比结果已过期，请重新创建任务",
    }.get(comparison.current_stage, f"当前阶段：{comparison.current_stage}")

    return ComparisonProgressResponse(
        comparison_id=comparison.comparison_id,
        status=comparison.status,
        current_stage=comparison.current_stage,
        degraded=comparison.degraded,
        requested_vehicle_count=_requested_vehicle_count(comparison),
        available_vehicle_count=_available_vehicle_count(comparison),
        excluded_vehicle_count=len(_excluded_vehicle_payloads(comparison)),
        overall_percent=overall_percent,
        estimated_remaining_seconds=total_seconds,
        estimated_remaining_minutes=minutes,
        eta_label=f"预计剩余 {minutes} 分钟",
        eta_confidence=confidence,
        vehicles=vehicle_payloads,
        excluded_vehicles=_excluded_vehicle_payloads(comparison),
        message=message,
    )


def comparison_result_payload(settings: Settings, comparison: ComparisonJob) -> ComparisonResultResponse:
    artifacts = sorted(comparison.artifacts, key=lambda item: item.id)
    excluded = _excluded_vehicle_payloads(comparison)
    report_json = dict(comparison.report_json or {})
    if excluded and not report_json.get("excluded_vehicles"):
        report_json["excluded_vehicles"] = [item.model_dump() for item in excluded]
    return ComparisonResultResponse(
        comparison_id=comparison.comparison_id,
        status=comparison.status,
        degraded=comparison.degraded,
        retention_days=settings.job_artifact_retention_days,
        requested_vehicle_count=_requested_vehicle_count(comparison),
        available_vehicle_count=_available_vehicle_count(comparison),
        excluded_vehicle_count=len(excluded),
        vehicle_count=comparison.vehicle_count,
        excluded_vehicles=excluded,
        report_json=report_json,
        artifacts=[comparison_artifact_item(artifact, comparison.comparison_id) for artifact in artifacts],
        zip_url=f"/api/comparisons/{comparison.comparison_id}/artifacts.zip",
    )
