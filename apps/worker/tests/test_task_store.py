from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.task_store import TaskStore


def create_schema(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                degraded INTEGER NOT NULL DEFAULT 0,
                upgraded_to_full INTEGER NOT NULL DEFAULT 0,
                view_token_hash TEXT NOT NULL,
                manage_token_hash TEXT NOT NULL,
                manage_token_expires_at TEXT NOT NULL,
                view_token_revoked_at TEXT,
                manage_token_revoked_at TEXT,
                eta_seconds INTEGER,
                eta_reason TEXT,
                collection_mode TEXT NOT NULL DEFAULT 'incremental',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE task_vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                query TEXT NOT NULL,
                model_name TEXT NOT NULL,
                autohome_series_id TEXT,
                dcd_series_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                result_snapshot_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );
            CREATE TABLE collection_runs (
                run_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                query_key TEXT NOT NULL,
                model_name TEXT NOT NULL,
                series_id TEXT NOT NULL,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                shared_by_task_ids TEXT NOT NULL DEFAULT '[]',
                agent_id TEXT,
                failure_category TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                resume_cursor TEXT NOT NULL DEFAULT '{}',
                output_path TEXT,
                started_at TEXT,
                finished_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX uq_collection_run_active_identity
                ON collection_runs (platform, query_key, series_id)
                WHERE status IN ('queued', 'waiting_agent', 'running', 'retry_wait');
            CREATE TABLE collection_run_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_vehicle_id INTEGER,
                created_at TEXT NOT NULL,
                UNIQUE (run_id, task_id),
                FOREIGN KEY (run_id) REFERENCES collection_runs(run_id) ON DELETE CASCADE,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                FOREIGN KEY (task_vehicle_id) REFERENCES task_vehicles(id) ON DELETE CASCADE
            );
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );
            CREATE TABLE task_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                path TEXT NOT NULL,
                downloadable INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );
            CREATE TABLE collector_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES collection_runs(run_id) ON DELETE CASCADE
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def seed_task(db_path: Path, task_id: str = "task_1", collection_mode: str = "incremental") -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, task_type, display_name, status, current_stage,
                view_token_hash, manage_token_hash, manage_token_expires_at,
                collection_mode, created_at, updated_at
            )
            VALUES (?, 'single_vehicle', ?, 'queued', 'queued', 'view', 'manage', '2030-01-01T00:00:00+00:00', ?, datetime('now'), datetime('now'))
            """,
            (task_id, f"Task {task_id}", collection_mode),
        )
        connection.commit()
    finally:
        connection.close()


def seed_vehicle(db_path: Path, *, task_id: str, position: int, query: str, model_name: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO task_vehicles (
                task_id, position, query, model_name, autohome_series_id, dcd_series_id,
                status, result_snapshot_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, '8089', '25398', 'queued', ?, datetime('now'), datetime('now'))
            """,
            (task_id, position, query, model_name, json.dumps({"position": position})),
        )
        connection.commit()
    finally:
        connection.close()


def test_load_task_returns_task_with_sorted_vehicles(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, collection_mode="full")
    seed_vehicle(db_path, task_id="task_1", position=2, query="测试车 B", model_name="测试车 B")
    seed_vehicle(db_path, task_id="task_1", position=1, query="测试车 A", model_name="测试车 A")

    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    task = store.load_task("task_1")

    assert task.task_id == "task_1"
    assert task.task_type == "single_vehicle"
    assert task.display_name == "Task task_1"
    assert task.status == "queued"
    assert task.current_stage == "queued"
    assert task.collection_mode == "full"
    assert [vehicle.position for vehicle in task.vehicles] == [1, 2]
    assert task.vehicles[0].query == "测试车 A"
    assert task.vehicles[0].result_snapshot_json == {"position": 1}


