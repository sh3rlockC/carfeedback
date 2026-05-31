from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.agent_pool import (
    choose_available_agent,
    platform_agent_ids_from_env,
    split_agent_ids,
)


def test_split_agent_ids_trims_and_deduplicates() -> None:
    assert split_agent_ids(" autohome-1,autohome-2,autohome-1 ,, ") == [
        "autohome-1",
        "autohome-2",
    ]


def test_platform_agent_ids_from_env_reads_v3_pools() -> None:
    env = {
        "OPENCLAW_AUTOHOME_AGENT_IDS": "autohome-1,autohome-2,autohome-3,autohome-4",
        "OPENCLAW_DCD_AGENT_IDS": "dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4",
    }

    assert platform_agent_ids_from_env(env) == {
        "autohome": ["autohome-1", "autohome-2", "autohome-3", "autohome-4"],
        "dongchedi": ["dongchedi-1", "dongchedi-2", "dongchedi-3", "dongchedi-4"],
    }


def test_choose_available_agent_skips_busy_agents() -> None:
    configured = {"autohome": ["autohome-1", "autohome-2", "autohome-3"]}
    busy = {"autohome": {"autohome-1", "autohome-3"}}

    assert choose_available_agent("autohome", configured, busy) == "autohome-2"


def test_choose_available_agent_returns_none_when_platform_pool_is_full() -> None:
    configured = {"dongchedi": ["dongchedi-1", "dongchedi-2"]}
    busy = {"dongchedi": {"dongchedi-1", "dongchedi-2"}}

    assert choose_available_agent("dongchedi", configured, busy) is None
