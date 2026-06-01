# carfeedback V3 Production Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the production replacement service in `/opt/codexwork/carFeedback` on port `18080`, with real v3 8-agent OpenClaw pool dispatch and no disruption to the current port `80` service.

**Architecture:** The Temporal worker owns platform lane assignment by selecting a real OpenClaw agent from `autohome-1..4` or `dongchedi-1..4` before a collector run starts. The collector service receives that assigned `agent_id` and passes it through to the OpenClaw gateway, while task-center stores the real assignment in `collection_runs.agent_id` so dashboard lane state matches execution.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Temporal Python SDK, Docker Compose, Nginx, Next.js, OpenClaw gateway.

---

## File Structure

- Create `apps/worker/worker_app/agent_pool.py`: Parse configured platform agent pools and choose available agents.
- Modify `apps/api/app/models.py`: Add a running-agent uniqueness guard for new databases.
- Modify `apps/worker/worker_app/task_store.py`: Include `agent_id` in collection run records, expose running agent state, and add claim/wait helpers.
- Modify `apps/worker/worker_app/collector_models.py`: Add `agent_id` to the worker-side collector request model.
- Modify `apps/collector_service/collector_service/models.py`: Add `agent_id` to the collector service request model.
- Modify `apps/worker/worker_app/openclaw_runner.py`: Allow assigned OpenClaw agent override.
- Modify `apps/collector_service/collector_service/real_runner.py`: Pass `request.agent_id` into the stage runner.
- Modify `apps/worker/worker_app/temporal_activities.py`: Assign pool agents before collector submission and include them in request/event payloads.
- Modify `docker-compose.yml`: Pass OpenClaw pool, gateway, state, and token configuration into API, temporal worker, and collector services.
- Modify `.env.example`: Document v3 OpenClaw pool and new deployment paths.
- Add or modify tests under `apps/worker/tests`, `apps/api/tests`, and `apps/collector_service/tests`.

## Task 1: Agent Pool Helper

**Files:**
- Create: `apps/worker/worker_app/agent_pool.py`
- Test: `apps/worker/tests/test_agent_pool.py`

- [ ] **Step 1: Write failing tests**

Create `apps/worker/tests/test_agent_pool.py`:

```python
from __future__ import annotations

from worker_app.agent_pool import (
    choose_available_agent,
    platform_agent_ids_from_env,
    split_agent_ids,
)


def test_split_agent_ids_trims_and_deduplicates() -> None:
    assert split_agent_ids(" autohome-1,autohome-2,autohome-1 ,, ") == [
        "autohome-1",
        "autohome-2",
    ]


def test_platform_agent_ids_from_env_reads_v3_pools() -> None:
    env = {
        "OPENCLAW_AUTOHOME_AGENT_IDS": "autohome-1,autohome-2,autohome-3,autohome-4",
        "OPENCLAW_DCD_AGENT_IDS": "dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4",
    }

    assert platform_agent_ids_from_env(env) == {
        "autohome": ["autohome-1", "autohome-2", "autohome-3", "autohome-4"],
        "dongchedi": ["dongchedi-1", "dongchedi-2", "dongchedi-3", "dongchedi-4"],
    }


def test_choose_available_agent_skips_busy_agents() -> None:
    configured = {"autohome": ["autohome-1", "autohome-2", "autohome-3"]}
    busy = {"autohome": {"autohome-1", "autohome-3"}}

    assert choose_available_agent("autohome", configured, busy) == "autohome-2"


def test_choose_available_agent_returns_none_when_platform_pool_is_full() -> None:
    configured = {"dongchedi": ["dongchedi-1", "dongchedi-2"]}
    busy = {"dongchedi": {"dongchedi-1", "dongchedi-2"}}

    assert choose_available_agent("dongchedi", configured, busy) is None
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_agent_pool.py -q
```

Expected: FAIL because `worker_app.agent_pool` does not exist.

- [ ] **Step 3: Implement the helper**

Create `apps/worker/worker_app/agent_pool.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
import os


PLATFORM_AGENT_ENV = {
    "autohome": "OPENCLAW_AUTOHOME_AGENT_IDS",
    "dongchedi": "OPENCLAW_DCD_AGENT_IDS",
}


def split_agent_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def platform_agent_ids_from_env(environ: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    env = environ or os.environ
    configured: dict[str, list[str]] = {}
    for platform, env_name in PLATFORM_AGENT_ENV.items():
        agent_ids = split_agent_ids(env.get(env_name))
        if agent_ids:
            configured[platform] = agent_ids
    return configured


def choose_available_agent(
    platform: str,
    configured_agents: Mapping[str, list[str]],
    busy_agents: Mapping[str, set[str]],
) -> str | None:
    for agent_id in configured_agents.get(platform, []):
        if agent_id not in busy_agents.get(platform, set()):
            return agent_id
    return None
```

