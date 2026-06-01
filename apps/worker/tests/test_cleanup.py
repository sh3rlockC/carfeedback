from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.cleanup import cleanup_expired_job_data, cleanup_settings_from_env, start_cleanup_process
import worker as worker_module


def create_cleanup_schema(db_path: Path) -> None:
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
            CREATE TABLE job_ai_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                report_version TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE job_qa_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                text TEXT NOT NULL,
                tags TEXT NOT NULL,
                metadata_json TEXT NOT NULL
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
            CREATE TABLE koubei_raw_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_key TEXT NOT NULL,
                query TEXT NOT NULL,
                model_name TEXT NOT NULL,
                platform TEXT NOT NULL,
                series_id TEXT NOT NULL,
                source_link TEXT,
                dedupe_key TEXT NOT NULL,
                row_json TEXT NOT NULL,
                first_seen_job_id TEXT,
                last_seen_job_id TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                published_at TEXT,
                page INTEGER
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def as_db_datetime(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def insert_job(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    status: str,
    created_at: datetime | str,
    finished_at: datetime | str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO jobs (
            job_id, query, model_name, status, current_stage, degraded, passphrase_version, created_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            "风云X3 PLUS",
            "风云X3 PLUS",
            status,
            status,
            0,
            "2026-W17",
            as_db_datetime(created_at),
            as_db_datetime(finished_at),
        ),
    )
    connection.execute(
        "INSERT INTO job_candidates (job_id, platform, series_id, selected) VALUES (?, ?, ?, ?)",
        (job_id, "autohome", "8089", 1),
    )
    connection.execute(
        "INSERT INTO job_stage_runs (job_id, stage_name, attempt_no, status, started_at) VALUES (?, ?, ?, ?, ?)",
        (job_id, "completed", 1, "success", as_db_datetime(created_at)),
    )
    connection.execute(
        "INSERT INTO job_artifacts (job_id, artifact_type, artifact_path, source_stage, created_at) VALUES (?, ?, ?, ?, ?)",
        (job_id, "excel", f"/tmp/{job_id}.xlsx", "summarizing", as_db_datetime(created_at)),
    )
    connection.execute(
        "INSERT INTO job_ai_reports (job_id, report_version, report_json, created_at) VALUES (?, ?, ?, ?)",
        (job_id, "v1", "{}", as_db_datetime(created_at)),
    )
    connection.execute(
        "INSERT INTO job_qa_chunks (job_id, chunk_id, source_type, text, tags, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, "chunk_1", "summary", "comment text", "[]", "{}"),
    )
    connection.execute(
        """
        INSERT INTO job_time_reports (
            report_id, job_id, model_name, start_date, end_date, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (f"time_{job_id}", job_id, "风云X3 PLUS", "2026-03-01", "2026-03-31", "completed", as_db_datetime(created_at), as_db_datetime(created_at)),
    )


def test_cleanup_removes_expired_job_data_but_keeps_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "cleanup.db"
    artifact_root = tmp_path / "jobs"
    create_cleanup_schema(db_path)
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)

    old_job_dir = artifact_root / "job_old"
    old_job_dir.mkdir(parents=True)
    (old_job_dir / "raw.xlsx").write_text("old comments", encoding="utf-8")
    recent_job_dir = artifact_root / "job_recent"
    recent_job_dir.mkdir(parents=True)
    (recent_job_dir / "raw.xlsx").write_text("recent comments", encoding="utf-8")
    running_job_dir = artifact_root / "job_running"
    running_job_dir.mkdir(parents=True)
    (running_job_dir / "raw.xlsx").write_text("running comments", encoding="utf-8")
    failed_recent_job_dir = artifact_root / "job_failed_recent"
    failed_recent_job_dir.mkdir(parents=True)
    (failed_recent_job_dir / "raw.xlsx").write_text("recent failed comments", encoding="utf-8")

    connection = sqlite3.connect(db_path)
    try:
        insert_job(
            connection,
            job_id="job_old",
            status="completed",
            created_at=now - timedelta(days=5),
            finished_at=now - timedelta(days=4),
        )
        connection.execute(
            """
            INSERT INTO koubei_raw_comments (
                query_key, query, model_name, platform, series_id, source_link, dedupe_key,
                row_json, first_seen_job_id, last_seen_job_id, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "风云x3 plus",
                "风云X3 PLUS",
                "风云X3 PLUS",
                "autohome",
                "8089",
                "https://k.autohome.com.cn/detail/view_01old.html",
                "link:https://k.autohome.com.cn/detail/view_01old.html",
                '{"用户名":"tester"}',
                "job_old",
                "job_old",
                as_db_datetime(now - timedelta(days=5)),
                as_db_datetime(now - timedelta(days=5)),
            ),
        )
        insert_job(
            connection,
            job_id="job_recent",
            status="completed",
            created_at=now - timedelta(days=2),
            finished_at=now - timedelta(days=1),
        )
        insert_job(
            connection,
            job_id="job_running",
            status="collecting_autohome",
            created_at=now - timedelta(days=10),
            finished_at=None,
        )
        insert_job(
            connection,
            job_id="job_failed_recent",
            status="failed",
            created_at=(now - timedelta(days=2, hours=13)).strftime("%Y-%m-%d %H:%M:%S.%f"),
            finished_at=None,
        )
        connection.commit()
    finally:
        connection.close()

    result = cleanup_expired_job_data(
        database_url=f"sqlite+pysqlite:///{db_path}",
        artifact_root=artifact_root,
        retention_days=3,
        now=now,
    )

    assert result.expired_job_ids == ["job_old"]
    assert not old_job_dir.exists()
    assert recent_job_dir.exists()
    assert running_job_dir.exists()
    assert failed_recent_job_dir.exists()

    connection = sqlite3.connect(db_path)
    try:
        old_status = connection.execute(
            "SELECT status, current_stage FROM jobs WHERE job_id = ?",
            ("job_old",),
        ).fetchone()
        assert old_status == ("expired", "expired")
        assert connection.execute("SELECT COUNT(*) FROM job_candidates WHERE job_id = ?", ("job_old",)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM job_stage_runs WHERE job_id = ?", ("job_old",)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM job_artifacts WHERE job_id = ?", ("job_old",)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM job_ai_reports WHERE job_id = ?", ("job_old",)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM job_qa_chunks WHERE job_id = ?", ("job_old",)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM job_time_reports WHERE job_id = ?", ("job_old",)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM koubei_raw_comments WHERE query_key = ?", ("风云x3 plus",)).fetchone()[0] == 1
        assert connection.execute("SELECT status FROM jobs WHERE job_id = ?", ("job_recent",)).fetchone()[0] == "completed"
        assert connection.execute("SELECT status FROM jobs WHERE job_id = ?", ("job_running",)).fetchone()[0] == "collecting_autohome"
        assert connection.execute("SELECT status FROM jobs WHERE job_id = ?", ("job_failed_recent",)).fetchone()[0] == "failed"
    finally:
        connection.close()


def test_cleanup_skips_time_reports_table_when_schema_is_not_migrated(tmp_path: Path) -> None:
    db_path = tmp_path / "cleanup.db"
    artifact_root = tmp_path / "jobs"
    create_cleanup_schema(db_path)
    now = datetime(2026, 5, 6, 12, 0, tzinfo=UTC)

    old_job_dir = artifact_root / "job_old"
    old_job_dir.mkdir(parents=True)
    (old_job_dir / "raw.xlsx").write_text("old comments", encoding="utf-8")

    connection = sqlite3.connect(db_path)
    try:
        insert_job(
            connection,
            job_id="job_old",
            status="completed",
            created_at=now - timedelta(days=5),
            finished_at=now - timedelta(days=4),
        )
        connection.execute("DROP TABLE job_time_reports")
        connection.commit()
    finally:
        connection.close()

    result = cleanup_expired_job_data(
        database_url=f"sqlite+pysqlite:///{db_path}",
        artifact_root=artifact_root,
        retention_days=3,
        now=now,
    )

    assert result.expired_job_ids == ["job_old"]
    assert not old_job_dir.exists()
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT status, current_stage FROM jobs WHERE job_id = ?", ("job_old",)).fetchone() == (
            "expired",
            "expired",
        )
        assert connection.execute("SELECT COUNT(*) FROM job_artifacts WHERE job_id = ?", ("job_old",)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM job_ai_reports WHERE job_id = ?", ("job_old",)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM job_qa_chunks WHERE job_id = ?", ("job_old",)).fetchone()[0] == 0
    finally:
        connection.close()


