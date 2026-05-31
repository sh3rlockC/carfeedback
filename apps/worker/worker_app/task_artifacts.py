from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import uuid
import zipfile

from sqlalchemy import text


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


def _existing_file(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_file():
        raise FileNotFoundError(f"task artifact file does not exist: {candidate}")
    return candidate


def _existing_files(paths: Iterable[str | Path]) -> list[Path]:
    return [_existing_file(path) for path in paths]


def _write_zip_atomic(output_path: Path, entries: Iterable[tuple[Path, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path, arcname in entries:
                archive.write(file_path, arcname)
        temp_path.replace(output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _write_business_zip(output_path: Path, files: Iterable[Path]) -> None:
    used: set[str] = set()
    entries = [(path, _unique_arcname(used, path.name)) for path in files]
    _write_zip_atomic(output_path, entries)


def _write_structured_zip(output_path: Path, entries: Iterable[tuple[str | Path, str]]) -> None:
    used: set[str] = set()
    normalized_entries = [
        (_existing_file(path), _unique_arcname(used, arcname))
        for path, arcname in entries
    ]
    _write_zip_atomic(output_path, normalized_entries)


def _normalise_vehicle_raw_paths(vehicle_raw_paths: Mapping[str, str | Path] | Iterable[str | Path]) -> list[tuple[str, Path]]:
    if isinstance(vehicle_raw_paths, Mapping):
        return [(str(label), Path(path)) for label, path in vehicle_raw_paths.items()]
    return [(Path(path).stem or f"vehicle_{index}", Path(path)) for index, path in enumerate(vehicle_raw_paths, start=1)]


def create_single_task_downloads(
    task_id: str,
    output_dir: str | Path,
    merged_raw_path: str | Path,
    business_files: Iterable[str | Path],
    *,
    one_pager_path: str | Path | None = None,
    bundle_entries: Iterable[tuple[str | Path, str]] | None = None,
) -> list[TaskArtifactRecord]:
    merged_raw = _existing_file(merged_raw_path)
    business_paths = _existing_files(business_files)
    one_pager = _existing_file(one_pager_path) if one_pager_path is not None else None
    business_zip_path = Path(output_dir) / f"{task_id}_business.zip"
    if bundle_entries is None:
        _write_business_zip(business_zip_path, business_paths)
    else:
        _write_structured_zip(business_zip_path, bundle_entries)
    records = [
        TaskArtifactRecord(task_id=task_id, artifact_type="business_zip", path=str(business_zip_path)),
        TaskArtifactRecord(task_id=task_id, artifact_type="merged_raw_excel", path=str(merged_raw)),
    ]
    if one_pager is not None:
        records.append(TaskArtifactRecord(task_id=task_id, artifact_type="one_pager_excel", path=str(one_pager)))
    return records


def create_comparison_downloads(
    task_id: str,
    output_dir: str | Path,
    vehicle_raw_paths: Mapping[str, str | Path] | Iterable[str | Path],
    business_files: Iterable[str | Path],
) -> list[TaskArtifactRecord]:
    output_path = Path(output_dir) / f"{task_id}_business.zip"
    raw_paths = [(label, _existing_file(path)) for label, path in _normalise_vehicle_raw_paths(vehicle_raw_paths)]
    business_paths = _existing_files(business_files)
    used: set[str] = set()
    entries = [(path, _unique_arcname(used, path.name)) for path in business_paths]
    entries.extend(
        (path, _unique_arcname(used, f"vehicle_raw/{_safe_zip_part(label)}_{path.name}"))
        for label, path in raw_paths
    )
    _write_zip_atomic(output_path, entries)

    return [
        TaskArtifactRecord(task_id=task_id, artifact_type="business_zip", path=str(output_path)),
        *[
            TaskArtifactRecord(task_id=task_id, artifact_type="vehicle_raw_excel", path=str(path))
            for _, path in raw_paths
        ],
    ]


def record_task_artifacts(session, records: Iterable[TaskArtifactRecord]) -> None:
    unique_records = list(dict.fromkeys(records))
    now = datetime.now(UTC).isoformat()
    for record in unique_records:
        params = {
            "task_id": record.task_id,
            "artifact_type": record.artifact_type,
            "path": record.path,
        }
        session.execute(
            text(
                """
                DELETE FROM task_artifacts
                WHERE task_id = :task_id
                  AND artifact_type = :artifact_type
                  AND path = :path
                """
            ),
            params,
        )
        session.execute(
            text(
                """
                INSERT INTO task_artifacts (task_id, artifact_type, path, downloadable, created_at)
                VALUES (:task_id, :artifact_type, :path, :downloadable, :created_at)
                """
            ),
            {
                **params,
                "downloadable": record.downloadable,
                "created_at": now,
            },
        )
    session.commit()
