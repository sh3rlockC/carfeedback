from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from worker_app.artifacts import JobPaths
from worker_app.dependencies import discover_manifest_path, get_workspace_root, load_dependency_map

DEPENDENCY_MANIFEST = discover_manifest_path(source_path=Path(__file__))
POSTPROCESS_BRIDGE = Path(__file__).resolve().with_name("postprocess_bridge.py")
HERMES_OUTPUTS = Path(__file__).resolve().with_name("hermes_outputs.py")
WORDCLOUD_FONT_PATH = os.getenv("WORDCLOUD_FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")


@dataclass(frozen=True)
class StageDefinition:
    name: str
    dependency_name: str
    core: bool = True


@dataclass(frozen=True)
class StageCommand:
    name: str
    dependency_name: str
    command: list[str]
    cwd: Path
    core: bool = True
    fallback_command: list[str] | None = None
    fallback_commands_by_stage: dict[str, list[str]] = field(default_factory=dict)
    skip_in_single_platform: bool = False
    expected_artifacts: tuple[str, ...] = ()
    optional_artifacts: tuple[str, ...] = ()
    progress_file: str | None = None
    parse_json_stdout: bool = False


@dataclass
class StageExecutionError(Exception):
    stage: str
    error_code: str
    message: str

    def __str__(self) -> str:
        return f"{self.stage}: {self.error_code}: {self.message}"


def load_dependencies(manifest_path: Path = DEPENDENCY_MANIFEST) -> dict[str, dict[str, Any]]:
    return load_dependency_map(manifest_path)


def resolve_stage_command(stage: StageCommand, *, single_platform_stage: str | None = None) -> StageCommand | None:
    if not single_platform_stage:
        return stage
    if stage.skip_in_single_platform:
        return None
    if single_platform_stage in stage.fallback_commands_by_stage:
        return replace(stage, command=stage.fallback_commands_by_stage[single_platform_stage])
    if stage.fallback_command:
        return replace(stage, command=stage.fallback_command)
    return stage


def build_wordcloud_font_args() -> list[str]:
    if not WORDCLOUD_FONT_PATH:
        return []
    font_path = Path(WORDCLOUD_FONT_PATH).expanduser()
    if font_path.exists():
        return ["--font-path", str(font_path)]
    return []


def _collection_args(collection_plan: dict[str, Any] | None, platform: str) -> list[str]:
    if not collection_plan:
        return []
    platform_plan = collection_plan.get(platform) or {}
    known_links_file = platform_plan.get("known_links_file")
    if not known_links_file:
        return []
    args = ["--known-links-file", str(known_links_file)]
    max_scan_pages = platform_plan.get("max_scan_pages")
    stop_after_known_pages = platform_plan.get("stop_after_known_pages")
    if max_scan_pages:
        args.extend(["--max-scan-pages", str(max_scan_pages)])
    if stop_after_known_pages:
        args.extend(["--stop-after-known-pages", str(stop_after_known_pages)])
    return args


