from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_task_store import create_schema, seed_task, seed_vehicle
from worker_app.task_store import TaskStore
from worker_app.temporal_activities import TaskActivities
from worker_app.temporal_workflows import estimate_comparison_seconds, run_comparison_task


class FakeComparisonActivityRunner:
    def __init__(self, responses: dict[str, list[Any]], events: list[str] | None = None) -> None:
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, Any]] = []
        self.events = events

    async def __call__(self, name: str, payload: Any, timeout: Any) -> dict[str, Any]:
        self.calls.append((name, payload))
        if self.events is not None:
            vehicle = payload.get("vehicle") if isinstance(payload, dict) else None
            vehicle_id = vehicle.get("id") if isinstance(vehicle, dict) else ""
            self.events.append(f"activity:{name}:{vehicle_id}")
        values = self.responses.get(name)
        if not values:
            return {}
        value = values.pop(0)
        if callable(value):
            return value(payload)
        return value

    def call_names(self) -> list[str]:
        return [name for name, _payload in self.calls]

    def payloads(self, name: str) -> list[Any]:
        return [payload for call_name, payload in self.calls if call_name == name]


def run_workflow(runner: FakeComparisonActivityRunner) -> dict[str, Any]:
    return asyncio.run(run_comparison_task("cmp_1", runner))


class FakeChildWorkflowRunner:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        vehicle = payload["vehicle"]
        self.events.append(f"child:{vehicle['id']}")
        return {"started": True}


class FakeChildCompletionRunner:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        vehicle = payload["vehicle"]
        self.events.append(f"child_complete:{vehicle['id']}")
        return {"status": "completed"}


def comparison_task() -> dict[str, Any]:
    return {
        "comparison_id": "cmp_1",
        "status": "queued",
        "start_date": None,
        "end_date": None,
        "passphrase_version": "2026-W17",
        "vehicles": [
            {"id": 1, "position": 1, "query": "测试车A", "model_name": "测试车A"},
            {"id": 2, "position": 2, "query": "测试车B", "model_name": "测试车B"},
            {
                "id": 3,
                "position": 3,
                "query": "测试车C",
                "model_name": "测试车C",
                "source_job_id": "job_reused_c",
            },
        ],
    }


def selected_candidates(autohome_id: str, dcd_id: str, title: str) -> dict[str, Any]:
    return {
        "autohome": {"series_id": autohome_id, "title": title},
        "dongchedi": {"series_id": dcd_id, "title": title},
    }


def usable_vehicle(vehicle_id: int, model_name: str, *, reused: bool = False, **overrides: Any) -> dict[str, Any]:
    source_job_id = overrides.pop("source_job_id", f"job_{vehicle_id}")
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
            "final_report_path": f"/tmp/{source_job_id}.final_report.json",
            "analysis_facts_path": f"/tmp/{source_job_id}.analysis_facts.jsonl",
        },
        **overrides,
    }


def test_comparison_collects_two_vehicles_and_reuses_existing_corpus_snapshot() -> None:
    def wait_result(payload: dict[str, Any]) -> dict[str, Any]:
        vehicle = payload["vehicle"]
        if vehicle["id"] == 1:
            return usable_vehicle(1, "测试车A")
        if vehicle["id"] == 2:
            return usable_vehicle(2, "测试车B")
        return usable_vehicle(3, "测试车C", reused=True, source_job_id="job_reused_c")

    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [
                {"child_workflow_id": "single_vehicle:cmp_1:1", "child_task_id": "job_1"},
                {"child_workflow_id": "single_vehicle:cmp_1:2", "child_task_id": "job_2"},
            ],
            "wait_for_vehicle_results": [wait_result, wait_result, wait_result],
            "generate_comparison_report": [
                {"report_json": {"headline": "竞品口碑对比"}, "artifact_paths": ["/tmp/final_comparison.json"]}
            ],
            "publish_comparison_result": [{"status": "completed"}],
        }
    )

    result = run_workflow(runner)

    assert result == {"comparison_id": "cmp_1", "status": "completed", "vehicle_count": 3, "excluded": []}
    ensure_payloads = runner.payloads("ensure_vehicle_subworkflow")
    assert [payload["vehicle"]["id"] for payload in ensure_payloads] == [1, 2]
    report_payload = runner.payloads("generate_comparison_report")[0]
    assert [vehicle["vehicle_id"] for vehicle in report_payload["vehicles"]] == [1, 2, 3]
    assert report_payload["vehicles"][2]["reused"] is True


