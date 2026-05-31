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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from worker_app.artifacts import JobPaths
from worker_app.jobs import StageResult
from worker_app.progress import ProgressSink
from worker_app.runner import _classify_error, _collect_existing_artifacts, _write_stage_log, run_stage_command
from worker_app.stages import StageCommand, StageExecutionError


@dataclass(frozen=True)
class OpenClawSettings:
    enabled: bool = False
    gateway_url: str = "ws://host.docker.internal:18790"
    token_file: str = "/run/secrets/openclaw_gateway_token"
    agent_id: str = "main"
    autohome_agent_id: str | None = None
    dcd_agent_id: str | None = None
    collector_skill: str = "sh3rlockC/auto-koubei-collector"
    dcd_collector_skill: str = "sh3rlockC/dcd-koubei-collector"
    timeout_seconds: int = 1800
    artifact_poll_interval_seconds: float = 5.0
    stages: tuple[str, ...] = ("collecting_autohome", "collecting_dcd")
    artifact_root_container: str = "/srv/koubei/jobs"
    artifact_root_host: str | None = None
    task_db_path: str | None = None
    device_identity_file: str = "/openclaw-state/identity/device.json"

    @classmethod
    def from_env(cls) -> "OpenClawSettings":
        return cls(
            enabled=_env_bool("OPENCLAW_ADAPTER_ENABLED") or _env_bool("OPENCLAW_AUTOHOME_ENABLED"),
            gateway_url=os.getenv("OPENCLAW_GATEWAY_URL", cls.gateway_url),
            token_file=os.getenv("OPENCLAW_GATEWAY_TOKEN_FILE", cls.token_file),
            agent_id=os.getenv("OPENCLAW_AGENT_ID", cls.agent_id),
            autohome_agent_id=os.getenv("OPENCLAW_AUTOHOME_AGENT_ID") or None,
            dcd_agent_id=os.getenv("OPENCLAW_DCD_AGENT_ID") or None,
            collector_skill=os.getenv("OPENCLAW_AUTOHOME_COLLECTOR_SKILL", os.getenv("OPENCLAW_COLLECTOR_SKILL", cls.collector_skill)),
            dcd_collector_skill=os.getenv("OPENCLAW_DCD_COLLECTOR_SKILL", cls.dcd_collector_skill),
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
        return self.agent_id

    def read_token(self) -> str | None:
        token_path = Path(self.token_file)
        if not token_path.exists():
            return None
        token = token_path.read_text(encoding="utf-8").strip()
        return token or None


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


def _host_path(path: str, settings: OpenClawSettings) -> str:
    if not settings.artifact_root_host:
        return path

    source_root = Path(settings.artifact_root_container).resolve()
    target_root = Path(settings.artifact_root_host).expanduser().resolve()
    candidate = Path(path).resolve()
    try:
        relative = candidate.relative_to(source_root)
    except ValueError:
        return path
    return str(target_root / relative)


def _build_autohome_message(command: StageCommand, settings: OpenClawSettings) -> str:
    series_id = _command_arg(command, "--series-id")
    output_path = _command_arg(command, "--output")
    progress_file = _command_arg(command, "--progress-file")
    known_links_file = _optional_command_arg(command, "--known-links-file")
    max_scan_pages = _optional_command_arg(command, "--max-scan-pages")
    stop_after_known_pages = _optional_command_arg(command, "--stop-after-known-pages")
    validation_path = str(Path(output_path).with_suffix(".validation.json"))

    lines = [
            "请调用已安装或已加载的汽车之家口碑采集 skill，并严格按以下 contract 输出。",
            f"skill={settings.collector_skill}",
            f"series_id={series_id}",
            f"output_path={_host_path(output_path, settings)}",
            f"validation_json_path={_host_path(validation_path, settings)}",
            f"progress_file={_host_path(progress_file, settings)}",
    ]
    if known_links_file:
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
            "8. 如果失败，明确返回失败原因；不要输出密钥、token 或其它本地凭据。",
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
    if known_links_file:
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
            "9. 如果失败，明确返回失败原因；不要输出密钥、token 或其它本地凭据。",
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


def _collector_skill_for_stage(command: StageCommand, settings: OpenClawSettings) -> str:
    if command.name == "collecting_autohome":
        return settings.collector_skill
    if command.name == "collecting_dcd":
        return settings.dcd_collector_skill
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
) -> dict[str, str] | None:
    if not settings.task_db_path or not markers:
        return None

    task_db_path = Path(settings.task_db_path)
    if not task_db_path.exists():
        return None

    where_clause = " OR ".join("task LIKE ?" for _ in markers)
    params = [f"%{marker}%" for marker in markers]
    try:
        with sqlite3.connect(f"file:{task_db_path}?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                f"""
                SELECT task_id, run_id, status, error
                FROM task_runs
                WHERE status IN ('failed', 'timed_out', 'cancelled', 'lost')
                  AND ({where_clause})
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
) -> dict[str, int] | None:
    if not settings.task_db_path or not markers:
        return None

    task_db_path = Path(settings.task_db_path)
    if not task_db_path.exists():
        return None

    where_clause = " OR ".join("task LIKE ?" for _ in markers)
    params = [f"%{marker}%" for marker in markers]
    try:
        with sqlite3.connect(f"file:{task_db_path}?mode=ro", uri=True, timeout=1) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM task_runs
                WHERE {where_clause}
                GROUP BY status
                """,
                params,
            ).fetchall()
    except sqlite3.Error:
        return None

    if not rows:
        return None
    return {str(row["status"] or ""): int(row["count"] or 0) for row in rows}


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
    if command.name not in {"collecting_autohome", "collecting_dcd"}:
        return run_stage_command(command, job_paths, progress_sink)

    stdout_log = job_paths.logs / f"{command.name}.openclaw.stdout.log"
    stderr_log = job_paths.logs / f"{command.name}.openclaw.stderr.log"
    client = gateway_client or OpenClawGatewayClient()
    session_id = f"vehicle-koubei-{job_paths.root.name}-{command.name}"
    agent_id = assigned_agent_id or settings.agent_id_for_stage(command.name)
    try:
        response = client.call_agent(
            _build_collector_message(command, settings),
            settings=settings,
            session_id=session_id,
            stage_name=command.name,
            agent_id=agent_id,
        )
    except StageExecutionError:
        raise
    except Exception as exc:
        raise StageExecutionError(
            stage=command.name,
            error_code=_classify_error(command, str(exc), ""),
            message=str(exc) or "OpenClaw collection failed",
        ) from exc

    _write_stage_log(stdout_log, json.dumps(response, ensure_ascii=False, indent=2))
    _write_stage_log(stderr_log, "")

    _wait_for_expected_artifacts(command, settings, response=response)

    artifact_paths, output_metadata = _collect_existing_artifacts(command, "")
    output_metadata.update(
        {
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "openclaw_gateway_url": settings.gateway_url,
            "openclaw_agent_id": agent_id,
            "openclaw_skill": _collector_skill_for_stage(command, settings),
        }
    )
    if command.progress_file and Path(command.progress_file).exists():
        output_metadata["progress_file"] = command.progress_file

    return StageResult(status="success", artifact_paths=artifact_paths, output_metadata=output_metadata)


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

        related_failure = _read_related_openclaw_failure(settings=settings, markers=related_markers)
        if related_failure:
            error_detail = related_failure["error"] or f"Related OpenClaw task ended with status={related_failure['status']}"
            raise StageExecutionError(
                stage=command.name,
                error_code="OPENCLAW_TASK_FAILED",
                message=error_detail,
            )

        related_status_counts = _read_related_openclaw_status_counts(settings=settings, markers=related_markers)
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
) -> StageRunnerCallable:
    settings = settings or OpenClawSettings.from_env()
    enabled_stages = set(settings.stages)

    def runner(command: StageCommand, job_paths: JobPaths, progress_sink: ProgressSink) -> StageResult:
        # OpenClaw is a per-stage adapter. The worker remains the pipeline owner
        # and keeps local execution for every stage not explicitly routed here.
        if settings.enabled and command.name in enabled_stages:
            if command.name in {"collecting_autohome", "collecting_dcd"}:
                return run_collector_via_openclaw(
                    command,
                    job_paths,
                    progress_sink,
                    settings=settings,
                    assigned_agent_id=assigned_agent_id,
                )
        return direct_runner(command, job_paths, progress_sink)

    return runner
