from __future__ import annotations

import asyncio
import json
import platform
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker_app.openclaw_runner as openclaw_runner
from worker_app.artifacts import ensure_job_dirs
from worker_app.jobs import StageResult
from worker_app.openclaw_runner import OpenClawSettings, build_stage_runner, run_autohome_via_openclaw, run_collector_via_openclaw
from worker_app.progress import ProgressSink
from worker_app.stages import StageCommand, StageExecutionError


def make_autohome_stage(tmp_path: Path) -> StageCommand:
    output = tmp_path / "jobs" / "job_openclaw" / "outputs" / "raw" / "ZJ测试车原始口碑.xlsx"
    progress = tmp_path / "jobs" / "job_openclaw" / "progress" / "collecting_autohome.progress.json"
    return StageCommand(
        name="collecting_autohome",
        dependency_name="auto-koubei-collector",
        command=[
            "python",
            "export_autohome_koubei.py",
            "--series-id",
            "8089",
            "--output",
            str(output),
            "--progress-file",
            str(progress),
        ],
        cwd=tmp_path,
        expected_artifacts=(str(output), str(output.with_suffix(".validation.json")), str(progress)),
        progress_file=str(progress),
    )


def make_dcd_stage(tmp_path: Path) -> StageCommand:
    output = tmp_path / "jobs" / "job_openclaw" / "outputs" / "raw" / "DCD口碑_测试车.xlsx"
    progress = tmp_path / "jobs" / "job_openclaw" / "progress" / "collecting_dcd.progress.json"
    return StageCommand(
        name="collecting_dcd",
        dependency_name="dcd-koubei-collector",
        command=[
            "python",
            "export_dcd_koubei.py",
            "--series-id",
            "25545",
            "--start-page",
            "1",
            "--output",
            str(output),
            "--progress-file",
            str(progress),
        ],
        cwd=tmp_path,
        expected_artifacts=(str(output), str(output.with_suffix(".validation.json")), str(progress)),
        optional_artifacts=(str(output.with_suffix(".failed-pages.json")),),
        progress_file=str(progress),
    )


def test_build_stage_runner_uses_direct_runner_when_openclaw_is_disabled(tmp_path: Path) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])

    calls: list[str] = []

    def direct_runner(command, _job_paths, _progress_sink):
        calls.append(command.name)
        return StageResult(status="success", artifact_paths=["direct"])

    runner = build_stage_runner(settings=OpenClawSettings(enabled=False), direct_runner=direct_runner)
    result = runner(stage, job_paths, progress_sink)

    assert calls == ["collecting_autohome"]
    assert result.artifact_paths == ["direct"]


def test_build_stage_runner_routes_only_configured_stages_when_openclaw_is_enabled(tmp_path: Path) -> None:
    dcd_stage = StageCommand(
        name="collecting_dcd",
        dependency_name="dcd-koubei-collector",
        command=["python", "dcd.py"],
        cwd=tmp_path,
    )
    settings = OpenClawSettings(enabled=True, stages=("collecting_autohome",))
    calls: list[str] = []

    def direct_runner(command, _job_paths, _progress_sink):
        calls.append(command.name)
        return StageResult(status="success", artifact_paths=["direct"])

    runner = build_stage_runner(settings=settings, direct_runner=direct_runner)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[dcd_stage.name])

    result = runner(dcd_stage, job_paths, progress_sink)

    assert calls == ["collecting_dcd"]
    assert result.artifact_paths == ["direct"]


def test_openclaw_settings_reads_stage_specific_agent_ids_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCLAW_AGENT_ID", "main")
    monkeypatch.setenv("OPENCLAW_AUTOHOME_AGENT_ID", "autohome")
    monkeypatch.setenv("OPENCLAW_DCD_AGENT_ID", "dongchedi")

    settings = OpenClawSettings.from_env()

    assert settings.agent_id == "main"
    assert settings.agent_id_for_stage("collecting_autohome") == "autohome"
    assert settings.agent_id_for_stage("collecting_dcd") == "dongchedi"
    assert settings.agent_id_for_stage("summarizing") == "main"