- [ ] **Step 4: Verify tests pass**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_agent_pool.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/worker_app/agent_pool.py apps/worker/tests/test_agent_pool.py
git commit -m "Add OpenClaw agent pool helper"
```

## Task 2: Persist Real Agent Assignments

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/worker/worker_app/task_store.py`
- Test: `apps/worker/tests/test_task_store.py`

- [ ] **Step 1: Write failing store tests**

Append to `apps/worker/tests/test_task_store.py`:

```python
def test_running_agent_ids_by_platform_tracks_real_openclaw_agents(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'task-store.db'}"
    store = TaskStore(database_url)
    task_id = store.create_task(
        task_type="single",
        display_name="测试车",
        vehicles=[{"query": "测试车", "model_name": "测试车"}],
    ).task_id
    run = store.create_or_join_collection_run(
        task_id=task_id,
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
    )

    claimed = store.claim_collection_run_with_agent(run.run_id, "autohome-2")

    assert claimed.status == "running"
    assert claimed.agent_id == "autohome-2"
    assert store.running_agent_ids_by_platform() == {"autohome": {"autohome-2"}}


def test_mark_collection_run_waiting_agent_keeps_run_dispatchable(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'task-store.db'}"
    store = TaskStore(database_url)
    task_id = store.create_task(
        task_type="single",
        display_name="测试车",
        vehicles=[{"query": "测试车", "model_name": "测试车"}],
    ).task_id
    run = store.create_or_join_collection_run(
        task_id=task_id,
        platform="dongchedi",
        query_key="测试车",
        model_name="测试车",
        series_id="25398",
        mode="incremental",
    )

    waiting = store.mark_collection_run_waiting_agent(run.run_id)

    assert waiting.status == "waiting_agent"
    assert waiting.agent_id is None
```

- [ ] **Step 2: Run the targeted tests and verify failure**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_task_store.py -q
```

Expected: FAIL because `CollectionRunRecord.agent_id`, `claim_collection_run_with_agent`, `mark_collection_run_waiting_agent`, and `running_agent_ids_by_platform` do not exist.

- [ ] **Step 3: Add the database uniqueness guard**

In `apps/api/app/models.py`, extend `CollectionRun.__table_args__`:

```python
    __table_args__ = (
        Index(
            "uq_collection_run_active_identity",
            "platform",
            "query_key",
            "series_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'waiting_agent', 'running', 'retry_wait')"),
            sqlite_where=text("status IN ('queued', 'waiting_agent', 'running', 'retry_wait')"),
        ),
        Index(
            "uq_collection_run_running_agent",
            "agent_id",
            unique=True,
            postgresql_where=text("status = 'running' AND agent_id IS NOT NULL"),
            sqlite_where=text("status = 'running' AND agent_id IS NOT NULL"),
        ),
    )
```

- [ ] **Step 4: Add `agent_id` to worker records and queries**

In `apps/worker/worker_app/task_store.py`, change `CollectionRunRecord`:

```python
@dataclass(frozen=True)
class CollectionRunRecord:
    run_id: str
    platform: str
    query_key: str
    model_name: str
    series_id: str
    status: str
    mode: str
    shared_by_task_ids: list[str]
    agent_id: str | None = None
    failure_category: str | None = None
    output_path: str | None = None
```

Update both `_get_collection_run_row` and `_get_collection_run_row_for_update` SELECT lists:

```sql
SELECT run_id, platform, query_key, model_name, series_id, status, mode,
       shared_by_task_ids, agent_id, failure_category, output_path
```

Update `_load_collection_run`:

```python
            agent_id=str(row["agent_id"]) if row["agent_id"] else None,
```

- [ ] **Step 5: Add store helper methods**

Add these methods near `start_collection_run` in `apps/worker/worker_app/task_store.py`:

```python
    def running_agent_ids_by_platform(self) -> dict[str, set[str]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT platform, agent_id
                    FROM collection_runs
                    WHERE status = 'running'
                      AND agent_id IS NOT NULL
                    """
                )
            ).mappings().all()
        busy: dict[str, set[str]] = {}
        for row in rows:
            busy.setdefault(str(row["platform"]), set()).add(str(row["agent_id"]))
        return busy

    def mark_collection_run_waiting_agent(self, run_id: str) -> CollectionRunRecord:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            row = self._get_collection_run_row_for_update(conn, run_id)
            if row is None:
                raise RuntimeError(f"collection run not found: {run_id}")
            if str(row["status"]) in {"queued", "retry_wait"}:
                conn.execute(
                    text(
                        """
                        UPDATE collection_runs
                        SET status = 'waiting_agent',
                            agent_id = NULL,
                            updated_at = :updated_at
                        WHERE run_id = :run_id
                        """
                    ),
                    {"run_id": run_id, "updated_at": now},
                )
            return self._load_collection_run(conn, run_id)

    def claim_collection_run_with_agent(self, run_id: str, agent_id: str) -> CollectionRunRecord:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            row = self._get_collection_run_row_for_update(conn, run_id)
            if row is None:
                raise RuntimeError(f"collection run not found: {run_id}")
            if str(row["status"]) in {"queued", "waiting_agent", "retry_wait"}:
                conn.execute(
                    text(
                        """
                        UPDATE collection_runs
                        SET status = 'running',
                            agent_id = :agent_id,
                            failure_category = NULL,
                            started_at = COALESCE(started_at, :started_at),
                            finished_at = NULL,
                            updated_at = :updated_at
                        WHERE run_id = :run_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "agent_id": agent_id,
                        "started_at": now,
                        "updated_at": now,
                    },
                )
            return self._load_collection_run(conn, run_id)