def test_comparison_starts_all_child_workflows_before_waiting_for_results() -> None:
    events: list[str] = []
    child_runner = FakeChildWorkflowRunner(events)
    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [
                {"child_workflow_id": "single_vehicle:cmp_1:1", "child_task_id": "job_1"},
                {"child_workflow_id": "single_vehicle:cmp_1:2", "child_task_id": "job_2"},
            ],
            "wait_for_vehicle_results": [
                usable_vehicle(1, "测试车A"),
                usable_vehicle(2, "测试车B"),
                usable_vehicle(3, "测试车C", reused=True, source_job_id="job_reused_c"),
            ],
            "generate_comparison_report": [{"report_json": {}, "artifact_paths": []}],
            "publish_comparison_result": [{"status": "completed"}],
        },
        events=events,
    )

    asyncio.run(run_comparison_task("cmp_1", runner, child_workflow_runner=child_runner))

    assert [call["child_task_id"] for call in child_runner.calls] == ["job_1", "job_2"]
    first_wait_index = next(index for index, event in enumerate(events) if event.startswith("activity:wait_for_vehicle_results"))
    assert events[:first_wait_index] == [
        "activity:load_comparison_task:",
        "activity:ensure_vehicle_subworkflow:1",
        "activity:ensure_vehicle_subworkflow:2",
        "child:1",
        "child:2",
    ]


def test_degraded_vehicle_enters_comparison_with_incomplete_source_label() -> None:
    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [{"child_task_id": "job_1"}, {"child_task_id": "job_2"}],
            "wait_for_vehicle_results": [
                usable_vehicle(
                    1,
                    "测试车A",
                    degraded=True,
                    incomplete_sources=["dongchedi"],
                    labels=["incomplete_source"],
                ),
                usable_vehicle(2, "测试车B"),
                usable_vehicle(3, "测试车C", reused=True, source_job_id="job_reused_c"),
            ],
            "generate_comparison_report": [{"report_json": {}, "artifact_paths": [], "degraded": True}],
            "publish_comparison_result": [{"status": "completed_degraded"}],
        }
    )

    result = run_workflow(runner)

    report_payload = runner.payloads("generate_comparison_report")[0]
    degraded_vehicle = report_payload["vehicles"][0]
    assert result["status"] == "completed_degraded"
    assert degraded_vehicle["usable"] is True
    assert degraded_vehicle["degraded"] is True
    assert degraded_vehicle["incomplete_sources"] == ["dongchedi"]
    assert "incomplete_source" in degraded_vehicle["labels"]


def test_upgraded_vehicle_regenerates_comparison_after_initial_publish() -> None:
    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [{"child_task_id": "job_1"}, {"child_task_id": "job_2"}],
            "wait_for_vehicle_results": [
                usable_vehicle(1, "测试车A", degraded=True, upgraded_to_full=True),
                usable_vehicle(2, "测试车B"),
                usable_vehicle(3, "测试车C", reused=True, source_job_id="job_reused_c"),
            ],
            "generate_comparison_report": [{"report_json": {"version": "initial"}, "artifact_paths": ["/tmp/initial.json"]}],
            "publish_comparison_result": [{"status": "completed_degraded"}],
            "regenerate_comparison_after_upgrade": [
                {"status": "completed_upgraded", "artifact_paths": ["/tmp/upgraded.json"], "upgraded": True}
            ],
        }
    )

    result = run_workflow(runner)

    assert result["status"] == "completed_upgraded"
    assert runner.call_names().index("publish_comparison_result") < runner.call_names().index(
        "regenerate_comparison_after_upgrade"
    )
    regenerate_payload = runner.payloads("regenerate_comparison_after_upgrade")[0]
    assert regenerate_payload["upgraded_vehicle_ids"] == [1]
    assert regenerate_payload["previous_report"]["report_json"] == {"version": "initial"}