def test_openclaw_settings_routes_both_collectors_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENCLAW_ADAPTER_STAGES", raising=False)

    settings = OpenClawSettings.from_env()

    assert settings.stages == ("collecting_autohome", "collecting_dcd")


def test_openclaw_gateway_connect_sends_device_identity_when_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_path = tmp_path / "identity" / "device.json"
    identity_path.parent.mkdir(parents=True)
    identity_path.write_text(
        json.dumps(
            {
                "version": 1,
                "deviceId": "device-1",
                "publicKeyPem": "PUBLIC KEY",
                "privateKeyPem": "PRIVATE KEY",
            }
        ),
        encoding="utf-8",
    )
    signed_payloads: list[str] = []

    def fake_sign(_private_key_pem: str, payload: str) -> str:
        signed_payloads.append(payload)
        return "signed-payload"

    monkeypatch.setattr(openclaw_runner, "_sign_device_payload", fake_sign, raising=False)

    class FakeWebSocket:
        def __init__(self) -> None:
            self.sent: list[dict] = []
            self.responses = [
                json.dumps({"type": "event", "event": "connect.challenge", "payload": {"nonce": "nonce-1"}}),
            ]

        async def recv(self) -> str:
            return self.responses.pop(0)

        async def send(self, raw: str) -> None:
            frame = json.loads(raw)
            self.sent.append(frame)
            self.responses.append(json.dumps({"type": "res", "id": frame["id"], "ok": True, "payload": {}}))

    websocket = FakeWebSocket()
    settings = OpenClawSettings()
    object.__setattr__(settings, "device_identity_file", str(identity_path))

    asyncio.run(openclaw_runner.OpenClawGatewayClient()._connect(websocket, settings=settings, token="gateway-token"))

    connect_params = websocket.sent[0]["params"]
    assert connect_params["auth"] == {"token": "gateway-token"}
    assert connect_params["scopes"] == ["operator.write"]
    assert connect_params["device"]["id"] == "device-1"
    assert connect_params["device"]["publicKey"] == "PUBLIC KEY"
    assert connect_params["device"]["signature"] == "signed-payload"
    assert connect_params["device"]["nonce"] == "nonce-1"
    assert isinstance(connect_params["device"]["signedAt"], int)
    assert signed_payloads == [
        f"v3|device-1|gateway-client|backend|operator|operator.write|{connect_params['device']['signedAt']}|gateway-token|nonce-1|{platform.system().lower()}|"
    ]


def test_run_collectors_route_to_stage_specific_openclaw_agents(tmp_path: Path) -> None:
    container_root = tmp_path / "jobs"
    job_paths = ensure_job_dirs(container_root, "job_openclaw")
    autohome_stage = make_autohome_stage(tmp_path)
    dcd_stage = make_dcd_stage(tmp_path)
    progress_sink = ProgressSink(
        job_id="job_openclaw",
        progress_path=job_paths.progress / "progress.json",
        stages=[autohome_stage.name, dcd_stage.name],
    )
    captured_agent_ids: dict[str, str | None] = {}

    class CapturingGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            captured_agent_ids[stage_name] = agent_id
            stage = autohome_stage if stage_name == "collecting_autohome" else dcd_stage
            for artifact in stage.expected_artifacts:
                Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                Path(artifact).write_text("artifact", encoding="utf-8")
            for artifact in stage.optional_artifacts:
                Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                Path(artifact).write_text("[]", encoding="utf-8")
            return {"status": "completed"}

    settings = OpenClawSettings(
        enabled=True,
        agent_id="main",
        autohome_agent_id="autohome",
        dcd_agent_id="dongchedi",
        stages=("collecting_autohome", "collecting_dcd"),
        artifact_root_container=str(container_root),
    )
    gateway_client = CapturingGatewayClient()

    autohome_result = run_collector_via_openclaw(
        autohome_stage,
        job_paths,
        progress_sink,
        settings=settings,
        gateway_client=gateway_client,
    )
    dcd_result = run_collector_via_openclaw(
        dcd_stage,
        job_paths,
        progress_sink,
        settings=settings,
        gateway_client=gateway_client,
    )

    assert captured_agent_ids == {
        "collecting_autohome": "autohome",
        "collecting_dcd": "dongchedi",
    }
    assert autohome_result.output_metadata["openclaw_agent_id"] == "autohome"
    assert dcd_result.output_metadata["openclaw_agent_id"] == "dongchedi"


