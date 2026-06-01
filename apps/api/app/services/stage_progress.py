from __future__ import annotations

import json

from app.config import Settings


PROGRESS_FILE_STAGES = {"checking_incremental", "collecting_autohome", "collecting_dcd", "generating_hermes_outputs"}


def clamp_percent(value: object) -> int | None:
    try:
        percent = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, percent))


def read_stage_progress(settings: Settings, job_id: str, stage_name: str, stage_status: str) -> tuple[int | None, str | None]:
    if stage_name not in PROGRESS_FILE_STAGES:
        return None, None

    progress_path = settings.artifact_root_path / job_id / "progress" / f"{stage_name}.progress.json"
    payload: dict = {}
    if progress_path.exists():
        try:
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}

    percent = clamp_percent(payload.get("percent"))
    if percent is None and isinstance(payload.get("overall"), dict):
        percent = clamp_percent(payload["overall"].get("percent"))
    if percent is None:
        if stage_status in {"success", "completed"}:
            percent = 100
        elif stage_status == "running":
            percent = 1
        else:
            percent = 0

    message = payload.get("message")
    if not message and stage_status == "running":
        message = "采集已启动，等待页面进度"
    return percent, message if isinstance(message, str) and message else None
