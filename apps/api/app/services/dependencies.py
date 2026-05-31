from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def resolve_workspace_path(workspace_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (workspace_root / path).resolve()


def discover_manifest_path(source_path: Path | None = None, workspace_root: Path | None = None) -> Path:
    if workspace_root is not None:
        workspace_candidate = (workspace_root / "carFeedbackv101" / "config" / "dependencies.yaml").resolve()
        if workspace_candidate.exists():
            return workspace_candidate

    file_path = (source_path or Path(__file__)).resolve()
    for parent in file_path.parents:
        manifest_candidate = (parent / "config" / "dependencies.yaml").resolve()
        if manifest_candidate.exists():
            return manifest_candidate

    fallback_root = (workspace_root or Path.cwd()).expanduser().resolve()
    return (fallback_root / "carFeedbackv101" / "config" / "dependencies.yaml").resolve()


def load_dependency_map(manifest_path: Path, workspace_root: Path) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    dependency_map: dict[str, dict[str, Any]] = {}
    for item in data.get("dependencies", []):
        resolved = dict(item)
        resolved["path"] = str(resolve_workspace_path(workspace_root, item["path"]))
        resolved["entrypoint"] = str(resolve_workspace_path(workspace_root, item["entrypoint"]))
        dependency_map[item["name"]] = resolved
    return dependency_map