```

- [ ] **Step 6: Verify targeted tests pass**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_task_store.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/models.py apps/worker/worker_app/task_store.py apps/worker/tests/test_task_store.py
git commit -m "Track real OpenClaw agents on collection runs"
```

## Task 3: Carry Assigned Agent Through Collector Request

**Files:**
- Modify: `apps/worker/worker_app/collector_models.py`
- Modify: `apps/collector_service/collector_service/models.py`
- Test: `apps/collector_service/tests/test_fake_collector_service.py`

- [ ] **Step 1: Write failing collector API test**

Append to `apps/collector_service/tests/test_fake_collector_service.py`:

```python
def test_post_runs_preserves_assigned_agent_id(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "run-agent.xlsx"
    captured_agent_ids: list[str | None] = []

    def fake_collector(request):
        captured_agent_ids.append(request.agent_id)
        output_path.write_text("raw", encoding="utf-8")
        return {"output_path": str(output_path)}

    monkeypatch.setattr("collector_service.main.run_collector", fake_collector)
    monkeypatch.setenv("COLLECTOR_SERVICE_INLINE", "true")
    client = make_client(monkeypatch)
    payload = run_request("run-agent")
    payload["agent_id"] = "autohome-3"

    response = client.post("/runs", json=payload)

    assert response.status_code == 200
    assert captured_agent_ids == ["autohome-3"]
    assert _run_requests["run-agent"].agent_id == "autohome-3"
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/collector_service/tests/test_fake_collector_service.py::test_post_runs_preserves_assigned_agent_id -q
```

Expected: FAIL because `CollectorRunRequest` has no `agent_id`.

- [ ] **Step 3: Add `agent_id` to both Pydantic models**

In `apps/worker/worker_app/collector_models.py` and `apps/collector_service/collector_service/models.py`, add this field to `CollectorRunRequest`:

```python
    agent_id: str | None = Field(default=None, min_length=1)
```

- [ ] **Step 4: Verify collector test passes**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/collector_service/tests/test_fake_collector_service.py::test_post_runs_preserves_assigned_agent_id -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/worker_app/collector_models.py apps/collector_service/collector_service/models.py apps/collector_service/tests/test_fake_collector_service.py
git commit -m "Pass assigned agent id in collector requests"
```

## Task 4: Use Assigned Agent In OpenClaw Runner

**Files:**
- Modify: `apps/worker/worker_app/openclaw_runner.py`
- Modify: `apps/collector_service/collector_service/real_runner.py`
- Test: `apps/worker/tests/test_openclaw_runner.py`

- [ ] **Step 1: Write failing OpenClaw override test**

Append to `apps/worker/tests/test_openclaw_runner.py`:

```python
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
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_openclaw_runner.py::test_run_collector_via_openclaw_prefers_assigned_agent_id -q
```

Expected: FAIL because `run_collector_via_openclaw` does not accept `assigned_agent_id`.

- [ ] **Step 3: Add assigned-agent support**

In `apps/worker/worker_app/openclaw_runner.py`, update signatures and agent selection:

```python
def run_collector_via_openclaw(
    command: StageCommand,
    job_paths: JobPaths,
    progress_sink: ProgressSink,
    *,
    settings: OpenClawSettings,
    gateway_client: OpenClawGatewayClientProtocol | None = None,
    assigned_agent_id: str | None = None,
) -> StageResult:
    ...
    agent_id = assigned_agent_id or settings.agent_id_for_stage(command.name)
```

Update `run_autohome_via_openclaw`:

```python
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
```

Update `build_stage_runner` to carry the optional assignment:

```python
def build_stage_runner(
    *,
    settings: OpenClawSettings | None = None,
    direct_runner: StageRunnerCallable = run_stage_command,
    assigned_agent_id: str | None = None,
) -> StageRunnerCallable:
    settings = settings or OpenClawSettings.from_env()

    def run(command: StageCommand, job_paths: JobPaths, progress_sink: ProgressSink) -> StageResult:
        if settings.enabled and command.name in settings.stages:
            return run_collector_via_openclaw(
                command,
                job_paths,
                progress_sink,
                settings=settings,
                assigned_agent_id=assigned_agent_id,
            )
        return direct_runner(command, job_paths, progress_sink)

    return run
