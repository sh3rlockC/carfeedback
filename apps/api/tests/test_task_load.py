from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import get_session_local, init_db, reset_engine_cache
from app.models import CollectionRun, Task
from app.services.task_load import project_task_load


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'task-load.db'}",
        pass_phrase_hash="sha256:test",
        session_secret="test-secret",
        artifact_root=str(tmp_path / "artifacts"),
    )


def _task(task_id: str, status: str) -> Task:
    now = datetime.now(UTC)
    return Task(
        task_id=task_id,
        task_type="single_vehicle",
        display_name=task_id,
        status=status,
        current_stage=status,
        view_token_hash=f"view-{task_id}",
        manage_token_hash=f"manage-{task_id}",
        manage_token_expires_at=now + timedelta(days=7),
    )


def _run(run_id: str, platform: str, status: str, agent_id: str | None = None) -> CollectionRun:
    return CollectionRun(
        run_id=run_id,
        platform=platform,
        query_key=run_id,
        model_name=run_id,
        series_id=run_id,
        status=status,
        mode="incremental",
        agent_id=agent_id,
    )


def test_project_task_load_counts_tasks_and_platform_agent_availability(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    reset_engine_cache()
    init_db(settings)
    session_local = get_session_local()

    with session_local() as session:
        session.add_all(
            [
                _task("task_running_1", "running"),
                _task("task_running_2", "running"),
                _task("task_queued_1", "queued"),
                _task("task_queued_2", "queued"),
                _task("task_waiting", "waiting_agent"),
                _task("task_retry", "retry_wait"),
                _task("task_done", "completed"),
                _run("run_auto_busy", "autohome", "running", "autohome_1"),
                _run("run_auto_queued", "autohome", "queued"),
                _run("run_dcd_busy", "dongchedi", "running", "dongchedi_1"),
                _run("run_dcd_retry", "dongchedi", "retry_wait"),
                _run("run_finished", "autohome", "completed", "autohome_2"),
            ]
        )
        session.commit()

        payload = project_task_load(
            session,
            platform_agent_ids={
                "autohome": ["autohome_1", "autohome_2"],
                "dongchedi": ["dongchedi_1"],
            },
        )

    assert payload == {
        "running_task_count": 2,
        "queued_task_count": 4,
        "platforms": {
            "autohome": {"available": 1, "total": 2},
            "dongchedi": {"available": 0, "total": 1},
        },
    }