def test_load_task_raises_for_missing_task(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")

    try:
        store.load_task("missing")
    except RuntimeError as exc:
        assert "task not found: missing" in str(exc)
    else:
        raise AssertionError("expected missing task to raise")


def test_mark_task_stage_and_append_task_event(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path)
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")

    store.mark_task_stage("task_1", "collecting", "running")
    store.append_task_event("task_1", "stage_started", {"stage": "collecting"})

    connection = sqlite3.connect(db_path)
    try:
        task_row = connection.execute(
            "SELECT current_stage, status, updated_at FROM tasks WHERE task_id = ?",
            ("task_1",),
        ).fetchone()
        event_row = connection.execute(
            "SELECT event_type, payload_json, created_at FROM task_events WHERE task_id = ?",
            ("task_1",),
        ).fetchone()
    finally:
        connection.close()

    assert task_row[0] == "collecting"
    assert task_row[1] == "running"
    assert task_row[2] is not None
    assert event_row[0] == "stage_started"
    assert json.loads(event_row[1]) == {"stage": "collecting"}
    assert event_row[2] is not None


def test_create_or_join_collection_run_shares_active_run(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_task(db_path, "task_2")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")

    first = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )
    second = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_2",
    )

    assert first.run_id == second.run_id
    assert second.status == "queued"
    assert set(second.shared_by_task_ids) == {"task_1", "task_2"}

    connection = sqlite3.connect(db_path)
    try:
        link_count = connection.execute(
            "SELECT count(*) FROM collection_run_tasks WHERE run_id = ?",
            (first.run_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert link_count == 2


def test_create_or_join_collection_run_ignores_completed_historical_run(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO collection_runs (
                run_id, platform, query_key, model_name, series_id, status, mode,
                shared_by_task_ids, retry_count, resume_cursor, created_at, updated_at
            )
            VALUES ('run_old', 'autohome', '测试车', '测试车', '8089', 'completed', 'incremental', '["task_old"]', 0, '{}', datetime('now'), datetime('now'))
            """
        )
        connection.commit()
    finally:
        connection.close()
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")

    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )

    assert run.run_id != "run_old"
    assert run.shared_by_task_ids == ["task_1"]


def test_create_or_join_collection_run_retries_insert_conflict_and_joins_winner(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_task(db_path, "task_2")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    calls = 0

    def insert_conflicting_winner_once(conn, *, platform, query_key, model_name, series_id, mode, task_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raw = sqlite3.connect(db_path)
            try:
                raw.execute(
                    """
                    INSERT INTO collection_runs (
                        run_id, platform, query_key, model_name, series_id, status, mode,
                        shared_by_task_ids, retry_count, resume_cursor, created_at, updated_at
                    )
                    VALUES ('run_winner', ?, ?, ?, ?, 'queued', ?, ?, 0, '{}', datetime('now'), datetime('now'))
                    """,
                    (platform, query_key, model_name, series_id, mode, json.dumps(["task_1"])),
                )
                raw.execute(
                    """
                    INSERT INTO collection_run_tasks (run_id, task_id, task_vehicle_id, created_at)
                    VALUES ('run_winner', 'task_1', NULL, datetime('now'))
                    """
                )
                raw.commit()
            finally:
                raw.close()
            raise IntegrityError("active identity conflict", {}, Exception("unique constraint"))
        raise AssertionError("retry should join the winner instead of inserting again")

    monkeypatch.setattr(store, "_insert_collection_run", insert_conflicting_winner_once)

    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_2",
    )

    assert calls == 1
    assert run.run_id == "run_winner"
    assert run.shared_by_task_ids == ["task_1", "task_2"]


def test_attach_task_to_run_is_idempotent_preserves_shared_tasks_and_records_collector_events(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_task(db_path, "task_2")
    seed_task(db_path, "task_3")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )

    updated = store.attach_task_to_run("task_2", run.run_id)
    with_third = store.attach_task_to_run("task_3", run.run_id)
    repeated = store.attach_task_to_run("task_2", run.run_id)
    store.record_collector_event(run.run_id, "collector_started", {"agent_id": "agent-1"})

    assert updated.shared_by_task_ids == ["task_1", "task_2"]
    assert with_third.shared_by_task_ids == ["task_1", "task_2", "task_3"]
    assert repeated.shared_by_task_ids == ["task_1", "task_2", "task_3"]

    connection = sqlite3.connect(db_path)
    try:
        link_count = connection.execute(
            "SELECT count(*) FROM collection_run_tasks WHERE run_id = ? AND task_id IN ('task_2', 'task_3')",
            (run.run_id,),
        ).fetchone()[0]
        collector_row = connection.execute(
            "SELECT platform, event_type, payload_json FROM collector_events WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
    finally:
        connection.close()

    assert link_count == 2
    assert collector_row[0] == "autohome"
    assert collector_row[1] == "collector_started"
    assert json.loads(collector_row[2]) == {"agent_id": "agent-1"}


def test_attach_task_to_run_loads_run_with_lock_hook(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_task(db_path, "task_2")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )
    original = store._get_collection_run_row_for_update
    calls = []

    def tracking_locked_load(conn, run_id):
        calls.append(run_id)
        return original(conn, run_id)

    store._get_collection_run_row_for_update = tracking_locked_load

    store.attach_task_to_run("task_2", run.run_id)

    assert calls == [run.run_id]


def test_publish_full_result_persists_comparison_snapshot_and_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_vehicle(db_path, task_id="task_1", position=1, query="测试车", model_name="测试车")
    final_report = tmp_path / "final_report.json"
    analysis_facts = tmp_path / "analysis_facts.jsonl"
    final_report.write_text('{"headline":"ok"}', encoding="utf-8")
    analysis_facts.write_text('{"comment_id":"fallback"}\n', encoding="utf-8")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")

    store.publish_full_result(
        "task_1",
        {
            "task": {"vehicles": [{"model_name": "测试车", "query": "测试车"}]},
            "postprocess_result": {"artifact_paths": [str(analysis_facts)]},
            "report_result": {"artifact_paths": [str(final_report)]},
        },
    )

    connection = sqlite3.connect(db_path)
    try:
        artifact_rows = connection.execute(
            "SELECT artifact_type, path FROM task_artifacts WHERE task_id = ? ORDER BY id ASC",
            ("task_1",),
        ).fetchall()
        snapshot_json = connection.execute(
            "SELECT result_snapshot_json FROM task_vehicles WHERE task_id = ?",
            ("task_1",),
        ).fetchone()[0]
    finally:
        connection.close()

    snapshot = json.loads(snapshot_json)["comparison_snapshot"]
    assert artifact_rows == [
        ("jsonl", str(analysis_facts)),
        ("json", str(final_report)),
    ]
    assert snapshot["model_name"] == "测试车"
    assert snapshot["source_job_id"] == "task_1"
    assert snapshot["final_report_path"] == str(final_report)
    assert snapshot["analysis_facts_path"] == str(analysis_facts)


def test_publish_degraded_result_persists_comparison_snapshot_and_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_vehicle(db_path, task_id="task_1", position=1, query="测试车", model_name="测试车")
    final_report = tmp_path / "final_report.json"
    analysis_facts = tmp_path / "analysis_facts.jsonl"
    final_report.write_text('{"headline":"degraded"}', encoding="utf-8")
    analysis_facts.write_text('{"comment_id":"fallback","fallback":true}\n', encoding="utf-8")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")

    store.publish_degraded_result(
        "task_1",
        {
            "task": {"vehicles": [{"model_name": "测试车", "query": "测试车"}]},
            "postprocess_result": {"artifact_paths": [str(analysis_facts)]},
            "report_result": {"artifact_paths": [str(final_report)]},
        },
    )

    connection = sqlite3.connect(db_path)
    try:
        task_row = connection.execute(
            "SELECT status, degraded, upgraded_to_full FROM tasks WHERE task_id = ?",
            ("task_1",),
        ).fetchone()
        artifact_rows = connection.execute(
            "SELECT artifact_type, path FROM task_artifacts WHERE task_id = ? ORDER BY id ASC",
            ("task_1",),
        ).fetchall()
        snapshot_json = connection.execute(
            "SELECT result_snapshot_json FROM task_vehicles WHERE task_id = ?",
            ("task_1",),
        ).fetchone()[0]
    finally:
        connection.close()

    snapshot = json.loads(snapshot_json)["comparison_snapshot"]
    assert task_row == ("completed_degraded", 1, 0)
    assert artifact_rows == [
        ("jsonl", str(analysis_facts)),
        ("json", str(final_report)),
    ]
    assert snapshot["source_job_id"] == "task_1"
    assert snapshot["final_report_path"] == str(final_report)
    assert snapshot["analysis_facts_path"] == str(analysis_facts)