```

In `apps/collector_service/collector_service/real_runner.py`, update the runner call:

```python
    stage_result = build_stage_runner(assigned_agent_id=request.agent_id)(stage_command, job_paths, progress_sink)
```

- [ ] **Step 4: Verify OpenClaw tests pass**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_openclaw_runner.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/worker/worker_app/openclaw_runner.py apps/collector_service/collector_service/real_runner.py apps/worker/tests/test_openclaw_runner.py
git commit -m "Use assigned OpenClaw agents in collector runs"
```

## Task 5: Assign Pool Agents During Temporal Dispatch

**Files:**
- Modify: `apps/worker/worker_app/temporal_activities.py`
- Test: `apps/worker/tests/test_temporal_integration_fake_collector.py`

- [ ] **Step 1: Write failing dispatch test**

Append to `apps/worker/tests/test_temporal_integration_fake_collector.py`:

```python
def test_dispatch_collection_runs_assigns_distinct_v3_pool_agents(tmp_path: Path, monkeypatch) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'dispatch.db'}"
    store = TaskStore(database_url)
    task_id = store.create_task(
        task_type="single",
        display_name="测试车",
        vehicles=[{"query": "测试车", "model_name": "测试车"}],
    ).task_id
    run_1 = store.create_or_join_collection_run(
        task_id=task_id,
        platform="autohome",
        query_key="测试车-A",
        model_name="测试车A",
        series_id="8089",
        mode="incremental",
    )
    run_2 = store.create_or_join_collection_run(
        task_id=task_id,
        platform="autohome",
        query_key="测试车-B",
        model_name="测试车B",
        series_id="8090",
        mode="incremental",
    )
    captured_requests = []

    class CapturingCollectorClient:
        def submit_run(self, request):
            captured_requests.append(request)
            return type(
                "Status",
                (),
                {
                    "status": "running",
                    "progress_current": 0,
                    "progress_total": 1,
                    "output_path": None,
                    "failure_category": None,
                    "resume_cursor": {},
                },
            )()

    activities = TaskActivities(database_url)
    monkeypatch.setenv("OPENCLAW_AUTOHOME_AGENT_IDS", "autohome-1,autohome-2")
    monkeypatch.setenv("OPENCLAW_DCD_AGENT_IDS", "dongchedi-1,dongchedi-2")
    monkeypatch.setattr(activities, "_collector_client", lambda platform: CapturingCollectorClient())

    asyncio.run(activities._dispatch_pending_collection_runs(task_id, [run_1.run_id, run_2.run_id]))

    assert [request.agent_id for request in captured_requests] == ["autohome-1", "autohome-2"]
    stored_runs = store.load_collection_runs([run_1.run_id, run_2.run_id])
    assert [run.agent_id for run in stored_runs] == ["autohome-1", "autohome-2"]
```

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_temporal_integration_fake_collector.py::test_dispatch_collection_runs_assigns_distinct_v3_pool_agents -q
```

Expected: FAIL because dispatch still records `collector-service:autohome` and does not pass `agent_id`.

- [ ] **Step 3: Import agent pool helpers**

In `apps/worker/worker_app/temporal_activities.py`, add:

```python
from worker_app.agent_pool import choose_available_agent, platform_agent_ids_from_env
```

- [ ] **Step 4: Add assignment helpers**

In `TaskActivities`, add:

```python
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
        return store.claim_collection_run_with_agent(run.run_id, agent_id)
```

- [ ] **Step 5: Use assigned agent during dispatch**

In `_dispatch_collection_run`, replace:

```python
        started = store.start_collection_run(queued_run.run_id, agent_id=f"collector-service:{queued_run.platform}")
```

with:

```python
        started = self._claim_run_agent(store, queued_run)
        if started is None:
            store.record_collector_event(
                queued_run.run_id,
                "collector_waiting_agent",
                {
                    "task_id": owner_task_id,
                    "platform": queued_run.platform,
                    "message": "No platform OpenClaw agent is currently available.",
                },
            )
            return
```

Update `_collector_request`:

```python
            agent_id=run.agent_id,
```

Update the `collector_submitted` event payload:

```python
                    "agent_id": started.agent_id,
```

- [ ] **Step 6: Dispatch pending runs sequentially**

In `_dispatch_pending_collection_runs`, replace the parallel `asyncio.gather` block with sequential dispatch so each claim observes the previous claim:

```python
        for run in pending:
            await asyncio.to_thread(self._dispatch_collection_run, run, task_id=task_id)
```

- [ ] **Step 7: Verify dispatch test passes**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_temporal_integration_fake_collector.py::test_dispatch_collection_runs_assigns_distinct_v3_pool_agents -q
```

Expected: PASS.

