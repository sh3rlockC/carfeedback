from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import get_engine, init_db, reset_engine_cache
from app.models import CollectionRun, CollectorEvent, EtaMetric, Task, TaskArtifact, TaskEvent, TaskVehicle


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'task-models.db'}",
        pass_phrase_hash="sha256:test",
        session_secret="test-secret",
        artifact_root=str(tmp_path / "artifacts"),
    )


def test_task_tables_are_created(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    reset_engine_cache()
    init_db(settings)

    inspector = inspect(get_engine(settings))
    assert {
        "tasks",
        "task_vehicles",
        "collection_runs",
        "task_events",
        "collector_events",
        "task_artifacts",
        "eta_metrics",
    } <= set(inspector.get_table_names())


def test_task_model_relationships_persist(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    reset_engine_cache()
    init_db(settings)
    session_local = __import__("app.db", fromlist=["get_session_local"]).get_session_local()

    with session_local() as session:
        task = Task(
            task_id="task_1",
            task_type="single",
            display_name="测试车",
            status="queued",
            current_stage="queued",
            view_token_hash="view-hash",
            manage_token_hash="manage-hash",
            manage_token_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(task)
        session.add(
            TaskVehicle(
                task_id="task_1",
                position=1,
                query="测试车",
                model_name="测试车",
                autohome_series_id="8089",
                dcd_series_id="25398",
            )
        )
        session.add(
            CollectionRun(
                run_id="run_1",
                platform="autohome",
                query_key="测试车",
                model_name="测试车",
                series_id="8089",
                status="queued",
                mode="incremental",
            )
        )
        session.add(TaskEvent(task_id="task_1", event_type="created", payload_json={"source": "test"}))
        session.add(CollectorEvent(run_id="run_1", platform="autohome", event_type="queued", payload_json={"queue_position": 1}))
        session.add(TaskArtifact(task_id="task_1", artifact_type="business_zip", path="/tmp/task.zip", downloadable=True))
        session.add(
            EtaMetric(
                stage="platform_collection",
                platform="autohome",
                task_type="single",
                sample_count=1,
                p50_seconds=600,
                p90_seconds=900,
            )
        )
        session.commit()

        loaded = session.get(Task, "task_1")
        assert loaded is not None
        assert loaded.vehicles[0].model_name == "测试车"
        assert loaded.events[0].event_type == "created"
        assert loaded.artifacts[0].downloadable is True
