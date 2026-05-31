from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.dependencies import discover_manifest_path, discover_workspace_root


def test_discover_workspace_root_prefers_workspace_mount(tmp_path: Path) -> None:
    mounted_workspace = tmp_path / "workspace"
    mounted_workspace.mkdir()

    workspace_root = discover_workspace_root(
        Path("/app/worker_app/dependencies.py"),
        mounted_workspace=mounted_workspace,
        fallback_cwd=tmp_path / "fallback",
    )

    assert workspace_root == mounted_workspace.resolve()


def test_discover_manifest_path_uses_workspace_root(tmp_path: Path) -> None:
    manifest_path = tmp_path / "carFeedbackv101" / "config" / "dependencies.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("dependencies: []\n", encoding="utf-8")

    discovered = discover_manifest_path(
        Path("/app/worker_app/dependencies.py"),
        workspace_root=tmp_path,
    )

    assert discovered == manifest_path.resolve()


def test_discover_manifest_path_uses_current_workspace_project(tmp_path: Path) -> None:
    manifest_path = tmp_path / "carFeedback" / "config" / "dependencies.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("dependencies: []\n", encoding="utf-8")

    discovered = discover_manifest_path(
        Path("/app/worker_app/dependencies.py"),
        workspace_root=tmp_path,
    )

    assert discovered == manifest_path.resolve()
