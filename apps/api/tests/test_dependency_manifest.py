from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.dependencies import discover_manifest_path


def test_discover_manifest_path_uses_current_workspace_project(tmp_path: Path) -> None:
    manifest_path = tmp_path / "carFeedback" / "config" / "dependencies.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("dependencies: []\n", encoding="utf-8")

    discovered = discover_manifest_path(
        Path("/app/app/services/dependencies.py"),
        workspace_root=tmp_path,
    )

    assert discovered == manifest_path.resolve()
