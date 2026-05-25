from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import get_session_local, init_db, reset_engine_cache
from app.models import Task, TaskArtifact, TaskEvent, TaskVehicle
from app.services.task_center import task_detail_payload, task_list_payload


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'task-center.db'}",
        pass_phrase_hash="sha256:test",
        session_secret="test-secret",
        artifact_root=str(tmp_path / "artifacts"),
    )


def test_task_list_payload_projects_task_summary(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    reset_engine_cache()
    init_db(settings)
    session_local = get_session_local()

    created_at = datetime(2026, 1, 1, 10, 0)
    completed_at = datetime(2026, 1, 1, 10, 10)
    with session_local() as session:
        session.add(
            Task(
                task_id="task_1",
                task_type="single",
                display_name="测试车",
                status="running",
                current_stage="collecting_autohome",
                degraded=False,
                upgraded_to_full=True,
                view_token_hash="view-hash",
                manage_token_hash="manage-hash",
                manage_token_expires_at=created_at + timedelta(days=7),
                eta_seconds=600,
                eta_reason="historical_p50",
                created_at=created_at,
                completed_at=completed_at,
            )
        )
        session.commit()

        loaded = session.get(Task, "task_1")
        assert loaded is not None

        payload = task_list_payload([loaded])

    item = payload[0]
    assert item["task_id"] == "task_1"
    assert item["task_type"] == "single"
    assert item["display_name"] == "测试车"
    assert item["eta_seconds"] == 600
    assert item["created_at"] == created_at.isoformat()
    assert item["completed_at"] == completed_at.isoformat()
    assert "view_token_hash" not in item
    assert "manage_token_hash" not in item


def test_task_detail_payload_projects_sorted_children_without_token_hashes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    reset_engine_cache()
    init_db(settings)
    session_local = get_session_local()

    created_at = datetime(2026, 1, 1, 10, 0)
    first_event_at = datetime(2026, 1, 1, 10, 1)
    second_event_at = datetime(2026, 1, 1, 10, 2)
    first_artifact_at = datetime(2026, 1, 1, 10, 3)
    second_artifact_at = datetime(2026, 1, 1, 10, 4)

    with session_local() as session:
        task = Task(
            task_id="task_1",
            task_type="single",
            display_name="测试车",
            status="running",
            current_stage="collecting_autohome",
            view_token_hash="view-hash",
            manage_token_hash="manage-hash",
            manage_token_expires_at=created_at + timedelta(days=7),
            view_token_revoked_at=created_at + timedelta(hours=1),
            manage_token_revoked_at=created_at + timedelta(hours=2),
            eta_seconds=600,
            eta_reason="historical_p50",
            created_at=created_at,
        )
        session.add(task)
        session.add_all(
            [
                TaskVehicle(
                    task_id="task_1",
                    position=2,
                    query="第二辆",
                    model_name="第二辆",
                    autohome_series_id="222",
                    dcd_series_id="333",
                    status="queued",
                ),
                TaskVehicle(
                    task_id="task_1",
                    position=1,
                    query="测试车",
                    model_name="测试车",
                    autohome_series_id="8089",
                    dcd_series_id="25398",
                    status="completed",
                ),
                TaskEvent(
                    task_id="task_1",
                    event_type="second",
                    payload_json={"step": 2},
                    created_at=second_event_at,
                ),
                TaskEvent(
                    task_id="task_1",
                    event_type="first",
                    payload_json={"step": 1},
                    created_at=first_event_at,
                ),
                TaskArtifact(
                    task_id="task_1",
                    artifact_type="latest_zip",
                    path="/tmp/latest.zip",
                    downloadable=False,
                    created_at=second_artifact_at,
                ),
                TaskArtifact(
                    task_id="task_1",
                    artifact_type="business_zip",
                    path="/tmp/task.zip",
                    downloadable=True,
                    created_at=first_artifact_at,
                ),
            ]
        )
        session.commit()

        loaded = session.get(Task, "task_1")
        assert loaded is not None

        detail = task_detail_payload(loaded)

    assert detail["task_id"] == "task_1"
    assert detail["task_type"] == "single"
    assert detail["display_name"] == "测试车"
    assert detail["eta_seconds"] == 600
    assert detail["vehicles"][0]["model_name"] == "测试车"
    assert [vehicle["position"] for vehicle in detail["vehicles"]] == [1, 2]
    assert [event["event_type"] for event in detail["events"]] == ["first", "second"]
    assert detail["events"][0]["payload"] == {"step": 1}
    assert [artifact["artifact_type"] for artifact in detail["artifacts"]] == ["business_zip", "latest_zip"]
    assert detail["artifacts"][0]["downloadable"] is True
    assert detail["events"][0]["created_at"] == first_event_at.isoformat()
    assert detail["artifacts"][0]["created_at"] == first_artifact_at.isoformat()

    assert "view_token_hash" not in detail
    assert "manage_token_hash" not in detail
    assert "view_token_revoked_at" not in detail
    assert "manage_token_revoked_at" not in detail