- [ ] **Step 8: Run adjacent worker tests**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/worker/tests/test_temporal_integration_fake_collector.py apps/worker/tests/test_single_vehicle_workflow.py apps/worker/tests/test_comparison_workflow.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/worker/worker_app/temporal_activities.py apps/worker/tests/test_temporal_integration_fake_collector.py
git commit -m "Dispatch collection runs through v3 agent pool"
```

## Task 6: Compose And Env For New Production Runtime

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Test: `docker compose config --quiet`

- [ ] **Step 1: Add OpenClaw pool settings to `.env.example`**

In `.env.example`, add:

```env
OPENCLAW_ADAPTER_ENABLED=true
OPENCLAW_ADAPTER_STAGES=collecting_autohome,collecting_dcd
OPENCLAW_GATEWAY_URL=ws://host.docker.internal:18790
OPENCLAW_GATEWAY_TOKEN_FILE=/run/secrets/openclaw_gateway_token
OPENCLAW_GATEWAY_TOKEN_FILE_HOST=./.runtime/secrets/openclaw_gateway_token
OPENCLAW_STATE_HOST_PATH=/home/ubuntu/.openclaw-koubei
OPENCLAW_TASK_DB_PATH=/openclaw-state/tasks/runs.sqlite
OPENCLAW_DEVICE_IDENTITY_FILE=/openclaw-state/identity/device.json
OPENCLAW_AGENT_ID=main
OPENCLAW_AUTOHOME_AGENT_IDS=autohome-1,autohome-2,autohome-3,autohome-4
OPENCLAW_DCD_AGENT_IDS=dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4
OPENCLAW_AGENT_LEASE_SECONDS=2400
OPENCLAW_AGENT_POOL_WAIT_SECONDS=1800
OPENCLAW_TIMEOUT_SECONDS=1800
OPENCLAW_ARTIFACT_POLL_INTERVAL_SECONDS=5
OPENCLAW_COLLECTOR_SKILL=sh3rlockC/auto-koubei-collector
OPENCLAW_AUTOHOME_COLLECTOR_SKILL=sh3rlockC/auto-koubei-collector
OPENCLAW_DCD_COLLECTOR_SKILL=sh3rlockC/dcd-koubei-collector
OPENCLAW_ARTIFACT_ROOT_HOST=./storage/jobs
```

- [ ] **Step 2: Add API lane-display env to `docker-compose.yml`**

In the `api.environment` block, add:

```yaml
      OPENCLAW_AUTOHOME_AGENT_IDS: ${OPENCLAW_AUTOHOME_AGENT_IDS:-autohome-1,autohome-2,autohome-3,autohome-4}
      OPENCLAW_DCD_AGENT_IDS: ${OPENCLAW_DCD_AGENT_IDS:-dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4}
```

- [ ] **Step 3: Add Temporal worker dispatch env to `docker-compose.yml`**

In the `temporal-worker.environment` block, add:

```yaml
      OPENCLAW_AUTOHOME_AGENT_IDS: ${OPENCLAW_AUTOHOME_AGENT_IDS:-autohome-1,autohome-2,autohome-3,autohome-4}
      OPENCLAW_DCD_AGENT_IDS: ${OPENCLAW_DCD_AGENT_IDS:-dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4}
      OPENCLAW_AGENT_LEASE_SECONDS: ${OPENCLAW_AGENT_LEASE_SECONDS:-2400}
      OPENCLAW_AGENT_POOL_WAIT_SECONDS: ${OPENCLAW_AGENT_POOL_WAIT_SECONDS:-1800}