def test_comparison_waits_for_child_completion_before_upgrade_regeneration() -> None:
    events: list[str] = []
    completion_runner = FakeChildCompletionRunner(events)
    wait_results = [
        usable_vehicle(1, "测试车A", degraded=True, incomplete_sources=["partial_collection"]),
        usable_vehicle(2, "测试车B"),
        usable_vehicle(3, "测试车C", reused=True, source_job_id="job_reused_c"),
        usable_vehicle(1, "测试车A", degraded=True, upgraded_to_full=True),
        usable_vehicle(2, "测试车B"),
    ]
    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [{"child_task_id": "job_1"}, {"child_task_id": "job_2"}],
            "wait_for_vehicle_results": wait_results,
            "generate_comparison_report": [{"report_json": {"version": "initial"}, "artifact_paths": ["/tmp/initial.json"]}],
            "publish_comparison_result": [{"status": "completed_degraded"}],
            "regenerate_comparison_after_upgrade": [{"status": "completed_upgraded"}],
        },
        events=events,
    )

    result = asyncio.run(
        run_comparison_task(
            "cmp_1",
            runner,
            child_completion_runner=completion_runner,
        )
    )

    assert result["status"] == "completed_upgraded"
    assert [call["child_task_id"] for call in completion_runner.calls] == ["job_1", "job_2"]
    assert events.index("activity:publish_comparison_result:") < events.index("child_complete:1")
    assert runner.call_names().count("wait_for_vehicle_results") == 5
    regenerate_payload = runner.payloads("regenerate_comparison_after_upgrade")[0]
    assert regenerate_payload["upgraded_vehicle_ids"] == [1]