def test_run_collector_via_openclaw_prefers_assigned_agent_id(tmp_path: Path) -> None:
    container_root = tmp_path / "jobs"
    job_paths = ensure_job_dirs(container_root, "job_openclaw")
    stage = make_autohome_stage(tmp_path)
    progress_sink = ProgressSink(
        job_id="job_openclaw",
        progress_path=job_paths.progress / "progress.json",
        stages=[stage.name],
    )
    captured_agent_ids: list[str | None] = []

    class CapturingGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            captured_agent_ids.append(agent_id)
            for artifact in stage.expected_artifacts:
                Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                Path(artifact).write_text("artifact", encoding="utf-8")
            return {"status": "completed"}

    result = run_collector_via_openclaw(
        stage,
        job_paths,
        progress_sink,
        settings=OpenClawSettings(
            enabled=True,
            agent_id="main",
            autohome_agent_id="autohome",
            stages=("collecting_autohome",),
            artifact_root_container=str(container_root),
        ),
        gateway_client=CapturingGatewayClient(),
        assigned_agent_id="autohome-4",
    )

    assert captured_agent_ids == ["autohome-4"]
    assert result.output_metadata["openclaw_agent_id"] == "autohome-4"


def test_build_stage_runner_passes_assigned_agent_id_to_collector_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])
    captured: dict[str, str | None] = {}

    def fake_run_collector_via_openclaw(
        command,
        job_paths,
        progress_sink,
        *,
        settings,
        assigned_agent_id=None,
    ):
        captured["agent_id"] = assigned_agent_id
        return StageResult(status="success", artifact_paths=[])

    monkeypatch.setattr(openclaw_runner, "run_collector_via_openclaw", fake_run_collector_via_openclaw)

    runner = build_stage_runner(
        settings=OpenClawSettings(enabled=True, stages=("collecting_autohome",)),
        assigned_agent_id="autohome-2",
    )
    result = runner(stage, job_paths, progress_sink)

    assert result.status == "success"
    assert captured["agent_id"] == "autohome-2"


