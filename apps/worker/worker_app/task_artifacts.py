from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
import zipfile


@dataclass(frozen=True)
class TaskArtifactRecord:
    task_id: str
    artifact_type: str
    path: str
    downloadable: bool = True


def _safe_zip_part(value: str) -> str:
    cleaned = re.sub(r"[\\/:\*\?\"<>\|\x00-\x1f]", "_", value.strip())
    return re.sub(r"\s+", " ", cleaned).strip(" .") or "vehicle"


def _unique_arcname(used: set[str], arcname: str) -> str:
    if arcname not in used:
        used.add(arcname)
        return arcname

    path = Path(arcname)
    stem = path.stem
    suffix = path.suffix
    parent = "" if str(path.parent) == "." else f"{path.parent}/"
    index = 2
    while True:
        candidate = f"{parent}{stem}_{index}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def _write_business_zip(output_path: Path, files: Iterable[str | Path]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in files:
            path = Path(file_path)
            archive.write(path, _unique_arcname(used, path.name))


def _normalise_vehicle_raw_paths(vehicle_raw_paths: Mapping[str, str | Path] | Iterable[str | Path]) -> list[tuple[str, Path]]:
    if isinstance(vehicle_raw_paths, Mapping):
        return [(str(label), Path(path)) for label, path in vehicle_raw_paths.items()]
    return [(Path(path).stem or f"vehicle_{index}", Path(path)) for index, path in enumerate(vehicle_raw_paths, start=1)]


def create_single_task_downloads(
    task_id: str,
    output_dir: str | Path,
    merged_raw_path: str | Path,
    business_files: Iterable[str | Path],
) -> list[TaskArtifactRecord]:
    business_zip_path = Path(output_dir) / f"{task_id}_business.zip"
    _write_business_zip(business_zip_path, business_files)
    return [
        TaskArtifactRecord(task_id=task_id, artifact_type="business_zip", path=str(business_zip_path)),
        TaskArtifactRecord(task_id=task_id, artifact_type="merged_raw_excel", path=str(Path(merged_raw_path))),
    ]


def create_comparison_downloads(
    task_id: str,
    output_dir: str | Path,
    vehicle_raw_paths: Mapping[str, str | Path] | Iterable[str | Path],
    business_files: Iterable[str | Path],
) -> list[TaskArtifactRecord]:
    output_path = Path(output_dir) / f"{task_id}_business.zip"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_paths = _normalise_vehicle_raw_paths(vehicle_raw_paths)
    used: set[str] = set()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in business_files:
            path = Path(file_path)
            archive.write(path, _unique_arcname(used, path.name))
        for label, file_path in raw_paths:
            safe_label = _safe_zip_part(label)
            archive.write(file_path, _unique_arcname(used, f"vehicle_raw/{safe_label}_{file_path.name}"))

    return [
        TaskArtifactRecord(task_id=task_id, artifact_type="business_zip", path=str(output_path)),
        *[
            TaskArtifactRecord(task_id=task_id, artifact_type="vehicle_raw_excel", path=str(path))
            for _, path in raw_paths
        ],
    ]


def record_task_artifacts(session, records: Iterable[TaskArtifactRecord]) -> None:
    from app.models import TaskArtifact

    session.add_all(
        [
            TaskArtifact(
                task_id=record.task_id,
                artifact_type=record.artifact_type,
                path=record.path,
                downloadable=record.downloadable,
            )
            for record in records
        ]
    )
    session.commit()