```

- [ ] **Step 4: Add OpenClaw env, state mount, and secret to both collector services**

In both `autohome-collector` and `dongchedi-collector`, add environment:

```yaml
      OPENCLAW_ADAPTER_ENABLED: ${OPENCLAW_ADAPTER_ENABLED:-true}
      OPENCLAW_ADAPTER_STAGES: ${OPENCLAW_ADAPTER_STAGES:-collecting_autohome,collecting_dcd}
      OPENCLAW_GATEWAY_URL: ${OPENCLAW_GATEWAY_URL:-ws://host.docker.internal:18790}
      OPENCLAW_GATEWAY_TOKEN_FILE: ${OPENCLAW_GATEWAY_TOKEN_FILE:-/run/secrets/openclaw_gateway_token}
      OPENCLAW_AGENT_ID: ${OPENCLAW_AGENT_ID:-main}
      OPENCLAW_AUTOHOME_AGENT_IDS: ${OPENCLAW_AUTOHOME_AGENT_IDS:-autohome-1,autohome-2,autohome-3,autohome-4}
      OPENCLAW_DCD_AGENT_IDS: ${OPENCLAW_DCD_AGENT_IDS:-dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4}
      OPENCLAW_TIMEOUT_SECONDS: ${OPENCLAW_TIMEOUT_SECONDS:-1800}
      OPENCLAW_ARTIFACT_POLL_INTERVAL_SECONDS: ${OPENCLAW_ARTIFACT_POLL_INTERVAL_SECONDS:-5}
      OPENCLAW_COLLECTOR_SKILL: ${OPENCLAW_COLLECTOR_SKILL:-sh3rlockC/auto-koubei-collector}
      OPENCLAW_AUTOHOME_COLLECTOR_SKILL: ${OPENCLAW_AUTOHOME_COLLECTOR_SKILL:-sh3rlockC/auto-koubei-collector}
      OPENCLAW_DCD_COLLECTOR_SKILL: ${OPENCLAW_DCD_COLLECTOR_SKILL:-sh3rlockC/dcd-koubei-collector}
      OPENCLAW_ARTIFACT_ROOT_HOST: ${OPENCLAW_ARTIFACT_ROOT_HOST:-}
      OPENCLAW_TASK_DB_PATH: ${OPENCLAW_TASK_DB_PATH:-/openclaw-state/tasks/runs.sqlite}
      OPENCLAW_DEVICE_IDENTITY_FILE: ${OPENCLAW_DEVICE_IDENTITY_FILE:-/openclaw-state/identity/device.json}
```

Add volume:

```yaml
      - ${OPENCLAW_STATE_HOST_PATH:-./ops/openclaw-state-placeholder}:/openclaw-state:ro
```

Add secret:

```yaml
    secrets:
      - openclaw_gateway_token
```

- [ ] **Step 5: Add compose secret definition**

At the bottom of `docker-compose.yml`, add:

```yaml
secrets:
  openclaw_gateway_token:
    file: ${OPENCLAW_GATEWAY_TOKEN_FILE_HOST:-./ops/secrets/openclaw_gateway_token.placeholder}
```

- [ ] **Step 6: Verify compose config**

Run:

```bash
docker compose config --quiet
```

Expected: exits `0`.

- [ ] **Step 7: Commit**

```bash
git add .env.example docker-compose.yml
git commit -m "Configure collectors for OpenClaw agent pools"
```

## Task 7: Full Local Verification

**Files:**
- No source edits unless tests expose a defect in earlier tasks.

- [ ] **Step 1: Run backend and worker tests**

Run:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/api/tests apps/worker/tests apps/collector_service/tests -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend typecheck**

Run:

```bash
npm --prefix apps/web run typecheck
```

Expected: PASS.

- [ ] **Step 3: Run frontend build**

Run:

```bash
npm --prefix apps/web run build
```

Expected: PASS.

- [ ] **Step 4: Run compose validation**

Run:

```bash
docker compose config --quiet
```

Expected: exits `0`.

- [ ] **Step 5: Commit verification-only fixes if needed**

If a verification command exposes a defect from Tasks 1-6, inspect the exact changed files before committing:

```bash
git status --short
```

If the defect fix touches `apps/worker/worker_app/temporal_activities.py` and `apps/worker/tests/test_temporal_integration_fake_collector.py`, commit exactly those files:

```bash
git add apps/worker/worker_app/temporal_activities.py apps/worker/tests/test_temporal_integration_fake_collector.py
git commit -m "Fix v3 rebuild verification issues"
```

If the defect fix touches a different pair from Tasks 1-6, add exactly the files shown by `git status --short` for that defect and use the same commit message.

## Task 8: Server Runtime Configuration And Parallel Deploy

**Files:**
- Server directory: `/opt/codexwork/carFeedback`
- Runtime files: `/opt/codexwork/carFeedback/.env`, `/opt/codexwork/carFeedback/.runtime/secrets/openclaw_gateway_token`

- [ ] **Step 1: Create the new server directory**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'mkdir -p /opt/codexwork/carFeedback/.runtime/secrets /opt/codexwork/carFeedback/storage/jobs /opt/codexwork/carFeedback/storage/corpus'
```

Expected: exits `0`.

- [ ] **Step 2: Sync code without local dependency/cache directories**

Run from `/Users/xyc/Documents/codexwork/carfeedback`:

```bash
rsync -az --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'apps/api/.venv/' \
  --exclude 'apps/web/node_modules/' \
  --exclude 'apps/web/.next/' \
  --exclude '.pytest_cache/' \
  --exclude 'storage/jobs/' \
  --exclude 'storage/corpus/' \
  ./ ubuntu@129.211.223.252:/opt/codexwork/carFeedback/
```

Expected: code is present under `/opt/codexwork/carFeedback`.

- [ ] **Step 3: Copy the OpenClaw gateway token into the new runtime secret**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'install -m 0600 /opt/codexwork/openclaw-koubei-runtime/gateway.token /opt/codexwork/carFeedback/.runtime/secrets/openclaw_gateway_token'
```

Expected: exits `0`; do not print token contents.

- [ ] **Step 4: Generate the new `.env` from old production configuration**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'python3 - <<"PY"
from pathlib import Path

old_path = Path("/opt/codexwork/vehicle-koubei-web-demo/.env")
new_path = Path("/opt/codexwork/carFeedback/.env")

old = {}
for line in old_path.read_text(encoding="utf-8").splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    old[key] = value

keep = {
    "POSTGRES_PASSWORD",
    "SESSION_SECRET",
    "TAVILY_API_KEY",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL_BATCH",
    "LLM_MODEL_REPORT",
    "LLM_MODEL_QA",
    "HERMES_COMMAND",
    "HERMES_LLM_MODE",
    "HERMES_BATCH_CONCURRENCY",
    "HERMES_BATCH_TARGET_BYTES",
    "HERMES_TIMEOUT_SECONDS",
    "HERMES_AGGREGATE_TIMEOUT_SECONDS",
    "HERMES_JSON_RETRIES",
    "WORDCLOUD_FONT_PATH",
}

env = {key: old[key] for key in keep if old.get(key) is not None}
postgres_password = env.get("POSTGRES_PASSWORD", old.get("POSTGRES_PASSWORD", "koubei"))
env.update(
    {
        "APP_ENV": "production",
        "BASE_URL": "http://129.211.223.252:18080/car-user-feedback",
        "BACKEND_ORIGIN": "http://api:8000",
        "NEXT_PUBLIC_BASE_PATH": "/car-user-feedback",
        "NEXT_PUBLIC_ACCESS_CONTROL_ENABLED": "false",
        "ACCESS_CONTROL_ENABLED": "false",
        "HTTP_PORT": "18080",
        "POSTGRES_DB": "koubei",
        "POSTGRES_USER": "koubei",
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": f"postgresql+psycopg://koubei:{postgres_password}@postgres:5432/koubei",
        "REDIS_URL": "redis://redis:6379/0",
        "ARTIFACT_ROOT": "/srv/koubei/jobs",
        "JOB_ARTIFACTS_HOST_PATH": "/opt/codexwork/carFeedback/storage/jobs",
        "CORPUS_HOST_PATH": "/opt/codexwork/carFeedback/storage/corpus",
        "WORKSPACE_ROOT": "/workspace",
        "TEMPORAL_ADDRESS": "temporal:7233",
        "TEMPORAL_NAMESPACE": "default",
        "TEMPORAL_TASK_QUEUE": "vehicle-koubei-temporal",
        "TEMPORAL_DBNAME": "temporal",
        "TEMPORAL_VISIBILITY_DBNAME": "temporal_visibility",
        "AUTOHOME_COLLECTOR_SERVICE_URL": "http://autohome-collector:8100",
        "DCD_COLLECTOR_SERVICE_URL": "http://dongchedi-collector:8100",
        "OPENCLAW_ADAPTER_ENABLED": "true",
        "OPENCLAW_ADAPTER_STAGES": "collecting_autohome,collecting_dcd",
        "OPENCLAW_GATEWAY_URL": "ws://host.docker.internal:18790",
        "OPENCLAW_GATEWAY_TOKEN_FILE": "/run/secrets/openclaw_gateway_token",
        "OPENCLAW_GATEWAY_TOKEN_FILE_HOST": "/opt/codexwork/carFeedback/.runtime/secrets/openclaw_gateway_token",
        "OPENCLAW_STATE_HOST_PATH": "/home/ubuntu/.openclaw-koubei",
        "OPENCLAW_TASK_DB_PATH": "/openclaw-state/tasks/runs.sqlite",
        "OPENCLAW_DEVICE_IDENTITY_FILE": "/openclaw-state/identity/device.json",
        "OPENCLAW_AGENT_ID": "main",
        "OPENCLAW_AUTOHOME_AGENT_IDS": "autohome-1,autohome-2,autohome-3,autohome-4",
        "OPENCLAW_DCD_AGENT_IDS": "dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4",
        "OPENCLAW_AGENT_LEASE_SECONDS": "2400",
        "OPENCLAW_AGENT_POOL_WAIT_SECONDS": "1800",
        "OPENCLAW_TIMEOUT_SECONDS": "1800",
        "OPENCLAW_ARTIFACT_POLL_INTERVAL_SECONDS": "5",
        "OPENCLAW_COLLECTOR_SKILL": "sh3rlockC/auto-koubei-collector",
        "OPENCLAW_AUTOHOME_COLLECTOR_SKILL": "sh3rlockC/auto-koubei-collector",
        "OPENCLAW_DCD_COLLECTOR_SKILL": "sh3rlockC/dcd-koubei-collector",
        "OPENCLAW_ARTIFACT_ROOT_HOST": "/opt/codexwork/carFeedback/storage/jobs",
    }
)

ordered = sorted(env)
new_path.write_text("".join(f"{key}={env[key]}\n" for key in ordered), encoding="utf-8")
new_path.chmod(0o600)
PY'
```

Expected: `/opt/codexwork/carFeedback/.env` exists with mode `0600`; do not print secret values.

- [ ] **Step 5: Validate new compose configuration on the server**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'cd /opt/codexwork/carFeedback && sudo -n docker compose -p carfeedback-v3 config --quiet'
```

Expected: exits `0`.

- [ ] **Step 6: Build and start the parallel service**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'cd /opt/codexwork/carFeedback && sudo -n docker compose -p carfeedback-v3 up -d --build'
```

Expected: containers start under project `carfeedback-v3`; old `koubei-20260527` containers remain running.

- [ ] **Step 7: Verify container health**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'sudo -n docker ps --format "{{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "carfeedback-v3|koubei-20260527"'
```

Expected: `carfeedback-v3` containers are up and healthy; `koubei-20260527-nginx-1` still owns port `80`.

## Task 9: Server Smoke Tests And Agent-Pool Verification

**Files:**
- No source edits unless smoke tests expose defects.

- [ ] **Step 1: Verify HTTP health and base path**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'curl -fsS http://127.0.0.1:18080/healthz && curl -fsS -I http://127.0.0.1:18080/car-user-feedback/ | sed -n "1,10p"'
```

Expected: `/healthz` returns `ok`; base path returns `200` or a valid Next.js response.

- [ ] **Step 2: Verify old service remains available**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'curl -fsS -I http://127.0.0.1/car-user-feedback/ | sed -n "1,10p"'
```

Expected: old port `80` service still responds.

- [ ] **Step 3: Verify OpenClaw gateway and agents**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'systemctl is-active openclaw-koubei.service && timeout 10s openclaw --profile koubei agents list --json | python3 -c "import json,sys; ids=[a.get(\"id\") for a in json.load(sys.stdin)]; print(ids); assert all(x in ids for x in [\"autohome-1\",\"autohome-2\",\"autohome-3\",\"autohome-4\",\"dongchedi-1\",\"dongchedi-2\",\"dongchedi-3\",\"dongchedi-4\"])"'
```

Expected: systemd service is `active`; all eight pool agents exist.

- [ ] **Step 4: Create one real single task through the UI or API**

Use the web UI at:

```text
http://129.211.223.252:18080/car-user-feedback/
```

Create a single vehicle task with a known valid Autohome and Dongchedi series candidate. If using API, first resolve and validate candidates, then `POST /api/tasks` with the selected platform inputs.

Expected: task enters running state and creates two collection runs.

- [ ] **Step 5: Verify real agent ids in the new database**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'sudo -n docker exec carfeedback-v3-postgres-1 psql -U koubei -d koubei -P pager=off -c "SELECT platform, agent_id, status, count(*) FROM collection_runs GROUP BY platform, agent_id, status ORDER BY platform, agent_id, status;"'
```

Expected: `agent_id` values are only from `autohome-1..4` and `dongchedi-1..4` for real platform collection. There must be no new-service rows using `autohome`, `dongchedi`, or `main`.

- [ ] **Step 6: Verify artifacts are written under the new directory**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'find /opt/codexwork/carFeedback/storage/jobs -maxdepth 3 -type f | sed -n "1,80p"'
```

Expected: task artifacts appear under `/opt/codexwork/carFeedback/storage/jobs`.

- [ ] **Step 7: Verify old service artifacts were not mutated by the new task**

Run:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'find /opt/codexwork/vehicle-koubei-web-demo/storage/jobs -maxdepth 1 -type d -printf "%TY-%Tm-%Td %TH:%TM %p\n" | sort | tail -10'
```

Expected: no new task directory from the `18080` validation appears in the old service artifact root.

- [ ] **Step 8: Run one comparison task**

Use the web UI at:

```text
http://129.211.223.252:18080/car-user-feedback/tasks/new
```

Create a 2-vehicle comparison task.

Expected: child single tasks are created and collection runs use the 8-agent pool.

- [ ] **Step 9: Record deployment result**

Add a short note to `docs/maintenance/service-flow-and-extension.md` or a new dated maintenance note with:

```markdown
## 2026-05-31 carfeedback v3 parallel deployment

- Directory: `/opt/codexwork/carFeedback`
- Compose project: `carfeedback-v3`
- Port: `18080`
- Base path: `/car-user-feedback`
- OpenClaw pool: `autohome-1..4`, `dongchedi-1..4`
- Old service status: `koubei-20260527` still running on port `80`
```

Commit the note:

```bash
git add docs/maintenance/service-flow-and-extension.md
git commit -m "Document carfeedback v3 parallel deployment"
```

## Final Verification

Run all local checks once more after any smoke-test fixes:

```bash
APP_ENV=test DATABASE_URL='sqlite+pysqlite:////tmp/vehicle-koubei-pytest.db' .venv/bin/pytest apps/api/tests apps/worker/tests apps/collector_service/tests -q
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
docker compose config --quiet
```

Run server checks:

```bash
ssh -i ~/.ssh/vehicle_koubei_tencent ubuntu@129.211.223.252 'curl -fsS http://127.0.0.1:18080/healthz && curl -fsS http://127.0.0.1/healthz && sudo -n docker ps --format "{{.Names}}\t{{.Status}}" | grep -E "carfeedback-v3|koubei-20260527"'
```

Expected:

- Local tests pass.
- Frontend typecheck and build pass.
- Compose config is valid.
- New `18080` service is healthy.
- Old `80` service is still healthy.
- New collection runs use only `autohome-1..4` and `dongchedi-1..4`.