def test_run_autohome_via_openclaw_calls_gateway_agent_and_collects_artifacts(tmp_path: Path) -> None:
    container_root = tmp_path / "jobs"
    host_root = tmp_path / "host-jobs"
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(container_root, "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])
    captured: dict[str, object] = {}

    class FakeGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            captured["message"] = message
            captured["settings"] = settings
            captured["session_id"] = session_id
            captured["stage_name"] = stage_name
            captured["method"] = "agent"
            captured["params"] = {"message": message, "agentId": agent_id, "timeout": settings.timeout_seconds}
            assert settings.gateway_url == "ws://host.docker.internal:18790"
            for artifact in stage.expected_artifacts:
                Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                Path(artifact).write_text("artifact", encoding="utf-8")
            return {"status": "completed"}

    result = run_autohome_via_openclaw(
        stage,
        job_paths,
        progress_sink,
        settings=OpenClawSettings(
            enabled=True,
            gateway_url="ws://host.docker.internal:18790",
            agent_id="main",
            collector_skill="sh3rlockC/auto-koubei-collector",
            timeout_seconds=1800,
            stages=("collecting_autohome",),
            artifact_root_container=str(container_root),
            artifact_root_host=str(host_root),
        ),
        gateway_client=FakeGatewayClient(),
    )

    assert captured["method"] == "agent"
    assert captured["stage_name"] == "collecting_autohome"
    assert captured["params"]["agentId"] == "main"
    assert captured["params"]["timeout"] == 1800
    message = captured["message"]
    assert "sh3rlockC/auto-koubei-collector" in message
    assert "不要创建子代理" in message
    assert "启动后必须立即创建 progress_file" in message
    assert "每完成一个页面或阶段都必须刷新 progress_file" in message
    assert "产物都存在后才报告完成" in message
    assert "series_id=8089" in message
    assert str(host_root / "job_openclaw" / "outputs" / "raw" / "ZJ测试车原始口碑.xlsx") in message
    assert str(host_root / "job_openclaw" / "progress" / "collecting_autohome.progress.json") in message
    assert set(result.artifact_paths) == set(stage.expected_artifacts + stage.optional_artifacts)
    assert result.output_metadata["openclaw_gateway_url"] == "ws://host.docker.internal:18790"


def test_run_collector_via_openclaw_supports_dcd_collection_contract(tmp_path: Path) -> None:
    container_root = tmp_path / "jobs"
    host_root = tmp_path / "host-jobs"
    stage = make_dcd_stage(tmp_path)
    job_paths = ensure_job_dirs(container_root, "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])
    captured: dict[str, object] = {}

    class FakeGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            captured["message"] = message
            captured["stage_name"] = stage_name
            captured["session_id"] = session_id
            for artifact in stage.expected_artifacts:
                Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                Path(artifact).write_text("artifact", encoding="utf-8")
            Path(stage.optional_artifacts[0]).write_text("[]", encoding="utf-8")
            return {"status": "completed"}

    result = run_collector_via_openclaw(
        stage,
        job_paths,
        progress_sink,
        settings=OpenClawSettings(
            enabled=True,
            dcd_collector_skill="sh3rlockC/dcd-koubei-collector",
            timeout_seconds=1800,
            stages=("collecting_autohome", "collecting_dcd"),
            artifact_root_container=str(container_root),
            artifact_root_host=str(host_root),
        ),
        gateway_client=FakeGatewayClient(),
    )

    message = captured["message"]
    assert captured["stage_name"] == "collecting_dcd"
    assert captured["session_id"] == "vehicle-koubei-job_openclaw-collecting_dcd"
    assert "sh3rlockC/dcd-koubei-collector" in message
    assert "不要创建子代理" in message
    assert "启动后必须立即创建 progress_file" in message
    assert "每完成一个页面或阶段都必须刷新 progress_file" in message
    assert "产物都存在后才报告完成" in message
    assert "series_id=25545" in message
    assert str(host_root / "job_openclaw" / "outputs" / "raw" / "DCD口碑_测试车.xlsx") in message
    assert str(host_root / "job_openclaw" / "outputs" / "raw" / "DCD口碑_测试车.failed-pages.json") in message
    assert set(result.artifact_paths) == set(stage.expected_artifacts + stage.optional_artifacts)
    assert result.output_metadata["openclaw_skill"] == "sh3rlockC/dcd-koubei-collector"


def test_run_autohome_via_openclaw_uses_job_scoped_session_id(tmp_path: Path) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])
    captured: dict[str, str | None] = {}

    class SessionGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            captured["session_id"] = session_id
            for artifact in stage.expected_artifacts:
                Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                Path(artifact).write_text("artifact", encoding="utf-8")
            return {"status": "completed"}

    run_autohome_via_openclaw(
        stage,
        job_paths,
        progress_sink,
        settings=OpenClawSettings(enabled=True),
        gateway_client=SessionGatewayClient(),
    )

    assert captured["session_id"] == "vehicle-koubei-job_openclaw-collecting_autohome"


def test_run_autohome_via_openclaw_waits_for_artifacts_after_agent_acceptance(tmp_path: Path) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])

    class AsyncGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            def write_artifacts() -> None:
                time.sleep(0.05)
                for artifact in stage.expected_artifacts:
                    Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                    Path(artifact).write_text("artifact", encoding="utf-8")

            threading.Thread(target=write_artifacts, daemon=True).start()
            return {"status": "accepted", "taskId": "task-1"}

    result = run_autohome_via_openclaw(
        stage,
        job_paths,
        progress_sink,
        settings=OpenClawSettings(enabled=True, timeout_seconds=2, artifact_poll_interval_seconds=0.01),
        gateway_client=AsyncGatewayClient(),
    )

    assert set(result.artifact_paths) == set(stage.expected_artifacts)


