from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def discover_workspace_root(
    source_path: Path | None = None,
    *,
    mounted_workspace: Path | None = None,
    fallback_cwd: Path | None = None,
) -> Path:
    workspace_mount = (mounted_workspace or Path("/workspace")).expanduser()
    if workspace_mount.exists():
        return workspace_mount.resolve()

    file_path = (source_path or Path(__file__)).resolve()
    for parent in file_path.parents:
        if (parent / "config" / "dependencies.yaml").exists():
            return parent.parent.resolve()

    return (fallback_cwd or Path.cwd()).expanduser().resolve()


DEFAULT_WORKSPACE_ROOT = discover_workspace_root()


def get_workspace_root() -> Path:
    return Path(os.getenv("WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT))).expanduser().resolve()


def resolve_workspace_path(workspace_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (workspace_root / path).resolve()


def discover_manifest_path(source_path: Path | None = None, workspace_root: Path | None = None) -> Path:
    resolved_workspace_root = workspace_root or get_workspace_root()
    for project_dir in ("", "carfeedback", "carFeedback"):
        workspace_candidate = (resolved_workspace_root / project_dir / "config" / "dependencies.yaml").resolve()
        if workspace_candidate.exists():
            return workspace_candidate

    file_path = (source_path or Path(__file__)).resolve()
    for parent in file_path.parents:
        manifest_candidate = (parent / "config" / "dependencies.yaml").resolve()
        if manifest_candidate.exists():
            return manifest_candidate

    return (resolved_workspace_root / "carfeedback" / "config" / "dependencies.yaml").resolve()


def load_dependency_map(manifest_path: Path) -> dict[str, dict[str, Any]]:
    workspace_root = get_workspace_root()
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    dependency_map: dict[str, dict[str, Any]] = {}
    for item in data.get("dependencies", []):
        resolved = dict(item)
        resolved["path"] = str(resolve_workspace_path(workspace_root, item["path"]))
        resolved["entrypoint"] = str(resolve_workspace_path(workspace_root, item["entrypoint"]))
        dependency_map[item["name"]] = resolved
    return dependency_map
