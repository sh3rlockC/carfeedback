from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
WORKER_ROOT = ROOT / "apps" / "worker"
for root in (API_ROOT, WORKER_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Task, TaskArtifact


@dataclass(frozen=True)
class BackfillCandidate:
    task_id: str
    display_name: str


def _has_full_report_artifact(task: Task) -> bool:
    paths = [artifact.path.lower() for artifact in task.artifacts]
    has_pdf = any(path.endswith(".pdf") for path in paths)
    has_summary = any(path.endswith("_双平台口碑摘要.xlsx") for path in paths)
    has_final_report = any(path.endswith("final_report.json") for path in paths)
    return has_pdf and has_summary and has_final_report


def find_backfill_candidates(session: Session) -> list[BackfillCandidate]:
    tasks = (
        session.query(Task)
        .filter(Task.task_type.in_(["single", "single_vehicle"]))
        .filter(Task.status.in_(["completed", "completed_degraded"]))
        .order_by(Task.created_at.asc(), Task.task_id.asc())
        .all()
    )
    return [
        BackfillCandidate(task_id=task.task_id, display_name=task.display_name)
        for task in tasks
        if not _has_full_report_artifact(task)
    ]


def _record_artifacts(session: Session, task_id: str, paths: Iterable[str]) -> None:
    existing = {
        row[0]
        for row in session.query(TaskArtifact.path).filter(TaskArtifact.task_id == task_id).all()
    }
    for path in paths:
        if path in existing:
            continue
        lowered = path.lower()
        if lowered.endswith(".jsonl"):
            artifact_type = "jsonl"
        elif lowered.endswith(".json"):
            artifact_type = "json"
        elif lowered.endswith(".xlsx"):
            artifact_type = "excel"
        elif lowered.endswith(".png"):
            artifact_type = "image_png"
        elif lowered.endswith(".pdf"):
            artifact_type = "pdf"
        else:
            artifact_type = "artifact"
        session.add(TaskArtifact(task_id=task_id, artifact_type=artifact_type, path=path, downloadable=True))


def run_backfill(session: Session, *, dry_run: bool) -> list[BackfillCandidate]:
    candidates = find_backfill_candidates(session)
    if dry_run:
        return candidates

    from worker_app.task_reports import generate_task_report_outputs

    settings = get_settings()
    for candidate in candidates:
        task = session.get(Task, candidate.task_id)
        if task is None or not task.vehicles:
            continue
        vehicle = sorted(task.vehicles, key=lambda item: (item.position, item.id or 0))[0]
        corpus_dir = (
            settings.corpus_root_path
            / f"{vehicle.model_name}__autohome-{vehicle.autohome_series_id or 'vehicle'}__dcd-{vehicle.dcd_series_id or 'vehicle'}"
        )
        result = generate_task_report_outputs(
            task_id=task.task_id,
            model_name=vehicle.model_name,
            autohome_raw_path=corpus_dir / "autohome" / "raw.xlsx",
            dcd_raw_path=corpus_dir / "dongchedi" / "raw.xlsx",
            output_root=settings.artifact_root_path / task.task_id / "outputs",
            allow_degraded=True,
        )
        _record_artifacts(session, task.task_id, [str(path) for path in result.get("artifact_paths") or []])
        session.commit()
    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill missing task one-pager reports.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = create_engine(settings.database_url, future=True)
    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as session:
        candidates = run_backfill(session, dry_run=args.dry_run)
        for candidate in candidates:
            print(f"{candidate.task_id}\t{candidate.display_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