def test_cleanup_settings_default_to_three_days_and_twelve_hours(monkeypatch) -> None:
    monkeypatch.delenv("JOB_ARTIFACT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("JOB_ARTIFACT_CLEANUP_INTERVAL_SECONDS", raising=False)

    settings = cleanup_settings_from_env()

    assert settings.retention_days == 3
    assert settings.interval_seconds == 12 * 60 * 60


def test_start_cleanup_process_uses_daemon_process(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, *, target, kwargs, name: str, daemon: bool):
            captured["target"] = target
            captured["kwargs"] = kwargs
            captured["name"] = name
            captured["daemon"] = daemon
            captured["started"] = False

        def start(self):
            captured["started"] = True

    monkeypatch.setattr("worker_app.cleanup.multiprocessing.Process", FakeProcess)
    settings = cleanup_settings_from_env(
        {
            "JOB_ARTIFACT_RETENTION_DAYS": "3",
            "JOB_ARTIFACT_CLEANUP_INTERVAL_SECONDS": "43200",
        }
    )

    process = start_cleanup_process(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'cleanup.db'}",
        artifact_root=tmp_path / "jobs",
        settings=settings,
    )

    assert process is not None
    assert captured["name"] == "job-artifact-cleanup"
    assert captured["daemon"] is True
    assert captured["started"] is True
    assert captured["kwargs"] == {
        "database_url": f"sqlite+pysqlite:///{tmp_path / 'cleanup.db'}",
        "artifact_root": tmp_path / "jobs",
        "settings": settings,
    }


