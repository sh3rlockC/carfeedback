from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Task, TaskArtifact, TaskVehicle
from scripts.task_reports.backfill_task_reports import find_backfill_candidates


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'backfill.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    return session_local()


def _add_task(session, task_id: str, *, artifact_paths: list[str] | None = None) -> None:
    task = Task(
        task_id=task_id,
        task_type="single",
        display_name=task_id,
        status="completed",
        current_stage="completed",
        view_token_hash="view",
        manage_token_hash="manage",
        manage_token_expires_at=datetime.now(UTC) + timedelta(days=7),
        completed_at=datetime.now(UTC),
    )
    task.vehicles.append(TaskVehicle(position=1, query=task_id, model_name=task_id))
    session.add(task)
    for artifact_path in artifact_paths or []:
        lowered = artifact_path.lower()
        artifact_type = "pdf" if lowered.endswith(".pdf") else "excel" if lowered.endswith(".xlsx") else "json"
        session.add(TaskArtifact(task_id=task_id, artifact_type=artifact_type, path=artifact_path, downloadable=True))
    session.commit()


def test_find_backfill_candidates_returns_completed_tasks_missing_full_report(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        _add_task(session, "task_missing")
        _add_task(
            session,
            "task_ready",
            artifact_paths=[
                str(tmp_path / "task_ready_完整报告.pdf"),
                str(tmp_path / "task_ready_双平台口碑摘要.xlsx"),
                str(tmp_path / "final_report.json"),
            ],
        )

        candidates = find_backfill_candidates(session)

        assert [candidate.task_id for candidate in candidates] == ["task_missing"]
        assert candidates[0].display_name == "task_missing"
    finally:
        session.close()
