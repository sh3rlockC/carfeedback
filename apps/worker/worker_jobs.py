from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

from worker_app.artifacts import ensure_job_dirs
from worker_app.comparison_outputs import VehicleSnapshot, generate_comparison_outputs
from worker_app.corpus import (
    DCD_HEADERS,
    AUTOHOME_HEADERS,
    INCREMENTAL_MAX_SCAN_PAGES,
    INCREMENTAL_STOP_AFTER_KNOWN_PAGES,
    export_platform_workbook,
    load_platform_state,
    read_validation_incremental_stats,
    read_workbook_rows,
    upsert_platform_rows,
    write_known_links_file,
)
from worker_app.hermes_outputs import generate_time_report_outputs
from worker_app.job_store import ComparisonVehicleInputs, DatabaseJobStore
from worker_app.jobs import JobContext, run_pipeline
from worker_app.openclaw_runner import OpenClawSettings, build_stage_runner
from worker_app.progress import ProgressSink
from worker_app.stages import StageCommand, StageExecutionError, build_stage_commands, load_dependencies


AI_OUTPUTS_CLI = Path(__file__).resolve().parent / "worker_app" / "ai_outputs_cli.py"


def _optional_existing_path(value: str | None) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return str(path) if path.exists() else None


def _error_code_from_exception(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message.split(":", 1)[0]


def _safe_filename_part(value: str) -> str:
    cleaned = "".join(char if char not in {'/', '\\', ':', '*', '?', '"', '<', '>', '|'} else "_" for char in value.strip())
    return cleaned or "vehicle"


def _copy_snapshot_artifacts(
    *,
    vehicle: ComparisonVehicleInputs,
    source_job_id: str,
    source_artifacts: dict[str, str],
    output_dir: Path,
) -> tuple[VehicleSnapshot, list[str]]:
    final_report = Path(source_artifacts["final_report.json"])
    analysis_facts = Path(source_artifacts["analysis_facts.jsonl"])
    llm_metrics_value = source_artifacts.get("llm_metrics.json")
    if not final_report.exists() or not analysis_facts.exists():
        raise RuntimeError(f"source job missing comparison JSON artifacts: {source_job_id}")

    prefix = _safe_filename_part(vehicle.model_name)
    final_target = output_dir / f"{prefix}.final_report.json"
    facts_target = output_dir / f"{prefix}.analysis_facts.jsonl"
    shutil.copy2(final_report, final_target)
    shutil.copy2(analysis_facts, facts_target)

    copied = [str(final_target), str(facts_target)]
    metrics_target = None
    if llm_metrics_value:
        metrics_source = Path(llm_metrics_value)
        if metrics_source.exists():
            metrics_target = output_dir / f"{prefix}.llm_metrics.json"
            shutil.copy2(metrics_source, metrics_target)
            copied.append(str(metrics_target))

    return (
        VehicleSnapshot(
            model_name=vehicle.model_name,
            source_job_id=source_job_id,
            final_report_path=final_target,
            analysis_facts_path=facts_target,
            llm_metrics_path=metrics_target,
        ),
        copied,
    )


def _unique_target(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _copy_vehicle_downloadable_artifacts(
    *,
    vehicle: ComparisonVehicleInputs,
    source_paths: list[str],
    output_dir: Path,
) -> list[str]:
    vehicle_dir = output_dir / _safe_filename_part(vehicle.model_name)
    vehicle_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    used_names: set[str] = set()
    for source_value in source_paths:
        source = Path(source_value)
        if not source.exists() or not source.name.lower().endswith((".xlsx", ".png")):
            continue
        target = vehicle_dir / source.name
        if target.name in used_names:
            target = _unique_target(target)
        used_names.add(target.name)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def _analysis_dependency_scripts() -> tuple[str, str]:
    dependency_map = load_dependencies()
    return (
        str(dependency_map["koubei-keyword-summary-skill"]["entrypoint"]),
        str(dependency_map["koubei-wordcloud"]["entrypoint"]),
    )


def _openclaw_settings_for_stage(stage_name: str) -> OpenClawSettings | None:
    settings = OpenClawSettings.from_env()
    if settings.enabled and stage_name in settings.stages:
        return settings
    return None


def _openclaw_only_direct_runner(command: StageCommand, _job_paths, _progress_sink):
    raise StageExecutionError(
        stage=command.name,
        error_code="OPENCLAW_NOT_ROUTED",
        message=f"OpenClaw adapter did not route configured stage: {command.name}",
    )


def _run_configured_openclaw_stage(*, stage: StageCommand, job_paths, settings: OpenClawSettings):
    progress_sink = ProgressSink(
        job_id=job_paths.root.name,
        progress_path=job_paths.progress / "progress.json",
        stages=[stage.name],
    )
    runner = build_stage_runner(settings=settings, direct_runner=_openclaw_only_direct_runner)
    return runner(stage, job_paths, progress_sink)


def _read_json_file(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _time_report_result_from_stage(
    *,
    stage: StageCommand,
    output_dir: Path,
    stage_result,
) -> dict:
    report_json = _read_json_file(output_dir / "final_report.json")
    metrics_json = _read_json_file(output_dir / "llm_metrics.json")
    source = str(report_json.get("source") or metrics_json.get("source") or "openclaw-hermes")
    return {
        "status": "completed_degraded" if stage_result.status == "degraded" else "completed",
        "degraded": stage_result.status == "degraded",
        "source": source,
        "sample_count": int(report_json.get("sample_count") or 0),
        "platform_counts": report_json.get("platform_counts") or {},
        "report_json": report_json,
        "artifact_paths": list(stage_result.artifact_paths) or [artifact for artifact in stage.expected_artifacts if Path(artifact).exists()],
        "output_metadata": dict(stage_result.output_metadata),
    }


def _build_time_report_stage_command(
    *,
    report_inputs,
    autohome_input: Path,
    dcd_input: Path,
    output_dir: Path,
    progress_file: Path,
    summary_script: str,
    wordcloud_script: str,
) -> StageCommand:
    date_label = f"{report_inputs.start_date}_{report_inputs.end_date}"
    summary_path = output_dir / f"{report_inputs.model_name}_{date_label}_时间范围口碑摘要.xlsx"
    terms_path = output_dir / f"{report_inputs.model_name}_{date_label}_词云词项清单.xlsx"
    final_report = output_dir / "final_report.json"
    qa_chunks = output_dir / "qa_chunks.json"
    normalized_comments = output_dir / "normalized_comments.jsonl"
    analysis_facts = output_dir / "analysis_facts.jsonl"
    llm_metrics = output_dir / "llm_metrics.json"
    command = [
        sys.executable,
        str(AI_OUTPUTS_CLI),
        "time-report",
        "--autohome-input",
        str(autohome_input),
        "--dcd-input",
        str(dcd_input),
        "--output-dir",
        str(output_dir),
        "--model-name",
        report_inputs.model_name,
        "--start-date",
        report_inputs.start_date,
        "--end-date",
        report_inputs.end_date,
        "--progress-file",
        str(progress_file),
        "--summary-script",
        summary_script,
        "--wordcloud-script",
        wordcloud_script,
        "--hermes-command",
        os.getenv("HERMES_COMMAND", "hermes"),
        "--skill-first",
        "--source-label",
        "openclaw-hermes",
    ]
    font_path = _optional_existing_path(os.getenv("WORDCLOUD_FONT_PATH"))
    if font_path:
        command.extend(["--font-path", font_path])
    return StageCommand(
        name="generating_time_report_outputs",
        dependency_name="hermes-agent",
        command=command,
        cwd=AI_OUTPUTS_CLI.parent,
        expected_artifacts=(
            str(final_report),
            str(normalized_comments),
            str(analysis_facts),
            str(llm_metrics),
            str(summary_path),
            str(summary_path.with_suffix(".validation.json")),
            str(terms_path),
            str(qa_chunks),
            str(progress_file),
        ),
        optional_artifacts=(
            str(output_dir / f"{report_inputs.model_name}_优点词云.png"),
            str(output_dir / f"{report_inputs.model_name}_槽点词云.png"),
        ),
        progress_file=str(progress_file),
        parse_json_stdout=True,
    )


def _write_comparison_snapshots_json(path: Path, snapshots: list[VehicleSnapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "model_name": snapshot.model_name,
                        "source_job_id": snapshot.source_job_id,
                        "final_report_path": str(snapshot.final_report_path),
                        "analysis_facts_path": str(snapshot.analysis_facts_path),
                        "llm_metrics_path": str(snapshot.llm_metrics_path) if snapshot.llm_metrics_path else None,
                    }
                    for snapshot in snapshots
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _build_comparison_stage_command(
    *,
    comparison_inputs,
    snapshots: list[VehicleSnapshot],
    comparison_dir: Path,
) -> StageCommand:
    snapshots_json = comparison_dir / "comparison_snapshots.json"
    _write_comparison_snapshots_json(snapshots_json, snapshots)
    command = [
        sys.executable,
        str(AI_OUTPUTS_CLI),
        "comparison",
        "--snapshots-json",
        str(snapshots_json),
        "--output-dir",
        str(comparison_dir),
        "--source-label",
        "openclaw-hermes",
    ]
    if comparison_inputs.start_date:
        command.extend(["--start-date", comparison_inputs.start_date])
    if comparison_inputs.end_date:
        command.extend(["--end-date", comparison_inputs.end_date])
    return StageCommand(
        name="generating_comparison_outputs",
        dependency_name="hermes-agent",
        command=command,
        cwd=AI_OUTPUTS_CLI.parent,
        expected_artifacts=(
            str(comparison_dir / "final_comparison.json"),
            str(comparison_dir / "comparison_summary.xlsx"),
            str(comparison_dir / "comparison_dimension_matrix.xlsx"),
            str(comparison_dir / "llm_metrics.json"),
        ),
        optional_artifacts=(str(snapshots_json),),
        parse_json_stdout=True,
    )


def _comparison_result_from_stage(*, stage: StageCommand, comparison_dir: Path, stage_result) -> dict:
    report_json = _read_json_file(comparison_dir / "final_comparison.json")
    return {
        "report_json": report_json,
        "artifact_paths": list(stage_result.artifact_paths) or [artifact for artifact in stage.expected_artifacts if Path(artifact).exists()],
        "degraded": stage_result.status == "degraded",
        "output_metadata": dict(stage_result.output_metadata),
    }


def _collection_summary_template(mode: str, existing_count: int) -> dict:
    return {
        "existing_count": existing_count,
        "new_count": 0,
        "total_count": existing_count,
        "pages_scanned": 0,
        "mode": mode,
        "stop_reason": None,
    }


def _write_incremental_progress(job_paths, collection_plan: dict[str, dict]) -> None:
    summary = {
        platform: {
            "existing_count": int(plan.get("existing_count") or 0),
            "new_count": int(plan.get("new_count") or 0),
            "total_count": int(plan.get("total_count") or plan.get("existing_count") or 0),
            "pages_scanned": int(plan.get("pages_scanned") or 0),
            "mode": str(plan.get("mode") or "incremental"),
            "stop_reason": plan.get("stop_reason"),
        }
        for platform, plan in collection_plan.items()
    }
    autohome = summary.get("autohome", {})
    dongchedi = summary.get("dongchedi", {})
    message = (
        f"历史语料：汽车之家 {autohome.get('existing_count', 0)} 条，"
        f"懂车帝 {dongchedi.get('existing_count', 0)} 条；准备"
        f"{'增量采集' if any(item.get('mode') == 'incremental' for item in summary.values()) else '全量采集'}"
    )
    progress_path = job_paths.progress / "checking_incremental.progress.json"
    progress_path.write_text(
        json.dumps(
            {
                "percent": 100,
                "message": message,
                "collection_summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def prepare_collection_plan(
    *,
    database_url: str,
    job_paths,
    query: str,
    autohome_series_id: str,
    dongchedi_series_id: str,
    collection_mode: str,
) -> dict[str, dict]:
    platform_series = {
        "autohome": str(autohome_series_id),
        "dongchedi": str(dongchedi_series_id),
    }
    plan: dict[str, dict] = {}
    for platform, series_id in platform_series.items():
        state = load_platform_state(database_url, query=query, platform=platform, series_id=series_id)
        mode = "incremental" if collection_mode == "incremental" and state.existing_count > 0 else "full_refresh"
        platform_plan = _collection_summary_template(mode, state.existing_count)
        platform_plan["series_id"] = series_id
        if mode == "incremental":
            known_links_file = job_paths.inputs / f"{platform}.known-links.txt"
            write_known_links_file(known_links_file, state.known_links)
            platform_plan.update(
                {
                    "known_links_file": str(known_links_file),
                    "known_links_count": len(state.known_links),
                    "max_scan_pages": INCREMENTAL_MAX_SCAN_PAGES,
                    "stop_after_known_pages": INCREMENTAL_STOP_AFTER_KNOWN_PAGES,
                }
            )
        plan[platform] = platform_plan

    _write_incremental_progress(job_paths, plan)
    return plan


def _collector_output_for_stage(job_paths, model_name: str, stage_name: str) -> tuple[str, Path, list[str]]:
    if stage_name == "collecting_autohome":
        return "autohome", job_paths.outputs.raw / f"ZJ{model_name}原始口碑.xlsx", AUTOHOME_HEADERS
    if stage_name == "collecting_dcd":
        return "dongchedi", job_paths.outputs.raw / f"DCD口碑_{model_name}.xlsx", DCD_HEADERS
    raise ValueError(f"unsupported collector stage: {stage_name}")


def _apply_collector_to_corpus(
    *,
    database_url: str,
    job_id: str,
    query: str,
    model_name: str,
    job_paths,
    stage_name: str,
    series_id: str,
    collection_plan: dict[str, dict],
) -> dict:
    platform, output_path, headers = _collector_output_for_stage(job_paths, model_name, stage_name)
    rows = read_workbook_rows(output_path)
    result = upsert_platform_rows(
        database_url=database_url,
        query=query,
        model_name=model_name,
        platform=platform,
        series_id=series_id,
        job_id=job_id,
        rows=rows,
    )
    exported_count = export_platform_workbook(
        database_url=database_url,
        query=query,
        platform=platform,
        series_id=series_id,
        output_path=output_path,
        headers=headers,
    )
    stats = read_validation_incremental_stats(output_path.with_suffix(".validation.json"))
    updated = dict(collection_plan.get(platform) or {})
    updated.update(
        {
            "new_count": result.inserted_count,
            "total_count": exported_count,
            "pages_scanned": int(stats.get("pages_scanned") or updated.get("pages_scanned") or 0),
            "stop_reason": stats.get("stop_reason") or updated.get("stop_reason"),
        }
    )
    collection_plan[platform] = updated
    return updated


def _summary_payload(collection_plan: dict[str, dict]) -> dict[str, dict]:
    payload: dict[str, dict] = {}
    for platform in ("autohome", "dongchedi"):
        plan = collection_plan.get(platform) or {}
        payload[platform] = {
            "existing_count": int(plan.get("existing_count") or 0),
            "new_count": int(plan.get("new_count") or 0),
            "total_count": int(plan.get("total_count") or 0),
            "pages_scanned": int(plan.get("pages_scanned") or 0),
            "mode": str(plan.get("mode") or "incremental"),
            "stop_reason": plan.get("stop_reason"),
        }
    return payload


def run_job(
    *,
    job_id: str,
    database_url: str,
    artifact_root: str,
) -> dict:
    store = DatabaseJobStore(database_url)
    job_inputs = store.fetch_job_inputs(job_id)
    context = JobContext(
        job_id=job_id,
        model_name=job_inputs.model_name,
        artifact_root=artifact_root,
    )

    try:
        job_paths = ensure_job_dirs(artifact_root, job_id)
        collection_plan = prepare_collection_plan(
            database_url=database_url,
            job_paths=job_paths,
            query=job_inputs.query,
            autohome_series_id=job_inputs.autohome_series_id,
            dongchedi_series_id=job_inputs.dongchedi_series_id,
            collection_mode=job_inputs.collection_mode,
        )
        if hasattr(store, "update_collection_summary"):
            store.update_collection_summary(job_id, _summary_payload(collection_plan))
        stage_commands = build_stage_commands(
            job_paths=job_paths,
            model_name=job_inputs.model_name,
            autohome_series_id=job_inputs.autohome_series_id,
            dongchedi_series_id=job_inputs.dongchedi_series_id,
            collection_plan=collection_plan,
        )
        series_by_stage = {
            "collecting_autohome": job_inputs.autohome_series_id,
            "collecting_dcd": job_inputs.dongchedi_series_id,
        }

        def handle_event(event: dict) -> None:
            if event.get("type") == "stage_success" and event.get("stage") in series_by_stage:
                _apply_collector_to_corpus(
                    database_url=database_url,
                    job_id=job_id,
                    query=job_inputs.query,
                    model_name=job_inputs.model_name,
                    job_paths=job_paths,
                    stage_name=str(event["stage"]),
                    series_id=series_by_stage[str(event["stage"])],
                    collection_plan=collection_plan,
                )
                if hasattr(store, "update_collection_summary"):
                    store.update_collection_summary(job_id, _summary_payload(collection_plan))
            store.handle_pipeline_event(job_id, event)

        pipeline_result = run_pipeline(
            context,
            stage_commands,
            build_stage_runner(),
            observer=handle_event,
        )
    except Exception as exc:
        error_message = str(exc) or exc.__class__.__name__
        error_code = _error_code_from_exception(exc)
        store.mark_job_failed(job_id, error_code=error_code, error_message=error_message)
        return {
            "job_id": job_id,
            "status": "failed",
            "degraded": False,
            "completed_stages": [],
            "failed_stage": None,
            "error_code": error_code,
            "error_message": error_message,
        }
    return {
        "job_id": job_id,
        "status": pipeline_result.status,
        "degraded": pipeline_result.degraded,
        "completed_stages": pipeline_result.completed_stages,
        "failed_stage": pipeline_result.failed_stage,
        "error_code": pipeline_result.error_code,
        "error_message": pipeline_result.error_message,
    }


def run_time_report(
    *,
    report_id: str,
    database_url: str,
    artifact_root: str,
) -> dict:
    store = DatabaseJobStore(database_url)
    report_inputs = store.fetch_time_report_inputs(report_id)
    store.mark_time_report_running(report_id)

    job_paths = ensure_job_dirs(artifact_root, report_inputs.job_id)
    autohome_input = job_paths.outputs.raw / f"ZJ{report_inputs.model_name}原始口碑.xlsx"
    dcd_input = job_paths.outputs.raw / f"DCD口碑_{report_inputs.model_name}.xlsx"
    output_dir = job_paths.root / "outputs" / "time_reports" / report_inputs.report_id
    progress_file = job_paths.progress / f"{report_inputs.report_id}.progress.json"
    summary_script, wordcloud_script = _analysis_dependency_scripts()

    try:
        openclaw_settings = _openclaw_settings_for_stage("generating_time_report_outputs")
        if openclaw_settings:
            stage = _build_time_report_stage_command(
                report_inputs=report_inputs,
                autohome_input=autohome_input,
                dcd_input=dcd_input,
                output_dir=output_dir,
                progress_file=progress_file,
                summary_script=summary_script,
                wordcloud_script=wordcloud_script,
            )
            try:
                stage_result = _run_configured_openclaw_stage(
                    stage=stage,
                    job_paths=job_paths,
                    settings=openclaw_settings,
                )
            except StageExecutionError:
                result = generate_time_report_outputs(
                    autohome_input=autohome_input,
                    dcd_input=dcd_input,
                    output_dir=output_dir,
                    model_name=report_inputs.model_name,
                    start_date=report_inputs.start_date,
                    end_date=report_inputs.end_date,
                    summary_script=summary_script,
                    wordcloud_script=wordcloud_script,
                    hermes_command=os.getenv("HERMES_COMMAND", "hermes"),
                    font_path=_optional_existing_path(os.getenv("WORDCLOUD_FONT_PATH")),
                    env=dict(os.environ),
                    progress_file=progress_file,
                    source_label="openclaw-hermes-local-fallback",
                )
                result["degraded"] = True
            else:
                result = _time_report_result_from_stage(stage=stage, output_dir=output_dir, stage_result=stage_result)
        else:
            result = generate_time_report_outputs(
                autohome_input=autohome_input,
                dcd_input=dcd_input,
                output_dir=output_dir,
                model_name=report_inputs.model_name,
                start_date=report_inputs.start_date,
                end_date=report_inputs.end_date,
                summary_script=summary_script,
                wordcloud_script=wordcloud_script,
                hermes_command=os.getenv("HERMES_COMMAND", "hermes"),
                font_path=_optional_existing_path(os.getenv("WORDCLOUD_FONT_PATH")),
                env=dict(os.environ),
                progress_file=progress_file,
            )
    except Exception as exc:
        error_message = str(exc) or exc.__class__.__name__
        error_code = _error_code_from_exception(exc)
        store.mark_time_report_failed(report_id, error_code=error_code, error_message=error_message)
        return {
            "report_id": report_id,
            "status": "failed",
            "error_code": error_code,
            "error_message": error_message,
        }

    store.mark_time_report_completed(report_id, result)
    return {
        "report_id": report_id,
        "status": result.get("status", "completed"),
        "sample_count": result.get("sample_count", 0),
        "source": result.get("source", "hermes"),
    }


def run_comparison_job(
    *,
    comparison_id: str,
    database_url: str,
    artifact_root: str,
) -> dict:
    store = DatabaseJobStore(database_url)
    comparison_inputs = store.fetch_comparison_inputs(comparison_id)
    comparison_dir = Path(artifact_root).expanduser().resolve() / comparison_id / "comparisons"
    comparison_dir.mkdir(parents=True, exist_ok=True)
    store.mark_comparison_running(comparison_id)

    snapshots: list[VehicleSnapshot] = []
    artifact_paths: list[str] = []
    excluded: list[dict[str, str]] = []

    for vehicle in comparison_inputs.vehicles:
        try:
            source_job_id = vehicle.source_job_id
            if source_job_id:
                store.mark_comparison_vehicle_status(vehicle.id, status="reused", source_job_id=source_job_id)
            else:
                child_job_id = store.ensure_comparison_child_job(vehicle, passphrase_version=comparison_inputs.passphrase_version)
                store.mark_comparison_vehicle_status(vehicle.id, status="running", child_job_id=child_job_id)
                child_result = run_job(job_id=child_job_id, database_url=database_url, artifact_root=artifact_root)
                if child_result.get("status") not in {"completed", "completed_degraded"}:
                    message = str(child_result.get("error_message") or "vehicle collection failed")
                    store.mark_comparison_vehicle_status(
                        vehicle.id,
                        status="excluded",
                        child_job_id=child_job_id,
                        error_code=str(child_result.get("error_code") or "collection_failed"),
                        error_message=message,
                    )
                    excluded.append({"model_name": vehicle.model_name, "reason": message})
                    continue
                source_job_id = child_job_id
                store.mark_comparison_vehicle_status(vehicle.id, status="completed", child_job_id=child_job_id)

            source_artifacts = store.comparison_source_artifacts(source_job_id)
            if "final_report.json" not in source_artifacts or "analysis_facts.jsonl" not in source_artifacts:
                raise RuntimeError(f"source job missing comparison JSON artifacts: {source_job_id}")
            snapshot, copied_paths = _copy_snapshot_artifacts(
                vehicle=vehicle,
                source_job_id=source_job_id,
                source_artifacts=source_artifacts,
                output_dir=comparison_dir,
            )
            snapshots.append(snapshot)
            artifact_paths.extend(copied_paths)
            artifact_paths.extend(
                _copy_vehicle_downloadable_artifacts(
                    vehicle=vehicle,
                    source_paths=store.comparison_downloadable_artifacts(source_job_id),
                    output_dir=comparison_dir,
                )
            )
        except Exception as exc:
            error_message = str(exc) or exc.__class__.__name__
            store.mark_comparison_vehicle_status(
                vehicle.id,
                status="excluded",
                error_code=_error_code_from_exception(exc),
                error_message=error_message,
            )
            excluded.append({"model_name": vehicle.model_name, "reason": error_message})

    if len(snapshots) < 2:
        message = "竞品对比至少需要 2 个可用车型结果"
        store.mark_comparison_failed(comparison_id, error_code="insufficient_available_vehicles", error_message=message)
        return {
            "comparison_id": comparison_id,
            "status": "failed",
            "error_code": "insufficient_available_vehicles",
            "error_message": message,
            "available_vehicle_count": len(snapshots),
            "excluded": excluded,
        }

    store.mark_comparison_comparing(comparison_id)
    openclaw_settings = _openclaw_settings_for_stage("generating_comparison_outputs")
    if openclaw_settings:
        stage = _build_comparison_stage_command(
            comparison_inputs=comparison_inputs,
            snapshots=snapshots,
            comparison_dir=comparison_dir,
        )
        try:
            stage_result = _run_configured_openclaw_stage(
                stage=stage,
                job_paths=ensure_job_dirs(artifact_root, comparison_id),
                settings=openclaw_settings,
            )
        except StageExecutionError:
            result = generate_comparison_outputs(
                snapshots=snapshots,
                output_dir=comparison_dir,
                start_date=comparison_inputs.start_date,
                end_date=comparison_inputs.end_date,
                env=dict(os.environ),
                source_label="openclaw-hermes-local-fallback",
            )
            result["degraded"] = True
        else:
            result = _comparison_result_from_stage(stage=stage, comparison_dir=comparison_dir, stage_result=stage_result)
    else:
        result = generate_comparison_outputs(
            snapshots=snapshots,
            output_dir=comparison_dir,
            start_date=comparison_inputs.start_date,
            end_date=comparison_inputs.end_date,
            env=dict(os.environ),
        )
    artifact_paths.extend(result["artifact_paths"])
    degraded = bool(excluded) or bool(result.get("degraded"))
    report_json = dict(result["report_json"])
    if excluded:
        report_json["excluded_vehicles"] = excluded
    store.mark_comparison_completed(
        comparison_id,
        report_json=report_json,
        artifact_paths=artifact_paths,
        degraded=degraded,
    )
    return {
        "comparison_id": comparison_id,
        "status": "completed_degraded" if degraded else "completed",
        "vehicle_count": len(snapshots),
        "excluded": excluded,
    }