def test_run_autohome_via_openclaw_waits_for_artifacts_after_agent_completion(tmp_path: Path) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])

    class CompletedGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            def write_artifacts() -> None:
                time.sleep(0.05)
                for artifact in stage.expected_artifacts:
                    Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                    Path(artifact).write_text("artifact", encoding="utf-8")

            threading.Thread(target=write_artifacts, daemon=True).start()
            return {"status": "completed", "taskId": "task-1"}

    result = run_autohome_via_openclaw(
        stage,
        job_paths,
        progress_sink,
        settings=OpenClawSettings(enabled=True, timeout_seconds=2, artifact_poll_interval_seconds=0.01),
        gateway_client=CompletedGatewayClient(),
    )

    assert set(result.artifact_paths) == set(stage.expected_artifacts)


def test_run_autohome_via_openclaw_fails_fast_when_accepted_task_fails(tmp_path: Path) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])
    task_db = tmp_path / "runs.sqlite"
    with sqlite3.connect(task_db) as db:
        db.execute(
            """
            CREATE TABLE task_runs (
                task_id TEXT PRIMARY KEY,
                run_id TEXT,
                status TEXT NOT NULL,
                error TEXT,
                ended_at INTEGER,
                last_event_at INTEGER
            )
            """
        )
        db.execute(
            "INSERT INTO task_runs (task_id, run_id, status, error, ended_at, last_event_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("task-1", "run-1", "failed", "LLM request failed: provider rejected the request schema or tool payload.", 1000, 1000),
        )

    class FailedAcceptedGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            return {"status": "accepted", "taskId": "task-1", "runId": "run-1"}

    with pytest.raises(StageExecutionError) as exc_info:
        run_autohome_via_openclaw(
            stage,
            job_paths,
            progress_sink,
            settings=OpenClawSettings(
                enabled=True,
                timeout_seconds=2,
                artifact_poll_interval_seconds=0.01,
                task_db_path=str(task_db),
            ),
            gateway_client=FailedAcceptedGatewayClient(),
        )

    assert exc_info.value.error_code == "OPENCLAW_TASK_FAILED"
    assert "provider rejected the request schema" in exc_info.value.message


def test_run_autohome_via_openclaw_fails_when_related_child_task_fails(tmp_path: Path) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])
    task_db = tmp_path / "runs.sqlite"
    with sqlite3.connect(task_db) as db:
        db.execute(
            """
            CREATE TABLE task_runs (
                task_id TEXT PRIMARY KEY,
                run_id TEXT,
                status TEXT NOT NULL,
                error TEXT,
                task TEXT,
                created_at INTEGER,
                ended_at INTEGER,
                last_event_at INTEGER
            )
            """
        )
        db.execute(
            "INSERT INTO task_runs (task_id, run_id, status, error, task, created_at, ended_at, last_event_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("parent-task", "parent-run", "succeeded", "", "parent accepted", 1000, 1100, 1100),
        )
        db.execute(
            "INSERT INTO task_runs (task_id, run_id, status, error, task, created_at, ended_at, last_event_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "child-task",
                "child-run",
                "failed",
                "child command failed",
                f"执行命令：python3 skills/auto-koubei-collector/scripts/export_autohome_koubei.py --output {stage.expected_artifacts[0]}",
                1200,
                1300,
                1300,
            ),
        )

    class AcceptedGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            return {"status": "accepted", "taskId": "parent-task", "runId": "parent-run"}

    with pytest.raises(StageExecutionError) as exc_info:
        run_autohome_via_openclaw(
            stage,
            job_paths,
            progress_sink,
            settings=OpenClawSettings(
                enabled=True,
                timeout_seconds=1,
                artifact_poll_interval_seconds=0.01,
                task_db_path=str(task_db),
            ),
            gateway_client=AcceptedGatewayClient(),
        )

    assert exc_info.value.error_code == "OPENCLAW_TASK_FAILED"
    assert "child command failed" in exc_info.value.message


