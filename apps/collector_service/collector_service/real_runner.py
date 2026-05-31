from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from collector_service.models import CollectorRunRequest


COLLECTOR_STAGE_NAMES = {
    "autohome": "collecting_autohome",
    "dongchedi": "collecting_dcd",
}


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for real collector execution")
    return database_url


def _artifact_root() -> str:
    return os.getenv("ARTIFACT_ROOT", "/srv/koubei/jobs")


def _stage_series_ids(request: CollectorRunRequest) -> tuple[str, str]:
    if request.platform == "autohome":
        return request.series_id, ""
    return "", request.series_id


def run_real_collector(request: CollectorRunRequest) -> dict[str, Any]:
    from worker_app.artifacts import ensure_job_dirs
    from worker_app.openclaw_runner import build_stage_runner
    from worker_app.progress import ProgressSink
    from worker_app.stages import build_stage_commands, resolve_stage_command
    from worker_jobs import _collector_output_for_stage, prepare_collection_plan

    service_platform = os.getenv("COLLECTOR_PLATFORM", "").strip()
    if service_platform and service_platform != request.platform:
        raise RuntimeError(f"collector service platform mismatch: {service_platform} cannot run {request.platform}")

    database_url = _database_url()
    job_paths = ensure_job_dirs(_artifact_root(), request.task_id)
    autohome_series_id, dcd_series_id = _stage_series_ids(request)
    collection_plan = prepare_collection_plan(
        database_url=database_url,
        job_paths=job_paths,
        query=request.query_key,
        autohome_series_id=autohome_series_id,
        dongchedi_series_id=dcd_series_id,
        collection_mode=request.mode,
    )
    stage_name = COLLECTOR_STAGE_NAMES[request.platform]
    commands = build_stage_commands(
        job_paths=job_paths,
        model_name=request.model_name,
        autohome_series_id=autohome_series_id,
        dongchedi_series_id=dcd_series_id,
        collection_plan=collection_plan,
    )
    command = next(command for command in commands if command.name == stage_name)
    stage_command = resolve_stage_command(command, single_platform_stage=stage_name)
    if stage_command is None:
        raise RuntimeError(f"collector stage unavailable: {stage_name}")

    progress_sink = ProgressSink(
        job_id=request.task_id,
        progress_path=Path(stage_command.progress_file) if stage_command.progress_file else job_paths.progress / "progress.json",
        stages=[stage_name],
    )
    stage_result = build_stage_runner(assigned_agent_id=request.agent_id)(stage_command, job_paths, progress_sink)
    _platform, output_path, _headers = _collector_output_for_stage(job_paths, request.model_name, stage_name)
    return {
        "output_path": str(output_path),
        "artifact_paths": list(stage_result.artifact_paths),
        "resume_cursor": dict(stage_result.output_metadata.get("resume_cursor") or {}),
    }
