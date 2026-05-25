from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CollectionRun, Task


RUNNING_TASK_STATUSES = ("running",)
QUEUED_TASK_STATUSES = ("queued", "waiting_agent", "retry_wait")
RUNNING_COLLECTION_STATUSES = ("running",)


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def platform_agent_ids_from_env(environ: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    env = environ or os.environ
    raw_json = env.get("TASK_LOAD_PLATFORM_AGENT_IDS")
    if raw_json:
        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict):
            raise ValueError("TASK_LOAD_PLATFORM_AGENT_IDS must be a JSON object")
        return {
            str(platform): _unique([str(agent_id) for agent_id in agent_ids])
            for platform, agent_ids in parsed.items()
            if isinstance(agent_ids, list)
        }

    autohome_ids = _split_env_list(env.get("OPENCLAW_AUTOHOME_AGENT_IDS"))
    if not autohome_ids:
        autohome_ids = _split_env_list(env.get("OPENCLAW_AUTOHOME_AGENT_ID"))
    dongchedi_ids = _split_env_list(env.get("OPENCLAW_DCD_AGENT_IDS"))
    if not dongchedi_ids:
        dongchedi_ids = _split_env_list(env.get("OPENCLAW_DCD_AGENT_ID"))

    configured: dict[str, list[str]] = {}
    if autohome_ids:
        configured["autohome"] = _unique(autohome_ids)
    if dongchedi_ids:
        configured["dongchedi"] = _unique(dongchedi_ids)
    return configured


def _count_tasks(db: Session, statuses: Sequence[str]) -> int:
    return int(db.query(func.count(Task.task_id)).filter(Task.status.in_(statuses)).scalar() or 0)


def _platform_busy_agent_ids(db: Session) -> dict[str, set[str]]:
    rows = (
        db.query(CollectionRun.platform, CollectionRun.agent_id)
        .filter(
            CollectionRun.status.in_(RUNNING_COLLECTION_STATUSES),
            CollectionRun.agent_id.isnot(None),
        )
        .all()
    )
    busy: dict[str, set[str]] = {}
    for platform, agent_id in rows:
        busy.setdefault(str(platform), set()).add(str(agent_id))
    return busy


def project_task_load(
    db: Session,
    *,
    platform_agent_ids: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    agent_ids_source = platform_agent_ids_from_env() if platform_agent_ids is None else platform_agent_ids
    configured_agents = {
        str(platform): _unique(agent_ids)
        for platform, agent_ids in agent_ids_source.items()
    }
    busy_by_platform = _platform_busy_agent_ids(db)
    platform_names = sorted(set(configured_agents) | set(busy_by_platform))

    platforms = {}
    for platform in platform_names:
        configured = set(configured_agents.get(platform, []))
        busy = busy_by_platform.get(platform, set())
        total = len(configured) if configured else len(busy)
        available = max(0, total - len(busy & configured if configured else busy))
        platforms[platform] = {"available": available, "total": total}

    return {
        "running_task_count": _count_tasks(db, RUNNING_TASK_STATUSES),
        "queued_task_count": _count_tasks(db, QUEUED_TASK_STATUSES),
        "platforms": platforms,
    }