def test_run_autohome_via_openclaw_ignores_stale_related_child_failure(tmp_path: Path) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])
    task_db = tmp_path / "runs.sqlite"
    with sqlite3.connect(task_db) as db:
        db.execute(
            """
            CREATE TABLE task_runs (
                task_id TEXT PRIMARY KEY,
                run_id TEXT,
                status TEXT NOT NULL,
                error TEXT,
                task TEXT,
                created_at INTEGER,
                ended_at INTEGER,
                last_event_at INTEGER
            )
            """
        )
        db.execute(
            "INSERT INTO task_runs (task_id, run_id, status, error, task, created_at, ended_at, last_event_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "old-child-task",
                "old-child-run",
                "failed",
                "old rate limit",
                f"执行命令：python3 skills/auto-koubei-collector/scripts/export_autohome_koubei.py --output {stage.expected_artifacts[0]}",
                500,
                600,
                600,
            ),
        )

    class AcceptedGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            def write_artifacts() -> None:
                time.sleep(0.05)
                for artifact in stage.expected_artifacts:
                    Path(artifact).parent.mkdir(parents=True, exist_ok=True)
                    Path(artifact).write_text("ok", encoding="utf-8")

            threading.Thread(target=write_artifacts, daemon=True).start()
            return {"status": "accepted", "taskId": "new-parent-task", "runId": "new-parent-run", "acceptedAt": 1000}

    result = run_autohome_via_openclaw(
        stage,
        job_paths,
        progress_sink,
        settings=OpenClawSettings(
            enabled=True,
            timeout_seconds=2,
            artifact_poll_interval_seconds=0.01,
            task_db_path=str(task_db),
        ),
        gateway_client=AcceptedGatewayClient(),
    )

    assert result.status == "success"


def test_run_autohome_via_openclaw_fails_when_related_child_succeeds_without_artifacts(tmp_path: Path) -> None:
    stage = make_autohome_stage(tmp_path)
    job_paths = ensure_job_dirs(tmp_path / "jobs", "job_openclaw")
    progress_sink = ProgressSink(job_id="job_openclaw", progress_path=job_paths.progress / "progress.json", stages=[stage.name])
    task_db = tmp_path / "runs.sqlite"
    with sqlite3.connect(task_db) as db:
        db.execute(
            """
            CREATE TABLE task_runs (
                task_id TEXT PRIMARY KEY,
                run_id TEXT,
                status TEXT NOT NULL,
                error TEXT,
                task TEXT,
                created_at INTEGER,
                ended_at INTEGER,
                last_event_at INTEGER
            )
            """
        )
        db.execute(
            "INSERT INTO task_runs (task_id, run_id, status, error, task, created_at, ended_at, last_event_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("parent-task", "parent-run", "succeeded", "", "parent accepted", 1000, 1100, 1100),
        )
        db.execute(
            "INSERT INTO task_runs (task_id, run_id, status, error, task, created_at, ended_at, last_event_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "child-task",
                "child-run",
                "succeeded",
                "",
                f"执行命令：python3 skills/auto-koubei-collector/scripts/export_autohome_koubei.py --output {stage.expected_artifacts[0]}",
                1200,
                1300,
                1300,
            ),
        )

    class AcceptedGatewayClient:
        def call_agent(
            self,
            message: str,
            *,
            settings: OpenClawSettings,
            session_id: str | None = None,
            stage_name: str = "openclaw",
            agent_id: str | None = None,
        ) -> dict:
            return {"status": "accepted", "taskId": "parent-task", "runId": "parent-run"}

    with pytest.raises(StageExecutionError) as exc_info:
        run_autohome_via_openclaw(
            stage,
            job_paths,
            progress_sink,
            settings=OpenClawSettings(
                enabled=True,
                timeout_seconds=30,
                artifact_poll_interval_seconds=0.01,
                task_db_path=str(task_db),
            ),
            gateway_client=AcceptedGatewayClient(),
        )

    assert exc_info.value.error_code == "OPENCLAW_ARTIFACTS_MISSING"
    assert "OpenClaw related task ended but expected artifacts are missing" in exc_info.value.message
