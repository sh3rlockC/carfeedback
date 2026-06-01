from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import sqlite3
import subprocess
import time
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from worker_app.artifacts import JobPaths
from worker_app.jobs import StageResult
from worker_app.progress import ProgressSink
from worker_app.runner import _classify_error, _collect_existing_artifacts, _write_stage_log, run_stage_command
from worker_app.stages import StageCommand, StageExecutionError


COLLECTOR_STAGES = {"collecting_autohome", "collecting_dcd"}
AI_OUTPUT_STAGES = {
    "generating_hermes_outputs",
    "generating_time_report_outputs",
    "generating_comparison_outputs",
}


@dataclass(frozen=True)
class OpenClawSettings:
    enabled: bool = False
    gateway_url: str = "ws://host.docker.internal:18790"
    token_file: str = "/run/secrets/openclaw_gateway_token"
    agent_id: str = "main"
    autohome_agent_id: str | None = None
    dcd_agent_id: str | None = None
    analysis_agent_ids: tuple[str, ...] = ()
    collector_skill: str = "sh3rlockC/auto-koubei-collector"
    dcd_collector_skill: str = "sh3rlockC/dcd-koubei-collector"
    keyword_summary_skill: str = "sh3rlockC/koubei-keyword-summary-skill"
    wordcloud_skill: str = "sh3rlockC/koubei-wordcloud"
    analysis_python: str = "/opt/codexwork/koubei-host-venv/bin/python"
    analysis_env_file: str = "/opt/codexwork/carFeedback/.runtime/secrets/openclaw-llm.env"
    analysis_worker_root_container: str = "/app"
    analysis_worker_root_host: str | None = None
    openclaw_workspace_host: str | None = None
    keyword_summary_script: str | None = None
    wordcloud_script: str | None = None
    analysis_agent_lease_dir: str = "/tmp/openclaw-analysis-agent-leases"
    analysis_agent_lease_ttl_seconds: int = 7200
    timeout_seconds: int = 1800
    artifact_poll_interval_seconds: float = 5.0
    stages: tuple[str, ...] = ("collecting_autohome", "collecting_dcd")
    artifact_root_container: str = "/srv/koubei/jobs"
    artifact_root_host: str | None = None
    task_db_path: str | None = None
    device_identity_file: str = "/openclaw-state/identity/device.json"

    @classmethod
    def from_env(cls) -> "OpenClawSettings":
        openclaw_workspace_host = os.getenv("OPENCLAW_WORKSPACE_HOST") or None
        return cls(
            enabled=_env_bool("OPENCLAW_ADAPTER_ENABLED") or _env_bool("OPENCLAW_AUTOHOME_ENABLED"),
            gateway_url=os.getenv("OPENCLAW_GATEWAY_URL", cls.gateway_url),
            token_file=os.getenv("OPENCLAW_GATEWAY_TOKEN_FILE", cls.token_file),
            agent_id=os.getenv("OPENCLAW_AGENT_ID", cls.agent_id),
            autohome_agent_id=os.getenv("OPENCLAW_AUTOHOME_AGENT_ID") or None,
            dcd_agent_id=os.getenv("OPENCLAW_DCD_AGENT_ID") or None,
            analysis_agent_ids=_env_list("OPENCLAW_ANALYSIS_AGENT_IDS", cls.analysis_agent_ids),
            collector_skill=os.getenv("OPENCLAW_AUTOHOME_COLLECTOR_SKILL", os.getenv("OPENCLAW_COLLECTOR_SKILL", cls.collector_skill)),
            dcd_collector_skill=os.getenv("OPENCLAW_DCD_COLLECTOR_SKILL", cls.dcd_collector_skill),
            keyword_summary_skill=os.getenv("OPENCLAW_KEYWORD_SUMMARY_SKILL", cls.keyword_summary_skill),
            wordcloud_skill=os.getenv("OPENCLAW_WORDCLOUD_SKILL", cls.wordcloud_skill),
            analysis_python=os.getenv("OPENCLAW_ANALYSIS_PYTHON", cls.analysis_python),
            analysis_env_file=os.getenv("OPENCLAW_ANALYSIS_ENV_FILE", cls.analysis_env_file),
            analysis_worker_root_container=os.getenv("OPENCLAW_ANALYSIS_WORKER_ROOT_CONTAINER", cls.analysis_worker_root_container),
            analysis_worker_root_host=os.getenv("OPENCLAW_ANALYSIS_WORKER_ROOT_HOST") or None,
            openclaw_workspace_host=openclaw_workspace_host,
            keyword_summary_script=(
                os.getenv("OPENCLAW_KEYWORD_SUMMARY_SCRIPT")
                or _default_openclaw_skill_script(openclaw_workspace_host, "koubei-keyword-summary-skill", "scripts/summarize_koubei_excel.py")
            ),
            wordcloud_script=(
                os.getenv("OPENCLAW_WORDCLOUD_SCRIPT")
                or _default_openclaw_skill_script(openclaw_workspace_host, "koubei-wordcloud", "scripts/generate_wordcloud.py")
            ),
            analysis_agent_lease_dir=os.getenv("OPENCLAW_ANALYSIS_AGENT_LEASE_DIR", cls.analysis_agent_lease_dir),
            analysis_agent_lease_ttl_seconds=int(os.getenv("OPENCLAW_ANALYSIS_AGENT_LEASE_TTL_SECONDS", str(cls.analysis_agent_lease_ttl_seconds))),
            timeout_seconds=int(os.getenv("OPENCLAW_TIMEOUT_SECONDS", os.getenv("OPENCLAW_AGENT_TIMEOUT_SECONDS", str(cls.timeout_seconds)))),
            artifact_poll_interval_seconds=float(os.getenv("OPENCLAW_ARTIFACT_POLL_INTERVAL_SECONDS", str(cls.artifact_poll_interval_seconds))),
            stages=_env_list("OPENCLAW_ADAPTER_STAGES", cls.stages),
            artifact_root_container=os.getenv("ARTIFACT_ROOT", cls.artifact_root_container),
            artifact_root_host=os.getenv("OPENCLAW_ARTIFACT_ROOT_HOST") or None,
            task_db_path=os.getenv("OPENCLAW_TASK_DB_PATH") or None,
            device_identity_file=os.getenv("OPENCLAW_DEVICE_IDENTITY_FILE", cls.device_identity_file),
        )

    def agent_id_for_stage(self, stage_name: str) -> str:
        if stage_name == "collecting_autohome":
            return self.autohome_agent_id or self.agent_id
        if stage_name == "collecting_dcd":
            return self.dcd_agent_id or self.agent_id
        if stage_name in AI_OUTPUT_STAGES and self.analysis_agent_ids:
            return self.analysis_agent_ids[0]
        return self.agent_id

    def read_token(self) -> str | None:
        token_path = Path(self.token_file)
        if not token_path.exists():
            return None
        token = token_path.read_text(encoding="utf-8").strip()
        return token or None