def test_worker_main_starts_cleanup_process_with_environment_settings(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeWorker:
        def __init__(self, queues, connection):
            captured["queues"] = queues
            captured["connection"] = connection

        def work(self, *, with_scheduler: bool):
            captured["with_scheduler"] = with_scheduler

    def fake_start_cleanup_process(*, database_url: str, artifact_root: str, settings):
        captured["database_url"] = database_url
        captured["artifact_root"] = artifact_root
        captured["retention_days"] = settings.retention_days
        captured["interval_seconds"] = settings.interval_seconds

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/9")
    monkeypatch.setenv("WORKER_QUEUE_NAME", "cleanup-test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'worker.db'}")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setenv("JOB_ARTIFACT_RETENTION_DAYS", "5")
    monkeypatch.setenv("JOB_ARTIFACT_CLEANUP_INTERVAL_SECONDS", "60")
    monkeypatch.setattr(worker_module, "make_redis_connection", lambda redis_url: f"connection:{redis_url}")
    monkeypatch.setattr(worker_module, "start_cleanup_process", fake_start_cleanup_process)
    monkeypatch.setattr(worker_module, "Worker", FakeWorker)

    assert worker_module.main() == 0
    assert captured["database_url"] == f"sqlite+pysqlite:///{tmp_path / 'worker.db'}"
    assert captured["artifact_root"] == str(tmp_path / "jobs")
    assert captured["retention_days"] == 5
    assert captured["interval_seconds"] == 60
    assert captured["queues"] == ["cleanup-test"]
    assert captured["connection"] == "connection:redis://127.0.0.1:6379/9"
    assert captured["with_scheduler"] is False
