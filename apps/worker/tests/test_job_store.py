from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.job_store import DatabaseJobStore, _db_bool
from worker_app.jobs import PipelineResult, StageResult


def test_db_bool_returns_native_bool_for_database_parameters() -> None:
    assert _db_bool(False) is False
    assert _db_bool(True) is True
    assert _db_bool(0) is False
    assert _db_bool(1) is True


def test_job_store_does_not_write_degraded_with_integer_literals() -> None:
    source = (ROOT / "worker_app" / "job_store.py").read_text(encoding="utf-8")

    assert re.search(r"\bdegraded\s*=\s*[01]\b", source) is None
    assert '"degraded": 0' not in source
    assert '"degraded": 1' not in source
    assert '"degraded": 1 if' not in source


def create_schema(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                model_name TEXT NOT NULL,
                status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                degraded INTEGER NOT NULL DEFAULT 0,
                passphrase_version TEXT NOT NULL,
                queue_job_id TEXT,
                collection_mode TEXT NOT NULL DEFAULT 'incremental',
                collection_summary TEXT NOT NULL DEFAULT '{}',
                created_at TEXT,
                enqueued_at TEXT,
                started_at TEXT,
                finished_at TEXT
            );
            CREATE TABLE job_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                series_id TEXT,
                url TEXT,
                title TEXT,
                source TEXT,
                selected INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE job_stage_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                stage_name TEXT NOT NULL,
                attempt_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                duration_ms INTEGER,
                error_code TEXT,
                error_message TEXT
            );
            CREATE TABLE job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                artifact_url TEXT,
                source_stage TEXT,
                created_at TEXT
            );
            CREATE TABLE job_time_reports (
                report_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                status TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                platform_counts TEXT,
                report_json TEXT,
                artifact_paths TEXT,
                source TEXT,
                error_code TEXT,
                error_message TEXT,
                queue_job_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                completed_at TEXT
            );
            """
        )
        connection.execute(
            """
            INSERT INTO jobs (job_id, query, model_name, status, current_stage, degraded, passphrase_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            ("job_store", "风云X3 PLUS", "风云X3 PLUS", "queued", "queued", 0, "2026-W17"),
        )
        connection.executemany(
            """
            INSERT INTO job_candidates (job_id, platform, series_id, selected)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("job_store", "autohome", "8089", 1),
                ("job_store", "dongchedi", "25398", 1),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_job_store_fetches_inputs_and_persists_stage_events(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    store = DatabaseJobStore(f"sqlite+pysqlite:///{db_path}")

    inputs = store.fetch_job_inputs("job_store")
    assert inputs.query == "风云X3 PLUS"
    assert inputs.model_name == "风云X3 PLUS"
    assert inputs.autohome_series_id == "8089"
    assert inputs.dongchedi_series_id == "25398"
    assert inputs.collection_mode == "incremental"

    artifact_path = tmp_path / "summary.xlsx"
    artifact_path.write_text("placeholder", encoding="utf-8")

    store.handle_pipeline_event(
        "job_store",
        {
            "type": "stage_running",
            "stage": "summarizing",
            "snapshot": {"status": "running", "degraded": False},
        },
    )
    store.handle_pipeline_event(
        "job_store",
        {
            "type": "stage_success",
            "stage": "summarizing",
            "snapshot": {"status": "success", "degraded": False},
            "result": StageResult(status="success", artifact_paths=[str(artifact_path)], output_metadata={}),
        },
    )
    store.handle_pipeline_event(
        "job_store",
        {
            "type": "pipeline_completed",
            "result": PipelineResult(status="completed", degraded=False, completed_stages=["summarizing"]),
        },
    )

    connection = sqlite3.connect(db_path)
    try:
        job_row = connection.execute(
            "SELECT status, current_stage, degraded, finished_at FROM jobs WHERE job_id = ?",
            ("job_store",),
        ).fetchone()
        assert job_row[0] == "completed"
        assert job_row[1] == "completed"
        assert job_row[2] == 0
        assert job_row[3] is not None

        stage_row = connection.execute(
            "SELECT stage_name, status, error_code FROM job_stage_runs WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            ("job_store",),
        ).fetchone()
        assert stage_row == ("summarizing", "success", None)

        artifact_row = connection.execute(
            "SELECT artifact_type, artifact_path, source_stage FROM job_artifacts WHERE job_id = ?",
            ("job_store",),
        ).fetchone()
        assert artifact_row == ("excel", str(artifact_path), "summarizing")
    finally:
        connection.close()


def test_degraded_stage_result_records_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    store = DatabaseJobStore(f"sqlite+pysqlite:///{db_path}")
    artifact_path = tmp_path / "final_report.json"
    artifact_path.write_text("{}", encoding="utf-8")

    store.handle_pipeline_event(
        "job_store",
        {
            "type": "stage_running",
            "stage": "generating_hermes_outputs",
            "snapshot": {"status": "running", "degraded": False},
            "command": ["python", "hermes_outputs.py"],
        },
    )
    store.handle_pipeline_event(
        "job_store",
        {
            "type": "stage_success",
            "stage": "generating_hermes_outputs",
            "snapshot": {"status": "degraded", "degraded": True},
            "result": StageResult(status="degraded", artifact_paths=[str(artifact_path)], output_metadata={}),
        },
    )

    connection = sqlite3.connect(db_path)
    try:
        artifact_row = connection.execute(
            "SELECT artifact_type, artifact_path, source_stage FROM job_artifacts WHERE job_id = ?",
            ("job_store",),
        ).fetchone()
    finally:
        connection.close()

    assert artifact_row == ("json", str(artifact_path), "generating_hermes_outputs")


def test_job_store_persists_time_report_lifecycle(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO job_time_reports (
                report_id, job_id, model_name, start_date, end_date, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            ("time_report_1", "job_store", "风云X3 PLUS", "2026-03-01", "2026-03-31", "queued"),
        )
        connection.commit()
    finally:
        connection.close()

    store = DatabaseJobStore(f"sqlite+pysqlite:///{db_path}")
    inputs = store.fetch_time_report_inputs("time_report_1")
    assert inputs.job_id == "job_store"
    assert inputs.model_name == "风云X3 PLUS"
    assert inputs.start_date == "2026-03-01"
    assert inputs.end_date == "2026-03-31"

    store.mark_time_report_running("time_report_1")
    store.mark_time_report_completed(
        "time_report_1",
        {
            "sample_count": 2,
            "platform_counts": {"汽车之家": 1, "懂车帝": 1},
            "report_json": {"headline": "时间范围报告"},
            "artifact_paths": [str(tmp_path / "final_report.json")],
            "source": "hermes-local-aggregate",
        },
    )

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            """
            SELECT status, sample_count, platform_counts, report_json, artifact_paths, source, completed_at
            FROM job_time_reports
            WHERE report_id = ?
            """,
            ("time_report_1",),
        ).fetchone()
    finally:
        connection.close()

    assert row[0] == "completed"
    assert row[1] == 2
    assert json.loads(row[2]) == {"汽车之家": 1, "懂车帝": 1}
    assert json.loads(row[3]) == {"headline": "时间范围报告"}
    assert json.loads(row[4]) == [str(tmp_path / "final_report.json")]
    assert row[5] == "hermes-local-aggregate"
    assert row[6] is not None


def test_job_store_marks_time_report_failed(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO job_time_reports (
                report_id, job_id, model_name, start_date, end_date, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            ("time_report_failed", "job_store", "风云X3 PLUS", "2026-04-01", "2026-04-30", "running"),
        )
        connection.commit()
    finally:
        connection.close()

    store = DatabaseJobStore(f"sqlite+pysqlite:///{db_path}")
    store.mark_time_report_failed("time_report_failed", error_code="no_comments", error_message="该时间范围无可分析评论")

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT status, error_code, error_message, completed_at FROM job_time_reports WHERE report_id = ?",
            ("time_report_failed",),
        ).fetchone()
    finally:
        connection.close()

    assert row == ("failed", "no_comments", "该时间范围无可分析评论", row[3])
    assert row[3] is not None
