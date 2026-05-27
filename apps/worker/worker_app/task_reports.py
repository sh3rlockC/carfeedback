from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from worker_app.hermes_outputs import generate_outputs
from worker_app.report_pdf import write_report_pdf
from worker_app.stages import WORDCLOUD_FONT_PATH, load_dependencies


def _safe_filename_part(value: str) -> str:
    cleaned = "".join(char if char not in {'/', '\\', ':', '*', '?', '"', '<', '>', '|'} else "_" for char in value.strip())
    return cleaned or "vehicle"


def _has_llm_credentials(env: dict[str, str]) -> bool:
    return bool((env.get("LLM_API_KEY") or env.get("DEEPSEEK_API_KEY") or "").strip())


def _artifact_paths(result: dict[str, Any], pdf_path: Path) -> list[str]:
    paths: list[str] = []
    for key in (
        "summary_path",
        "terms_path",
        "final_report_path",
        "qa_chunks_path",
        "normalized_comments_path",
        "analysis_facts_path",
        "llm_metrics_path",
    ):
        value = result.get(key)
        if value:
            paths.append(str(value))
    paths.extend(str(path) for path in result.get("image_paths") or [] if path)
    paths.append(str(pdf_path))
    deduped: list[str] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return deduped


def generate_task_report_outputs(
    *,
    task_id: str,
    model_name: str,
    autohome_raw_path: str | Path,
    dcd_raw_path: str | Path | None,
    output_root: str | Path,
    summary_script: str | Path | None = None,
    wordcloud_script: str | Path | None = None,
    allow_degraded: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    active_env = dict(env or os.environ)
    if not allow_degraded and not _has_llm_credentials(active_env):
        return {
            "artifact_paths": [],
            "skipped": True,
            "failure_category": "llm_unavailable",
            "message": "LLM credentials are missing; full report generation is paused",
        }

    output_root_path = Path(output_root).expanduser().resolve()
    safe_model = _safe_filename_part(model_name)
    summary_dir = output_root_path / "summary"
    wordcloud_dir = output_root_path / "wordcloud"
    ai_dir = output_root_path / "ai"
    report_dir = output_root_path / "report"
    progress_dir = output_root_path.parent / "progress"
    for directory in (summary_dir, wordcloud_dir, ai_dir, report_dir, progress_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if summary_script is None or wordcloud_script is None:
        dependencies = load_dependencies()
        summary_script = dependencies["koubei-keyword-summary-skill"]["entrypoint"]
        wordcloud_script = dependencies["koubei-wordcloud"]["entrypoint"]

    autohome_path = Path(autohome_raw_path)
    dcd_path = Path(dcd_raw_path) if dcd_raw_path else None
    single_platform = not autohome_path.exists() or dcd_path is None or not dcd_path.exists()
    result = generate_outputs(
        autohome_input=autohome_path,
        dcd_input=dcd_path,
        postprocess_input=None,
        summary_output=summary_dir / f"{safe_model}_双平台口碑摘要.xlsx",
        terms_output=wordcloud_dir / f"{safe_model}_词云词项清单.xlsx",
        wordcloud_output_dir=wordcloud_dir,
        final_report_output=ai_dir / "final_report.json",
        qa_chunks_output=ai_dir / "qa_chunks.json",
        model_name=model_name,
        progress_file=progress_dir / "generating_hermes_outputs.progress.json",
        summary_script=summary_script,
        wordcloud_script=wordcloud_script,
        single_platform=single_platform,
        font_path=WORDCLOUD_FONT_PATH if Path(WORDCLOUD_FONT_PATH).exists() else None,
        env=active_env,
    )

    if result.get("degraded") and not allow_degraded:
        return {
            **result,
            "artifact_paths": [],
            "skipped": True,
            "failure_category": "llm_unavailable",
            "message": str(result.get("fallback_reason") or "full report generation degraded"),
        }

    pdf_path = report_dir / f"{safe_model}_完整报告.pdf"
    write_report_pdf(
        report_json_path=result["final_report_path"],
        output_path=pdf_path,
        model_name=model_name,
        sample_summary={},
    )
    artifact_paths = _artifact_paths(result, pdf_path)
    return {
        **result,
        "pdf_path": str(pdf_path),
        "artifact_paths": artifact_paths,
        "skipped": False,
    }