@dataclass(frozen=True)
class _OpenClawAgentLease:
    agent_id: str
    path: Path


class OpenClawGatewayClientProtocol(Protocol):
    def call_agent(
        self,
        message: str,
        *,
        settings: OpenClawSettings,
        session_id: str | None = None,
        stage_name: str = "openclaw",
        agent_id: str | None = None,
    ) -> dict[str, Any]: ...


StageRunnerCallable = Callable[[StageCommand, JobPaths, ProgressSink], StageResult]


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    return values or default


def _default_openclaw_skill_script(openclaw_workspace_host: str | None, skill_dir: str, script_relative_path: str) -> str | None:
    if not openclaw_workspace_host:
        return None
    return str(Path(openclaw_workspace_host) / "skills" / skill_dir / script_relative_path)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _read_device_identity(identity_file: str) -> dict[str, str] | None:
    path = Path(identity_file)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if raw.get("version") != 1:
        return None
    device_id = raw.get("deviceId")
    public_key_pem = raw.get("publicKeyPem")
    private_key_pem = raw.get("privateKeyPem")
    if not all(isinstance(value, str) and value.strip() for value in (device_id, public_key_pem, private_key_pem)):
        return None
    return {
        "device_id": device_id,
        "public_key_pem": public_key_pem,
        "private_key_pem": private_key_pem,
    }


def _sign_device_payload(private_key_pem: str, payload: str) -> str:
    key_path: Path | None = None
    payload_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as key_file:
            key_file.write(private_key_pem)
            key_path = Path(key_file.name)
        with tempfile.NamedTemporaryFile("wb", delete=False) as payload_file:
            payload_file.write(payload.encode("utf-8"))
            payload_path = Path(payload_file.name)
        signature = subprocess.check_output(
            ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key_path), "-in", str(payload_path)],
            stderr=subprocess.DEVNULL,
        )
    finally:
        if key_path is not None:
            key_path.unlink(missing_ok=True)
        if payload_path is not None:
            payload_path.unlink(missing_ok=True)
    return _base64url(signature)


def _build_device_auth(
    *,
    settings: OpenClawSettings,
    token: str,
    nonce: str,
    role: str,
    scopes: list[str],
    client_id: str,
    client_mode: str,
    client_platform: str,
    device_family: str = "",
) -> dict[str, Any] | None:
    identity = _read_device_identity(settings.device_identity_file)
    if identity is None:
        return None

    signed_at = int(time.time() * 1000)
    payload = "|".join(
        [
            "v3",
            identity["device_id"],
            client_id,
            client_mode,
            role,
            ",".join(scopes),
            str(signed_at),
            token,
            nonce,
            client_platform,
            device_family,
        ]
    )
    signature = _sign_device_payload(identity["private_key_pem"], payload)
    return {
        "id": identity["device_id"],
        "publicKey": identity["public_key_pem"],
        "signature": signature,
        "signedAt": signed_at,
        "nonce": nonce,
    }


def _command_arg(command: StageCommand, option: str) -> str:
    try:
        index = command.command.index(option)
        return command.command[index + 1]
    except (ValueError, IndexError) as exc:
        raise StageExecutionError(
            stage=command.name,
            error_code="CONFIG_ERROR",
            message=f"missing required command option: {option}",
        ) from exc


def _optional_command_arg(command: StageCommand, option: str) -> str | None:
    try:
        index = command.command.index(option)
        return command.command[index + 1]
    except (ValueError, IndexError):
        return None


def _is_full_refresh_command(command: StageCommand) -> bool:
    return _optional_command_arg(command, "--known-links-file") is None


def _map_host_path_prefix(path: str, *, source_root: str | None, target_root: str | None) -> str | None:
    if not source_root or not target_root:
        return None
    candidate_path = Path(path)
    if not candidate_path.is_absolute():
        return None
    source = Path(source_root).expanduser().resolve()
    target = Path(target_root).expanduser().resolve()
    candidate = candidate_path.expanduser().resolve()
    try:
        relative = candidate.relative_to(source)
    except ValueError:
        return None
    return str(target / relative)