def test_activity_returns_upgraded_to_full_from_child_job_record(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker.db"
    final_report = tmp_path / "job_child.final_report.json"
    analysis_facts = tmp_path / "job_child.analysis_facts.jsonl"
    final_report.write_text('{"headline":"ok"}', encoding="utf-8")
    analysis_facts.write_text('{"comment_id":"1"}\n', encoding="utf-8")

    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                degraded INTEGER NOT NULL DEFAULT 0,
                upgraded_to_full INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                error_message TEXT
            );
            CREATE TABLE comparison_vehicles (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                source_job_id TEXT,
                child_job_id TEXT,
                error_code TEXT,
                error_message TEXT,
                updated_at TEXT
            );
            CREATE TABLE job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                artifact_path TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO jobs (job_id, status, degraded, upgraded_to_full) VALUES (?, ?, ?, ?)",
            ("job_child", "completed", 1, 1),
        )
        connection.execute(
            "INSERT INTO comparison_vehicles (id, status, child_job_id) VALUES (?, ?, ?)",
            (1, "running", "job_child"),
        )
        connection.executemany(
            "INSERT INTO job_artifacts (job_id, artifact_path) VALUES (?, ?)",
            [("job_child", str(final_report)), ("job_child", str(analysis_facts))],
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    activities = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    result = asyncio.run(
        activities.wait_for_vehicle_results(
            {
                "comparison_id": "cmp_1",
                "vehicle": {"id": 1, "position": 1, "query": "测试车A", "model_name": "测试车A"},
                "subworkflow": {"child_task_id": "job_child"},
                "reused": False,
            }
        )
    )

    assert result["usable"] is True
    assert result["upgraded_to_full"] is True


def test_activity_treats_completed_degraded_reused_job_as_usable(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker.db"
    final_report = tmp_path / "job_child.final_report.json"
    analysis_facts = tmp_path / "job_child.analysis_facts.jsonl"
    final_report.write_text('{"headline":"ok"}', encoding="utf-8")
    analysis_facts.write_text('{"comment_id":"1"}\n', encoding="utf-8")

    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                degraded INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                error_message TEXT
            );
            CREATE TABLE comparison_vehicles (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                source_job_id TEXT,
                child_job_id TEXT,
                error_code TEXT,
                error_message TEXT,
                updated_at TEXT
            );
            CREATE TABLE job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                artifact_path TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO jobs (job_id, status, degraded) VALUES (?, ?, ?)",
            ("job_child", "completed_degraded", 1),
        )
        connection.execute(
            "INSERT INTO comparison_vehicles (id, status, source_job_id) VALUES (?, ?, ?)",
            (1, "running", "job_child"),
        )
        connection.executemany(
            "INSERT INTO job_artifacts (job_id, artifact_path) VALUES (?, ?)",
            [("job_child", str(final_report)), ("job_child", str(analysis_facts))],
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("COMPARISON_VEHICLE_WAIT_TIMEOUT_SECONDS", "0")
    activities = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    result = asyncio.run(
        activities.wait_for_vehicle_results(
            {
                "comparison_id": "cmp_1",
                "vehicle": {
                    "id": 1,
                    "position": 1,
                    "query": "测试车A",
                    "model_name": "测试车A",
                    "source_job_id": "job_child",
                },
                "subworkflow": {"source_job_id": "job_child"},
                "reused": True,
            }
        )
    )

    assert result["usable"] is True
    assert result["degraded"] is True
    assert result["incomplete_sources"] == ["partial_collection"]
    assert "incomplete_source" in result["labels"]


def test_ensure_vehicle_subworkflow_creates_loadable_single_vehicle_task(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE comparison_vehicles (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                source_job_id TEXT,
                child_job_id TEXT,
                error_code TEXT,
                error_message TEXT,
                updated_at TEXT
            );
            """
        )
        connection.execute("INSERT INTO comparison_vehicles (id, status) VALUES (?, ?)", (10, "queued"))
        connection.commit()
    finally:
        connection.close()

    activities = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    ensured = asyncio.run(
        activities.ensure_vehicle_subworkflow(
            {
                "comparison_id": "cmp_1",
                "task": {"passphrase_version": "2026-W17"},
                "vehicle": {
                    "id": 10,
                    "position": 2,
                    "query": "测试车A",
                    "model_name": "测试车A",
                    "selected_candidates": selected_candidates("1001", "2001", "测试车A"),
                },
            }
        )
    )

    child_task = TaskStore(f"sqlite+pysqlite:///{db_path}").load_task(ensured["child_task_id"])
    assert child_task.task_id == ensured["child_task_id"]
    assert child_task.task_type == "single_vehicle"
    assert child_task.collection_mode == "incremental"
    assert child_task.vehicles[0].query == "测试车A"
    assert child_task.vehicles[0].autohome_series_id == "1001"
    assert child_task.vehicles[0].dcd_series_id == "2001"
    assert ensured["child_workflow_id"] == f"SingleVehicleTaskWorkflow:{ensured['child_task_id']}"


def test_ensure_vehicle_subworkflow_reuses_child_task_from_comparison_vehicle(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE comparison_vehicles (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                source_job_id TEXT,
                child_job_id TEXT,
                error_code TEXT,
                error_message TEXT,
                updated_at TEXT
            );
            """
        )
        connection.execute("INSERT INTO comparison_vehicles (id, status) VALUES (?, ?)", (10, "queued"))
        connection.commit()
    finally:
        connection.close()

    activities = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")
    payload = {
        "comparison_id": "cmp_1",
        "task": {"passphrase_version": "2026-W17"},
        "vehicle": {
            "id": 10,
            "position": 2,
            "query": "测试车A",
            "model_name": "测试车A",
            "selected_candidates": selected_candidates("1001", "2001", "测试车A"),
        },
    }

    first = asyncio.run(activities.ensure_vehicle_subworkflow(payload))
    second = asyncio.run(activities.ensure_vehicle_subworkflow(payload))

    connection = sqlite3.connect(db_path)
    try:
        task_count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        child_job_id = connection.execute("SELECT child_job_id FROM comparison_vehicles WHERE id = 10").fetchone()[0]
    finally:
        connection.close()

    assert second["child_task_id"] == first["child_task_id"]
    assert task_count == 1
    assert child_job_id == first["child_task_id"]


def test_wait_for_vehicle_results_reads_upgrade_and_snapshot_from_child_task(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_child")
    seed_vehicle(db_path, task_id="task_child", position=1, query="测试车A", model_name="测试车A")
    final_report = tmp_path / "task_child.final_report.json"
    analysis_facts = tmp_path / "task_child.analysis_facts.jsonl"
    final_report.write_text('{"headline":"ok"}', encoding="utf-8")
    analysis_facts.write_text('{"comment_id":"1"}\n', encoding="utf-8")
    snapshot = {
        "model_name": "测试车A",
        "source_job_id": "task_child",
        "final_report_path": str(final_report),
        "analysis_facts_path": str(analysis_facts),
    }

    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE comparison_vehicles (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                source_job_id TEXT,
                child_job_id TEXT,
                error_code TEXT,
                error_message TEXT,
                updated_at TEXT
            );
            """
        )
        connection.execute(
            """
            UPDATE tasks
            SET status = 'completed', current_stage = 'completed', degraded = 1, upgraded_to_full = 1
            WHERE task_id = 'task_child'
            """
        )
        connection.execute(
            "UPDATE task_vehicles SET result_snapshot_json = ? WHERE task_id = ?",
            (json.dumps({"comparison_snapshot": snapshot}), "task_child"),
        )
        connection.execute(
            "INSERT INTO comparison_vehicles (id, status, child_job_id) VALUES (?, ?, ?)",
            (11, "running", "task_child"),
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    activities = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    result = asyncio.run(
        activities.wait_for_vehicle_results(
            {
                "comparison_id": "cmp_1",
                "vehicle": {"id": 11, "position": 1, "query": "测试车A", "model_name": "测试车A"},
                "subworkflow": {"child_task_id": "task_child"},
                "reused": False,
            }
        )
    )

    assert result["usable"] is True
    assert result["source_job_id"] == "task_child"
    assert result["degraded"] is False
    assert result["upgraded_to_full"] is True
    assert result["incomplete_sources"] == []
    assert "incomplete_source" not in result["labels"]
    assert Path(result["snapshot"]["final_report_path"]).exists()
    assert Path(result["snapshot"]["analysis_facts_path"]).exists()


def test_default_single_vehicle_activities_publish_snapshot_consumable_by_comparison(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_child")
    seed_vehicle(db_path, task_id="task_child", position=1, query="测试车A", model_name="测试车A")
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE comparison_vehicles (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                source_job_id TEXT,
                child_job_id TEXT,
                error_code TEXT,
                error_message TEXT,
                updated_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO comparison_vehicles (id, status, child_job_id) VALUES (?, ?, ?)",
            (12, "running", "task_child"),
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    activities = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")
    task = {
        "task_id": "task_child",
        "vehicles": [{"query": "测试车A", "model_name": "测试车A"}],
    }
    results = {"successful_platforms": ["autohome", "dongchedi"], "failed_platforms": []}
    postprocess = asyncio.run(activities.run_postprocess({"task_id": "task_child", "task": task, "results": results}))
    report = asyncio.run(
        activities.run_llm_report(
            {
                "task_id": "task_child",
                "task": task,
                "results": results,
                "postprocess_result": postprocess,
            }
        )
    )
    asyncio.run(
        activities.publish_full_result(
            {
                "task_id": "task_child",
                "task": task,
                "results": results,
                "postprocess_result": postprocess,
                "report_result": report,
            }
        )
    )

    result = asyncio.run(
        activities.wait_for_vehicle_results(
            {
                "comparison_id": "cmp_1",
                "vehicle": {"id": 12, "position": 1, "query": "测试车A", "model_name": "测试车A"},
                "subworkflow": {"child_task_id": "task_child"},
                "reused": False,
            }
        )
    )

    assert postprocess["skipped"] is False
    assert report["skipped"] is False
    assert result["usable"] is True
    assert result["snapshot"]["source_job_id"] == "task_child"
    assert Path(result["snapshot"]["final_report_path"]).exists()
    assert Path(result["snapshot"]["analysis_facts_path"]).exists()


def test_default_degraded_single_vehicle_result_is_usable_by_comparison(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_child")
    seed_vehicle(db_path, task_id="task_child", position=1, query="测试车A", model_name="测试车A")
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE comparison_vehicles (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                source_job_id TEXT,
                child_job_id TEXT,
                error_code TEXT,
                error_message TEXT,
                updated_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO comparison_vehicles (id, status, child_job_id) VALUES (?, ?, ?)",
            (13, "running", "task_child"),
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    activities = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")
    task = {
        "task_id": "task_child",
        "vehicles": [{"query": "测试车A", "model_name": "测试车A"}],
    }
    results = {
        "successful_platforms": ["autohome"],
        "failed_platforms": [
            {"platform": "dongchedi", "failure_category": "timeout", "retryable": True},
        ],
    }
    postprocess = asyncio.run(activities.run_postprocess({"task_id": "task_child", "task": task, "results": results}))
    report = asyncio.run(
        activities.run_llm_report(
            {
                "task_id": "task_child",
                "task": task,
                "results": results,
                "postprocess_result": postprocess,
            }
        )
    )
    asyncio.run(
        activities.publish_degraded_result(
            {
                "task_id": "task_child",
                "task": task,
                "results": results,
                "postprocess_result": postprocess,
                "report_result": report,
            }
        )
    )

    result = asyncio.run(
        activities.wait_for_vehicle_results(
            {
                "comparison_id": "cmp_1",
                "vehicle": {"id": 13, "position": 1, "query": "测试车A", "model_name": "测试车A"},
                "subworkflow": {"child_task_id": "task_child"},
                "reused": False,
            }
        )
    )

    assert result["usable"] is True
    assert result["degraded"] is True
    assert "incomplete_source" in result["labels"]
    assert result["incomplete_sources"] == ["partial_collection"]
    assert result["snapshot"]["source_job_id"] == "task_child"


def test_fewer_than_two_usable_vehicles_marks_comparison_failed() -> None:
    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [{"child_task_id": "job_1"}, {"child_task_id": "job_2"}],
            "wait_for_vehicle_results": [
                usable_vehicle(1, "测试车A"),
                {"vehicle_id": 2, "model_name": "测试车B", "usable": False, "reason": "collection_failed"},
                {"vehicle_id": 3, "model_name": "测试车C", "usable": False, "reason": "missing_snapshot"},
            ],
            "publish_comparison_result": [{"status": "failed"}],
        }
    )

    result = run_workflow(runner)

    assert result == {
        "comparison_id": "cmp_1",
        "status": "failed",
        "error_code": "insufficient_available_vehicles",
        "error_message": "竞品对比至少需要 2 个可用车型结果",
        "available_vehicle_count": 1,
        "excluded": [
            {"model_name": "测试车B", "reason": "collection_failed"},
            {"model_name": "测试车C", "reason": "missing_snapshot"},
        ],
    }
    assert "generate_comparison_report" not in runner.call_names()
    failure_payload = runner.payloads("publish_comparison_result")[0]
    assert failure_payload["status"] == "failed"
    assert failure_payload["error_code"] == "insufficient_available_vehicles"


def test_comparison_eta_uses_slowest_vehicle_path_plus_summary_time() -> None:
    assert estimate_comparison_seconds([180, 30, 75], summary_seconds=45) == 225
    assert estimate_comparison_seconds([], summary_seconds=45) == 45
