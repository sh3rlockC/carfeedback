from __future__ import annotations

from collections.abc import Mapping
import os


PLATFORM_AGENT_ENV = {
    "autohome": "OPENCLAW_AUTOHOME_AGENT_IDS",
    "dongchedi": "OPENCLAW_DCD_AGENT_IDS",
}


def split_agent_ids(value: str | None) -> list[str]:
    if not value:
        return []
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def platform_agent_ids_from_env(environ: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    env = environ or os.environ
    configured: dict[str, list[str]] = {}
    for platform, env_name in PLATFORM_AGENT_ENV.items():
        agent_ids = split_agent_ids(env.get(env_name))
        if agent_ids:
            configured[platform] = agent_ids
    return configured


def choose_available_agent(
    platform: str,
    configured_agents: Mapping[str, list[str]],
    busy_agents: Mapping[str, set[str]],
) -> str | None:
    for agent_id in configured_agents.get(platform, []):
        if agent_id not in busy_agents.get(platform, set()):
            return agent_id
    return None