def _host_path(path: str, settings: OpenClawSettings) -> str:
    for source_root, target_root in (
        (settings.artifact_root_container, settings.artifact_root_host),
        (settings.analysis_worker_root_container, settings.analysis_worker_root_host),
    ):
        mapped = _map_host_path_prefix(path, source_root=source_root, target_root=target_root)
        if mapped:
            return mapped
    return path


def _looks_like_python_executable(value: str) -> bool:
    name = Path(value).name.lower()
    return name == "python" or name.startswith("python") or name in {"python3", "python.exe"}


def _set_command_option(args: list[str], option: str, value: str | None = None) -> list[str]:
    updated = list(args)
    if option in updated:
        index = updated.index(option)
        if value is not None:
            if index + 1 < len(updated):
                updated[index + 1] = value
            else:
                updated.append(value)
        return updated
    updated.append(option)
    if value is not None:
        updated.append(value)
    return updated


def _analysis_command_for_openclaw(command: StageCommand, settings: OpenClawSettings) -> list[str]:
    args = list(command.command)
    if args and _looks_like_python_executable(args[0]):
        args[0] = settings.analysis_python
    args = [_host_path(arg, settings) for arg in args]
    if command.name in {"generating_hermes_outputs", "generating_time_report_outputs"}:
        args = _set_command_option(args, "--skill-first")
        if settings.keyword_summary_script:
            args = _set_command_option(args, "--summary-script", settings.keyword_summary_script)
        if settings.wordcloud_script:
            args = _set_command_option(args, "--wordcloud-script", settings.wordcloud_script)
    if command.name in AI_OUTPUT_STAGES:
        args = _set_command_option(args, "--source-label", "openclaw-hermes")
    return args


def _analysis_local_fallback_command(command: StageCommand) -> StageCommand:
    args = list(command.command)
    if command.name in AI_OUTPUT_STAGES:
        args = _set_command_option(args, "--source-label", "openclaw-hermes-local-fallback")
    return replace(command, command=args)


def _ensure_host_writable_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o777)
    except OSError:
        return


def _prepare_ai_output_directories_for_openclaw(command: StageCommand, job_paths: JobPaths) -> None:
    # OpenClaw runs the analysis scripts on the host as the gateway user, while
    # the worker container may have created root-owned artifact directories.
    directories = {job_paths.logs, job_paths.logs / "hermes", job_paths.progress}
    for artifact in (*command.expected_artifacts, *command.optional_artifacts):
        if artifact:
            directories.add(Path(artifact).parent)
    if command.progress_file:
        directories.add(Path(command.progress_file).parent)
    for directory in directories:
        _ensure_host_writable_directory(directory)


def _lease_file_for_agent(settings: OpenClawSettings, agent_id: str) -> Path:
    safe_agent_id = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in agent_id)
    return Path(settings.analysis_agent_lease_dir) / f"{safe_agent_id}.lock"


def _remove_stale_lease(path: Path, *, ttl_seconds: int) -> None:
    if ttl_seconds <= 0 or not path.exists():
        return
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return
    if age_seconds > ttl_seconds:
        path.unlink(missing_ok=True)


