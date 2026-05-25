from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.task_eta import (  # noqa: E402
    clear_eta_metrics,
    estimate_comparison_seconds,
    estimate_queue_seconds,
    eta_reason_for_platform,
    record_stage_duration,
)


def test_record_stage_duration_updates_p50_and_p90_from_observed_durations() -> None:
    clear_eta_metrics()

    observations = [10, 20, 30, 40, 100]
    metric = None
    for duration in observations:
        metric = record_stage_duration("platform_collection", "autohome", "single_vehicle", duration)

    assert metric is not None
    assert metric.sample_count == 5
    assert metric.p50_seconds == 30
    assert metric.p90_seconds == 100


def test_record_stage_duration_upserts_eta_metric_row_readable_by_new_session(tmp_path: Path) -> None:
    clear_eta_metrics()
    db_path = tmp_path / "eta.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE eta_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                platform TEXT,
                task_type TEXT,
                sample_count INTEGER NOT NULL DEFAULT 0,
                p50_seconds INTEGER NOT NULL,
                p90_seconds INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (stage, platform, task_type)
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    database_url = f"sqlite+pysqlite:///{db_path}"
    record_stage_duration("platform_collection", "autohome", "single_vehicle", 10, database_url=database_url)
    metric = record_stage_duration(
        "platform_collection",
        "autohome",
        "single_vehicle",
        100,
        database_url=database_url,
    )

    new_connection = sqlite3.connect(db_path)
    try:
        rows = new_connection.execute(
            """
            SELECT stage, platform, task_type, sample_count, p50_seconds, p90_seconds, updated_at
            FROM eta_metrics
            """
        ).fetchall()
    finally:
        new_connection.close()

    assert metric.sample_count == 2
    assert len(rows) == 1
    assert rows[0][:6] == ("platform_collection", "autohome", "single_vehicle", 2, 10, 100)
    assert rows[0][6]


def test_record_stage_duration_upserts_nullable_metric_identity(tmp_path: Path) -> None:
    clear_eta_metrics()
    db_path = tmp_path / "eta-nullable.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE eta_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                platform TEXT,
                task_type TEXT,
                sample_count INTEGER NOT NULL DEFAULT 0,
                p50_seconds INTEGER NOT NULL,
                p90_seconds INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (stage, platform, task_type)
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    database_url = f"sqlite+pysqlite:///{db_path}"
    record_stage_duration("summary", None, None, 60, database_url=database_url)
    record_stage_duration("summary", None, None, 120, database_url=database_url)

    new_connection = sqlite3.connect(db_path)
    try:
        rows = new_connection.execute(
            """
            SELECT stage, platform, task_type, sample_count, p50_seconds, p90_seconds
            FROM eta_metrics
            """
        ).fetchall()
    finally:
        new_connection.close()

    assert rows == [("summary", None, None, 2, 60, 120)]


def test_queue_eta_uses_queue_position_and_platform_agent_count() -> None:
    assert estimate_queue_seconds(position=3, agent_count=2, p50_seconds=60) == 120
    assert estimate_queue_seconds(position=0, agent_count=2, p50_seconds=60) == 0
    assert estimate_queue_seconds(position=3, agent_count=0, p50_seconds=60) == 180


def test_comparison_eta_uses_max_vehicle_path_not_sum() -> None:
    assert estimate_comparison_seconds([180, 30, 75], summary_seconds=45) == 225
    assert estimate_comparison_seconds([], summary_seconds=45) == 45


def test_eta_reason_changes_when_platform_queue_is_congested() -> None:
    normal = eta_reason_for_platform("autohome", queue_length=1, agent_count=2, retrying_count=0)
    congested = eta_reason_for_platform("autohome", queue_length=2, agent_count=2, retrying_count=0)

    assert normal != congested
    assert "normal" in normal
    assert "congested" in congested
    assert "autohome" in congested