def build_stage_commands(
    *,
    job_paths: JobPaths,
    model_name: str,
    autohome_series_id: str,
    dongchedi_series_id: str,
    dependency_map: dict[str, dict[str, Any]] | None = None,
    collection_plan: dict[str, Any] | None = None,
) -> list[StageCommand]:
    dependency_map = dependency_map or load_dependencies()
    workspace_root = get_workspace_root()
    zj_output = job_paths.outputs.raw / f"ZJ{model_name}原始口碑.xlsx"
    dcd_output = job_paths.outputs.raw / f"DCD口碑_{model_name}.xlsx"
    prepared_zj_output = job_paths.outputs.postprocess / f"{model_name}_ZJ预处理口碑.xlsx"
    dual_output = job_paths.outputs.postprocess / f"{model_name}_双平台口碑汇总.xlsx"
    summary_output = job_paths.outputs.summary / f"{model_name}_双平台口碑摘要.xlsx"
    wordcloud_terms_output = job_paths.outputs.wordcloud / f"{model_name}_词云词项清单.xlsx"
    final_report_output = job_paths.outputs.ai / "final_report.json"
    qa_chunks_output = job_paths.outputs.ai / "qa_chunks.json"
    normalized_comments_output = job_paths.outputs.ai / "normalized_comments.jsonl"
    analysis_facts_output = job_paths.outputs.ai / "analysis_facts.jsonl"
    llm_metrics_output = job_paths.outputs.ai / "llm_metrics.json"
    autohome_progress = job_paths.progress / "collecting_autohome.progress.json"
    dcd_progress = job_paths.progress / "collecting_dcd.progress.json"
    checking_progress = job_paths.progress / "checking_incremental.progress.json"
    hermes_progress = job_paths.progress / "generating_hermes_outputs.progress.json"

    auto_dep = dependency_map["auto-koubei-collector"]
    dcd_dep = dependency_map["dcd-koubei-collector"]
    post_dep = dependency_map["koubei-postprocess"]
    summary_dep = dependency_map["koubei-keyword-summary-skill"]
    wordcloud_dep = dependency_map["koubei-wordcloud"]

    hermes_dual_command = [
        sys.executable,
        str(HERMES_OUTPUTS),
        "--autohome-input",
        str(zj_output),
        "--dcd-input",
        str(dcd_output),
        "--postprocess-input",
        str(dual_output),
        "--summary-output",
        str(summary_output),
        "--terms-output",
        str(wordcloud_terms_output),
        "--wordcloud-output-dir",
        str(job_paths.outputs.wordcloud),
        "--final-report-output",
        str(final_report_output),
        "--qa-chunks-output",
        str(qa_chunks_output),
        "--model-name",
        model_name,
        "--progress-file",
        str(hermes_progress),
        "--summary-script",
        summary_dep["entrypoint"],
        "--wordcloud-script",
        wordcloud_dep["entrypoint"],
        *build_wordcloud_font_args(),
    ]
    hermes_autohome_single_platform_command = [
        sys.executable,
        str(HERMES_OUTPUTS),
        "--autohome-input",
        str(zj_output),
        "--summary-output",
        str(summary_output),
        "--terms-output",
        str(wordcloud_terms_output),
        "--wordcloud-output-dir",
        str(job_paths.outputs.wordcloud),
        "--final-report-output",
        str(final_report_output),
        "--qa-chunks-output",
        str(qa_chunks_output),
        "--model-name",
        model_name,
        "--progress-file",
        str(hermes_progress),
        "--summary-script",
        summary_dep["entrypoint"],
        "--wordcloud-script",
        wordcloud_dep["entrypoint"],
        "--single-platform",
        *build_wordcloud_font_args(),
    ]
    hermes_dcd_single_platform_command = [
        sys.executable,
        str(HERMES_OUTPUTS),
        "--autohome-input",
        str(zj_output),
        "--dcd-input",
        str(dcd_output),
        "--summary-output",
        str(summary_output),
        "--terms-output",
        str(wordcloud_terms_output),
        "--wordcloud-output-dir",
        str(job_paths.outputs.wordcloud),
        "--final-report-output",
        str(final_report_output),
        "--qa-chunks-output",
        str(qa_chunks_output),
        "--model-name",
        model_name,
        "--progress-file",
        str(hermes_progress),
        "--summary-script",
        summary_dep["entrypoint"],
        "--wordcloud-script",
        wordcloud_dep["entrypoint"],
        "--single-platform",
        *build_wordcloud_font_args(),
    ]

    return [
        StageCommand(
            name="checking_incremental",
            dependency_name="incremental-corpus",
            cwd=Path.cwd(),
            command=[
                sys.executable,
                "-c",
                "pass",
            ],
            core=True,
            expected_artifacts=(str(checking_progress),),
            progress_file=str(checking_progress),
        ),
        StageCommand(
            name="collecting_autohome",
            dependency_name="auto-koubei-collector",
            cwd=Path(auto_dep["path"]),
            command=[
                sys.executable,
                auto_dep["entrypoint"],
                "--series-id",
                autohome_series_id,
                "--start-page",
                "1",
                "--auto-detect-pages",
                "--output",
                str(zj_output),
                "--workdir",
                str(workspace_root),
                "--progress-file",
                str(autohome_progress),
                *_collection_args(collection_plan, "autohome"),
            ],
            core=True,
            expected_artifacts=(
                str(zj_output),
                str(zj_output.with_suffix(".validation.json")),
                str(autohome_progress),
            ),
            progress_file=str(autohome_progress),
        ),
        StageCommand(
            name="collecting_dcd",
            dependency_name="dcd-koubei-collector",
            cwd=Path(dcd_dep["path"]),
            command=[
                sys.executable,
                dcd_dep["entrypoint"],
                "--series-id",
                dongchedi_series_id,
                "--start-page",
                "1",
                "--output",
                str(dcd_output),
                "--progress-file",
                str(dcd_progress),
                "--quiet",
                *_collection_args(collection_plan, "dongchedi"),
            ],
            core=True,
            expected_artifacts=(
                str(dcd_output),
                str(dcd_output.with_suffix(".validation.json")),
                str(dcd_progress),
            ),
            optional_artifacts=(str(dcd_output.with_suffix(".failed-pages.json")),),
            progress_file=str(dcd_progress),
        ),
        StageCommand(
            name="postprocessing",
            dependency_name="koubei-postprocess",
            cwd=POSTPROCESS_BRIDGE.parent.parent,
            command=[
                sys.executable,
                str(POSTPROCESS_BRIDGE),
                "--postprocess-script",
                post_dep["entrypoint"],
                "--source-zj-input",
                str(zj_output),
                "--prepared-zj-output",
                str(prepared_zj_output),
                "--dcd-input",
                str(dcd_output),
                "--output",
                str(dual_output),
            ],
            core=True,
            skip_in_single_platform=True,
            expected_artifacts=(str(dual_output),),
            optional_artifacts=(str(prepared_zj_output),),
        ),
        StageCommand(
            name="generating_hermes_outputs",
            dependency_name="hermes-agent",
            cwd=HERMES_OUTPUTS.parent,
            command=hermes_dual_command,
            core=True,
            fallback_command=hermes_autohome_single_platform_command,
            fallback_commands_by_stage={
                "collecting_autohome": hermes_autohome_single_platform_command,
                "collecting_dcd": hermes_dcd_single_platform_command,
            },
            expected_artifacts=(
                str(summary_output),
                str(summary_output.with_suffix(".validation.json")),
                str(hermes_progress),
                str(wordcloud_terms_output),
                str(final_report_output),
                str(qa_chunks_output),
            ),
            optional_artifacts=(
                str(job_paths.outputs.wordcloud / f"{model_name}_优点词云.png"),
                str(job_paths.outputs.wordcloud / f"{model_name}_槽点词云.png"),
                str(normalized_comments_output),
                str(analysis_facts_output),
                str(llm_metrics_output),
            ),
            progress_file=str(hermes_progress),
            parse_json_stdout=True,
        ),
    ]