def _try_acquire_agent_lease(
    *,
    settings: OpenClawSettings,
    agent_id: str,
    stage_name: str,
    session_id: str,
) -> _OpenClawAgentLease | None:
    lease_dir = Path(settings.analysis_agent_lease_dir)
    lease_dir.mkdir(parents=True, exist_ok=True)
    lease_path = _lease_file_for_agent(settings, agent_id)
    _remove_stale_lease(lease_path, ttl_seconds=settings.analysis_agent_lease_ttl_seconds)
    payload = json.dumps(
        {
            "agent_id": agent_id,
            "stage": stage_name,
            "session_id": session_id,
            "pid": os.getpid(),
            "acquired_at": int(time.time()),
        },
        ensure_ascii=False,
    )
    try:
        descriptor = os.open(str(lease_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(payload)
    return _OpenClawAgentLease(agent_id=agent_id, path=lease_path)


def _acquire_analysis_agent_lease(
    *,
    settings: OpenClawSettings,
    stage_name: str,
    session_id: str,
) -> _OpenClawAgentLease:
    deadline = time.monotonic() + max(1.0, min(float(settings.timeout_seconds), 30.0))
    while True:
        for agent_id in settings.analysis_agent_ids:
            lease = _try_acquire_agent_lease(
                settings=settings,
                agent_id=agent_id,
                stage_name=stage_name,
                session_id=session_id,
            )
            if lease is not None:
                return lease
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StageExecutionError(
                stage=stage_name,
                error_code="OPENCLAW_ANALYSIS_AGENT_BUSY",
                message="No OpenClaw analysis agent lease is currently available.",
            )
        time.sleep(min(settings.artifact_poll_interval_seconds, remaining))


def _release_agent_lease(lease: _OpenClawAgentLease | None) -> None:
    if lease is None:
        return
    lease.path.unlink(missing_ok=True)


def _build_autohome_message(command: StageCommand, settings: OpenClawSettings) -> str:
    series_id = _command_arg(command, "--series-id")
    start_page = _optional_command_arg(command, "--start-page") or "1"
    output_path = _command_arg(command, "--output")
    progress_file = _command_arg(command, "--progress-file")
    known_links_file = _optional_command_arg(command, "--known-links-file")
    max_scan_pages = _optional_command_arg(command, "--max-scan-pages")
    stop_after_known_pages = _optional_command_arg(command, "--stop-after-known-pages")
    validation_path = str(Path(output_path).with_suffix(".validation.json"))
    is_full_refresh = _is_full_refresh_command(command)

    lines = [
            "请调用已安装或已加载的汽车之家口碑采集 skill，并严格按以下 contract 输出。",
            f"skill={settings.collector_skill}",
            f"series_id={series_id}",
            f"start_page={start_page}",
            f"output_path={_host_path(output_path, settings)}",
            f"validation_json_path={_host_path(validation_path, settings)}",
            f"progress_file={_host_path(progress_file, settings)}",
    ]
    if is_full_refresh:
        lines.extend(
            [
                "collection_mode=full_refresh",
                "page_mode=auto_detect_all_pages",
                "command_contract=运行汽车之家 skill 附带脚本时必须传 --start-page 1 --auto-detect-pages，并让脚本自动探测最后一页。",
                "禁止添加 --end-page 10、固定抓取 10 页、或在探测到更多页面时提前结束。",
            ]
        )
    else:
        lines.extend(
            [
                f"known_links_file={_host_path(known_links_file, settings)}",
                f"max_scan_pages={max_scan_pages or '10'}",
                f"stop_after_known_pages={stop_after_known_pages or '2'}",
            ]
        )
    lines.extend(
        [
            "要求：",
            "1. 不要创建子代理、不要另起新对话；在当前任务中同步执行脚本并等待完成。",
            "2. 采集汽车之家用户口碑，输出 Excel 到 output_path。",
            "3. 输出 validation_json_path，用于说明采集页数、记录数和校验结果。",
            "4. 按现有 progress JSON contract 写入 progress_file。",
            "5. 启动后必须立即创建 progress_file，初始 percent 可为 0 或 1，并写入可读 message。",
            "6. 每完成一个页面或阶段都必须刷新 progress_file，至少包含 percent 或 overall.percent。",
            "7. 只有 output_path、validation_json_path、progress_file 产物都存在后才报告完成。",
            "8. 全量模式必须采集到自动探测的最后一页；如果接口显示 pagecount/rowcount 超过本轮页数，必须返回失败原因。",
            "9. 如果失败，明确返回失败原因；不要输出密钥、token 或其它本地凭据。",
        ]
    )
    return "\n".join(lines)


def _build_dcd_message(command: StageCommand, settings: OpenClawSettings) -> str:
    series_id = _command_arg(command, "--series-id")
    start_page = _optional_command_arg(command, "--start-page") or "1"
    output_path = _command_arg(command, "--output")
    progress_file = _command_arg(command, "--progress-file")
    known_links_file = _optional_command_arg(command, "--known-links-file")
    max_scan_pages = _optional_command_arg(command, "--max-scan-pages")
    stop_after_known_pages = _optional_command_arg(command, "--stop-after-known-pages")
    validation_path = str(Path(output_path).with_suffix(".validation.json"))
    failed_pages_path = str(Path(output_path).with_suffix(".failed-pages.json"))
    is_full_refresh = _is_full_refresh_command(command)

    lines = [
            "请调用已安装或已加载的懂车帝口碑采集 skill，并严格按以下 contract 输出。",
            f"skill={settings.dcd_collector_skill}",
            f"series_id={series_id}",
            f"start_page={start_page}",
            f"output_path={_host_path(output_path, settings)}",
            f"validation_json_path={_host_path(validation_path, settings)}",
            f"failed_pages_json_path={_host_path(failed_pages_path, settings)}",
            f"progress_file={_host_path(progress_file, settings)}",
    ]
    if is_full_refresh:
        lines.extend(
            [
                "collection_mode=full_refresh",
                "page_mode=auto_detect_all_pages",
                "command_contract=运行懂车帝 skill 附带脚本时必须省略 --end-page，让脚本自动探测最后一页。",
                "validation_requirement=input.end_page_auto_detected=true；如果 page_meta 最后一页 has_more=true，不能报告完成。",
                "禁止添加 --end-page 10、固定抓取 10 页、或在 total_count/has_more 表明还有更多页面时提前结束。",
            ]
        )
    else:
        lines.extend(
            [
                f"known_links_file={_host_path(known_links_file, settings)}",
                f"max_scan_pages={max_scan_pages or '10'}",
                f"stop_after_known_pages={stop_after_known_pages or '2'}",
            ]
        )
    lines.extend(
        [
            "要求：",
            "1. 不要创建子代理、不要另起新对话；在当前任务中同步执行脚本并等待完成。",
            "2. 采集懂车帝用户口碑，输出 Excel 到 output_path。",
            "3. 输出 validation_json_path，用于说明采集页数、记录数和校验结果。",
            "4. 如存在失败分页，输出 failed_pages_json_path；无失败分页也可以写空数组。",
            "5. 按现有 progress JSON contract 写入 progress_file。",
            "6. 启动后必须立即创建 progress_file，初始 percent 可为 0 或 1，并写入可读 message。",
            "7. 每完成一个页面或阶段都必须刷新 progress_file，至少包含 percent 或 overall.percent。",
            "8. 只有 output_path、validation_json_path、progress_file 产物都存在后才报告完成。",
            "9. 全量模式必须采集到自动探测的最后一页；如果 total_count/has_more 表明还有更多页面，必须返回失败原因。",
            "10. 如果失败，明确返回失败原因；不要输出密钥、token 或其它本地凭据。",
        ]
    )
    return "\n".join(lines)


def _build_collector_message(command: StageCommand, settings: OpenClawSettings) -> str:
    if command.name == "collecting_autohome":
        return _build_autohome_message(command, settings)
    if command.name == "collecting_dcd":
        return _build_dcd_message(command, settings)
    raise StageExecutionError(
        stage=command.name,
        error_code="CONFIG_ERROR",
        message=f"OpenClaw collector adapter does not support stage: {command.name}",
    )


def _build_ai_outputs_message(command: StageCommand, settings: OpenClawSettings) -> str:
    cwd = str(command.cwd)
    host_cwd = _host_path(cwd, settings)
    command_json = json.dumps(_analysis_command_for_openclaw(command, settings), ensure_ascii=False)
    expected_artifacts = [_host_path(path, settings) for path in command.expected_artifacts]
    optional_artifacts = [_host_path(path, settings) for path in command.optional_artifacts]
    progress_file = _host_path(command.progress_file, settings) if command.progress_file else ""

    lines = [
        "请在 Hermes/OpenClaw analysis agent 内执行批处理 AI 产物阶段，并严格按以下 contract 输出。",
        f"stage={command.name}",
        f"analysis_python={settings.analysis_python}",
        f"analysis_env_file={settings.analysis_env_file}",
        f"cwd={host_cwd}",
        f"command_json={command_json}",
    ]
    if command.name != "generating_comparison_outputs":
        lines.extend(
            [
                f"keyword_summary_skill={settings.keyword_summary_skill}",
                f"wordcloud_skill={settings.wordcloud_skill}",
            ]
        )
    if progress_file:
        lines.append(f"progress_file={progress_file}")
    if expected_artifacts:
        lines.append("expected_artifacts=" + json.dumps(expected_artifacts, ensure_ascii=False))
    if optional_artifacts:
        lines.append("optional_artifacts=" + json.dumps(optional_artifacts, ensure_ascii=False))
    lines.extend(
        [
            "要求：",
            "1. 不要创建子代理、不要另起新对话；在当前 analysis agent 中同步执行并等待完成。",
            (
                "2. command_json 已包含 --skill-first 和两个 skill 脚本路径；只执行 command_json 一次，它会生成摘要、词云并继续 DeepSeek 分析。不要在 command_json 之前重复运行两个 skill 脚本。"
                if command.name != "generating_comparison_outputs"
                else "2. 多车型对比阶段只执行 comparison aggregate 分析；子任务已复用单车型摘要、词云和 facts。"
            ),
            "3. DeepSeek/API 分析必须通过 analysis_env_file 注入的受控环境变量完成；只引用 env 文件路径，不要打印或回传任何密钥值。",
            "4. source analysis_env_file 后执行 command_json 指定的项目内分析命令，保留对外文件名、artifact 类型和 JSON schema。",
            "5. 启动后必须立即创建 progress_file；每完成一个阶段都必须刷新进度。",
            "6. 只有 expected_artifacts 全部存在后才报告完成；如果失败，明确返回失败原因。",
            "7. 日志、响应、产物 contract 中不得包含 LLM_API_KEY、gateway token 或其它本地凭据。",
        ]
    )
    return "\n".join(lines)


def _build_openclaw_message(command: StageCommand, settings: OpenClawSettings) -> str:
    if command.name in COLLECTOR_STAGES:
        return _build_collector_message(command, settings)
    if command.name in AI_OUTPUT_STAGES:
        return _build_ai_outputs_message(command, settings)
    raise StageExecutionError(
        stage=command.name,
        error_code="CONFIG_ERROR",
        message=f"OpenClaw adapter does not support stage: {command.name}",
    )


def _openclaw_skill_for_stage(command: StageCommand, settings: OpenClawSettings) -> str:
    if command.name == "collecting_autohome":
        return settings.collector_skill
    if command.name == "collecting_dcd":
        return settings.dcd_collector_skill
    if command.name == "generating_comparison_outputs":
        return "deepseek-analysis"
    if command.name in AI_OUTPUT_STAGES:
        return f"{settings.keyword_summary_skill},{settings.wordcloud_skill}"
    return ""


def _read_openclaw_task_status(
    *,
    settings: OpenClawSettings,
    task_id: str | None,
    run_id: str | None,
) -> dict[str, str] | None:
    if not settings.task_db_path or not (task_id or run_id):
        return None

    task_db_path = Path(settings.task_db_path)
    if not task_db_path.exists():
        return None

    try:
        with sqlite3.connect(f"file:{task_db_path}?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT task_id, run_id, status, error
                FROM task_runs
                WHERE (? IS NOT NULL AND task_id = ?)
                   OR (? IS NOT NULL AND run_id = ?)
                ORDER BY ended_at DESC NULLS LAST, last_event_at DESC NULLS LAST
                LIMIT 1
                """,
                (task_id, task_id, run_id, run_id),
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    return {
        "task_id": str(row["task_id"] or ""),
        "run_id": str(row["run_id"] or ""),
        "status": str(row["status"] or ""),
        "error": str(row["error"] or ""),
    }


def _openclaw_task_markers(command: StageCommand, settings: OpenClawSettings) -> list[str]:
    markers: list[str] = []
    values = [*command.expected_artifacts]
    if command.progress_file:
        values.append(command.progress_file)

    for value in values:
        if not value:
            continue
        for marker in (value, _host_path(value, settings)):
            if marker and marker not in markers:
                markers.append(marker)
    return markers


def _read_related_openclaw_failure(
    *,
    settings: OpenClawSettings,
    markers: list[str],
    not_before_created_at: int | None = None,
) -> dict[str, str] | None:
    if not settings.task_db_path or not markers:
        return None

    task_db_path = Path(settings.task_db_path)
    if not task_db_path.exists():
        return None

    where_clause = " OR ".join("task LIKE ?" for _ in markers)
    params = [f"%{marker}%" for marker in markers]
    created_after_clause = ""
    if not_before_created_at is not None:
        created_after_clause = "AND created_at >= ?"
        params.append(not_before_created_at)
    try:
        with sqlite3.connect(f"file:{task_db_path}?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                f"""
                SELECT task_id, run_id, status, error
                FROM task_runs
                WHERE status IN ('failed', 'timed_out', 'cancelled', 'lost')
                  AND ({where_clause})
                  {created_after_clause}
                ORDER BY ended_at DESC NULLS LAST, last_event_at DESC NULLS LAST
                LIMIT 1
                """,
                params,
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None
    return {
        "task_id": str(row["task_id"] or ""),
        "run_id": str(row["run_id"] or ""),
        "status": str(row["status"] or ""),
        "error": str(row["error"] or ""),
    }


def _read_related_openclaw_status_counts(
    *,
    settings: OpenClawSettings,
    markers: list[str],
    not_before_created_at: int | None = None,
) -> dict[str, int] | None:
    if not settings.task_db_path or not markers:
        return None

    task_db_path = Path(settings.task_db_path)
    if not task_db_path.exists():
        return None

    where_clause = " OR ".join("task LIKE ?" for _ in markers)
    params = [f"%{marker}%" for marker in markers]
    created_after_clause = ""
    if not_before_created_at is not None:
        created_after_clause = "AND created_at >= ?"
        params.append(not_before_created_at)
    try:
        with sqlite3.connect(f"file:{task_db_path}?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM task_runs
                WHERE ({where_clause})
                  {created_after_clause}
                GROUP BY status
                """,
                params,
            ).fetchall()
    except sqlite3.Error:
        return None

    if not rows:
        return None
    return {str(row["status"] or ""): int(row["count"] or 0) for row in rows}


def _response_accepted_at(response: dict[str, Any] | None) -> int | None:
    if not response:
        return None
    for key in ("acceptedAt", "accepted_at", "createdAt", "created_at"):
        value = response.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _read_validation_payload(command: StageCommand) -> dict[str, Any] | None:
    try:
        validation_path = Path(_command_arg(command, "--output")).with_suffix(".validation.json")
    except StageExecutionError:
        return None
    if not validation_path.exists():
        return None
    try:
        payload = json.loads(validation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _validate_full_refresh_dcd_output(command: StageCommand, payload: dict[str, Any]) -> None:
    input_payload = payload.get("input")
    if isinstance(input_payload, dict) and input_payload.get("end_page_auto_detected") is False:
        raise StageExecutionError(
            stage=command.name,
            error_code="OPENCLAW_PARTIAL_COLLECTION",
            message=(
                "DCD full refresh completed with input.end_page_auto_detected=false; "
                "this usually means OpenClaw added a manual page cap such as --end-page 10."
            ),
        )

    page_meta = payload.get("page_meta")
    if not isinstance(page_meta, list):
        return
    valid_meta = [item for item in page_meta if isinstance(item, dict)]
    if not valid_meta:
        return
    last_meta = max(valid_meta, key=lambda item: int(item.get("page") or 0))
    if last_meta.get("has_more") is True:
        raise StageExecutionError(
            stage=command.name,
            error_code="OPENCLAW_PARTIAL_COLLECTION",
            message="DCD full refresh stopped while validation page_meta still reports has_more=true.",
        )


def _validate_collector_output_contract(command: StageCommand) -> None:
    if not _is_full_refresh_command(command):
        return
    payload = _read_validation_payload(command)
    if payload is None:
        return
    if command.name == "collecting_dcd":
        _validate_full_refresh_dcd_output(command, payload)


class OpenClawGatewayClient:
    def call_agent(
        self,
        message: str,
        *,
        settings: OpenClawSettings,
        session_id: str | None = None,
        stage_name: str = "openclaw",
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        token = settings.read_token()
        if token is None:
            raise StageExecutionError(
                stage=stage_name,
                error_code="CONFIG_ERROR",
                message=f"OpenClaw gateway token file not found: {settings.token_file}",
            )
        return asyncio.run(
            self._call_agent_async(
                message=message,
                settings=settings,
                token=token,
                session_id=session_id,
                stage_name=stage_name,
                agent_id=agent_id or settings.agent_id_for_stage(stage_name),
            )
        )

    async def _call_agent_async(
        self,
        *,
        message: str,
        settings: OpenClawSettings,
        token: str,
        session_id: str | None,
        stage_name: str,
        agent_id: str,
    ) -> dict[str, Any]:
        try:
            import websockets
        except ImportError as exc:
            raise StageExecutionError(
                stage=stage_name,
                error_code="CONFIG_ERROR",
                message="Python package 'websockets' is required for OpenClaw gateway adapter",
            ) from exc

        async with websockets.connect(settings.gateway_url, max_size=25 * 1024 * 1024) as websocket:
            await self._connect(websocket, settings=settings, token=token)
            return await self._request(
                websocket,
                method="agent",
                params={
                    "message": message,
                    "agentId": agent_id,
                    "sessionId": session_id,
                    "timeout": settings.timeout_seconds,
                    "idempotencyKey": str(uuid.uuid4()),
                },
                timeout_seconds=settings.timeout_seconds,
                stage_name=stage_name,
            )

    async def _connect(self, websocket, *, settings: OpenClawSettings, token: str) -> None:
        nonce = await self._wait_connect_challenge(websocket)
        role = "operator"
        scopes = ["operator.write"]
        client_id = "gateway-client"
        client_mode = "backend"
        client_platform = platform.system().lower()
        device = _build_device_auth(
            settings=settings,
            token=token,
            nonce=nonce,
            role=role,
            scopes=scopes,
            client_id=client_id,
            client_mode=client_mode,
            client_platform=client_platform,
        )
        await self._request(
            websocket,
            method="connect",
            params={
                "minProtocol": 3,
                "maxProtocol": 3,
                "client": {
                    "id": client_id,
                    "displayName": "vehicle-koubei-worker",
                    "version": "0.1.0",
                    "platform": client_platform,
                    "mode": client_mode,
                    "instanceId": str(uuid.uuid4()),
                },
                "caps": [],
                "auth": {"token": token},
                "role": role,
                "scopes": scopes,
                **({"device": device} if device else {}),
            },
            timeout_seconds=min(settings.timeout_seconds, 30),
        )

    async def _wait_connect_challenge(self, websocket) -> str:
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            frame = json.loads(raw)
            if frame.get("type") == "event" and frame.get("event") == "connect.challenge":
                nonce = (frame.get("payload") or {}).get("nonce")
                if isinstance(nonce, str) and nonce.strip():
                    return nonce.strip()
                raise StageExecutionError(
                    stage="openclaw",
                    error_code="OPENCLAW_ERROR",
                    message="OpenClaw connect challenge missing nonce",
                )

    async def _request(
        self,
        websocket,
        *,
        method: str,
        params: dict[str, Any],
        timeout_seconds: int,
        stage_name: str = "openclaw",
        expect_final: bool = False,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        await websocket.send(json.dumps({"type": "req", "id": request_id, "method": method, "params": params}, ensure_ascii=False))
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout=max(timeout_seconds, 1))
            frame = json.loads(raw)
            if frame.get("type") != "res" or frame.get("id") != request_id:
                continue
            if frame.get("ok") is True:
                payload = frame.get("payload") or {}
                if expect_final and payload.get("status") == "accepted":
                    continue
                return payload
            error = frame.get("error") or {}
            raise StageExecutionError(
                stage=stage_name,
                error_code=str(error.get("code") or "OPENCLAW_ERROR"),
                message=str(error.get("message") or "OpenClaw gateway request failed"),
            )


def run_collector_via_openclaw(
    command: StageCommand,
    job_paths: JobPaths,
    progress_sink: ProgressSink,
    *,
    settings: OpenClawSettings,
    gateway_client: OpenClawGatewayClientProtocol | None = None,
    assigned_agent_id: str | None = None,
) -> StageResult:
    if command.name not in COLLECTOR_STAGES | AI_OUTPUT_STAGES:
        return run_stage_command(command, job_paths, progress_sink)

    stdout_log = job_paths.logs / f"{command.name}.openclaw.stdout.log"
    stderr_log = job_paths.logs / f"{command.name}.openclaw.stderr.log"
    client = gateway_client or OpenClawGatewayClient()
    session_id = f"vehicle-koubei-{job_paths.root.name}-{command.name}"
    lease: _OpenClawAgentLease | None = None
    if command.name in AI_OUTPUT_STAGES and assigned_agent_id is None and settings.analysis_agent_ids:
        lease = _acquire_analysis_agent_lease(settings=settings, stage_name=command.name, session_id=session_id)
        agent_id = lease.agent_id
    else:
        agent_id = assigned_agent_id or settings.agent_id_for_stage(command.name)
    try:
        if command.name in AI_OUTPUT_STAGES:
            _prepare_ai_output_directories_for_openclaw(command, job_paths)
        response = client.call_agent(
            _build_openclaw_message(command, settings),
            settings=settings,
            session_id=session_id,
            stage_name=command.name,
            agent_id=agent_id,
        )
        _write_stage_log(stdout_log, json.dumps(response, ensure_ascii=False, indent=2))
        _write_stage_log(stderr_log, "")

        _wait_for_expected_artifacts(command, settings, response=response)
        if command.name in COLLECTOR_STAGES:
            _validate_collector_output_contract(command)
    except StageExecutionError as exc:
        if command.name in AI_OUTPUT_STAGES:
            return _run_ai_outputs_local_fallback(
                command,
                job_paths,
                progress_sink,
                settings=settings,
                agent_id=agent_id,
                exc=exc,
            )
        raise
    except Exception as exc:
        stage_exc = StageExecutionError(
            stage=command.name,
            error_code=_classify_error(command, str(exc), ""),
            message=str(exc) or "OpenClaw collection failed",
        )
        if command.name in AI_OUTPUT_STAGES:
            return _run_ai_outputs_local_fallback(
                command,
                job_paths,
                progress_sink,
                settings=settings,
                agent_id=agent_id,
                exc=stage_exc,
            )
        raise stage_exc from exc
    finally:
        _release_agent_lease(lease)

    artifact_paths, output_metadata = _collect_existing_artifacts(
        command,
        json.dumps(response, ensure_ascii=False) if command.parse_json_stdout else "",
    )
    output_metadata.update(
        {
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "openclaw_gateway_url": settings.gateway_url,
            "openclaw_agent_id": agent_id,
            "openclaw_skill": _openclaw_skill_for_stage(command, settings),
        }
    )
    if command.progress_file and Path(command.progress_file).exists():
        output_metadata["progress_file"] = command.progress_file

    return StageResult(status="success", artifact_paths=artifact_paths, output_metadata=output_metadata)


def _run_ai_outputs_local_fallback(
    command: StageCommand,
    job_paths: JobPaths,
    progress_sink: ProgressSink,
    *,
    settings: OpenClawSettings,
    agent_id: str | None,
    exc: StageExecutionError,
) -> StageResult:
    fallback_command = _analysis_local_fallback_command(command)
    result = run_stage_command(fallback_command, job_paths, progress_sink)
    output_metadata = dict(result.output_metadata)
    output_metadata.update(
        {
            "openclaw_gateway_url": settings.gateway_url,
            "openclaw_agent_id": agent_id,
            "openclaw_skill": _openclaw_skill_for_stage(command, settings),
            "openclaw_fallback": True,
            "openclaw_error_code": exc.error_code,
            "openclaw_error_message": exc.message,
        }
    )
    return StageResult(status="degraded", artifact_paths=result.artifact_paths, output_metadata=output_metadata)


def run_autohome_via_openclaw(
    command: StageCommand,
    job_paths: JobPaths,
    progress_sink: ProgressSink,
    *,
    settings: OpenClawSettings,
    gateway_client: OpenClawGatewayClientProtocol | None = None,
    assigned_agent_id: str | None = None,
) -> StageResult:
    return run_collector_via_openclaw(
        command,
        job_paths,
        progress_sink,
        settings=settings,
        gateway_client=gateway_client,
        assigned_agent_id=assigned_agent_id,
    )


def _wait_for_expected_artifacts(command: StageCommand, settings: OpenClawSettings, *, response: dict[str, Any] | None = None) -> None:
    task_id = str(response.get("taskId") or response.get("task_id") or "") if response else ""
    run_id = str(response.get("runId") or response.get("run_id") or response.get("sourceId") or "") if response else ""
    related_markers = _openclaw_task_markers(command, settings)
    related_not_before_created_at = _response_accepted_at(response)
    deadline = time.monotonic() + max(settings.timeout_seconds, 1)
    while True:
        missing = [artifact for artifact in command.expected_artifacts if not Path(artifact).exists()]
        if not missing:
            return

        task_status = _read_openclaw_task_status(settings=settings, task_id=task_id or None, run_id=run_id or None)
        if task_status and task_status["status"] in {"failed", "timed_out", "cancelled", "lost"}:
            error_detail = task_status["error"] or f"OpenClaw task ended with status={task_status['status']}"
            raise StageExecutionError(
                stage=command.name,
                error_code="OPENCLAW_TASK_FAILED",
                message=error_detail,
            )

        related_failure = _read_related_openclaw_failure(
            settings=settings,
            markers=related_markers,
            not_before_created_at=related_not_before_created_at,
        )
        if related_failure:
            error_detail = related_failure["error"] or f"Related OpenClaw task ended with status={related_failure['status']}"
            raise StageExecutionError(
                stage=command.name,
                error_code="OPENCLAW_TASK_FAILED",
                message=error_detail,
            )

        related_status_counts = _read_related_openclaw_status_counts(
            settings=settings,
            markers=related_markers,
            not_before_created_at=related_not_before_created_at,
        )
        active_statuses = {"accepted", "created", "pending", "queued", "running", "scheduled"}
        if related_status_counts and not (set(related_status_counts) & active_statuses):
            raise StageExecutionError(
                stage=command.name,
                error_code="OPENCLAW_ARTIFACTS_MISSING",
                message=(
                    "OpenClaw related task ended but expected artifacts are missing: "
                    + ", ".join(missing)
                ),
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StageExecutionError(
                stage=command.name,
                error_code="TIMEOUT",
                message=f"OpenClaw did not produce expected artifacts within {settings.timeout_seconds} seconds: {', '.join(missing)}",
            )

        time.sleep(min(settings.artifact_poll_interval_seconds, remaining))


def build_stage_runner(
    *,
    settings: OpenClawSettings | None = None,
    direct_runner: StageRunnerCallable = run_stage_command,
    assigned_agent_id: str | None = None,
    gateway_client: OpenClawGatewayClientProtocol | None = None,
) -> StageRunnerCallable:
    settings = settings or OpenClawSettings.from_env()
    enabled_stages = set(settings.stages)

    def runner(command: StageCommand, job_paths: JobPaths, progress_sink: ProgressSink) -> StageResult:
        # OpenClaw is a per-stage adapter. The worker remains the pipeline owner
        # and keeps local execution for every stage not explicitly routed here.
        if settings.enabled and command.name in enabled_stages:
            if command.name in COLLECTOR_STAGES | AI_OUTPUT_STAGES:
                runner_kwargs: dict[str, Any] = {
                    "settings": settings,
                    "assigned_agent_id": assigned_agent_id,
                }
                if gateway_client is not None:
                    runner_kwargs["gateway_client"] = gateway_client
                return run_collector_via_openclaw(
                    command,
                    job_paths,
                    progress_sink,
                    **runner_kwargs,
                )
        return direct_runner(command, job_paths, progress_sink)

    return runner
