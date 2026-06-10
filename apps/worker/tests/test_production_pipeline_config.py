from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _compose_service_block(service: str) -> str:
    compose_text = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^  {re.escape(service)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", compose_text)
    assert match is not None, f"missing compose service: {service}"
    return match.group("body")


def test_compose_passes_collector_wait_controls_to_worker_services() -> None:
    expected = {
        "COLLECTOR_WAIT_TIMEOUT_SECONDS: ${COLLECTOR_WAIT_TIMEOUT_SECONDS:-2400}",
        "COLLECTOR_WAIT_POLL_SECONDS: ${COLLECTOR_WAIT_POLL_SECONDS:-5}",
        "COLLECTOR_PROGRESS_STALL_TIMEOUT_SECONDS: ${COLLECTOR_PROGRESS_STALL_TIMEOUT_SECONDS:-900}",
    }
    for service in ("worker", "temporal-worker"):
        block = _compose_service_block(service)
        for line in expected:
            assert line in block


def test_compose_passes_openclaw_resource_gate_and_browser_controls() -> None:
    worker_expected = {
        "OPENCLAW_MAX_ACTIVE_COLLECTIONS: ${OPENCLAW_MAX_ACTIVE_COLLECTIONS:-2}",
        "OPENCLAW_BURST_ACTIVE_COLLECTIONS: ${OPENCLAW_BURST_ACTIVE_COLLECTIONS:-3}",
        "OPENCLAW_MIN_MEM_AVAILABLE_MB: ${OPENCLAW_MIN_MEM_AVAILABLE_MB:-3000}",
        "OPENCLAW_BURST_MIN_MEM_AVAILABLE_MB: ${OPENCLAW_BURST_MIN_MEM_AVAILABLE_MB:-4000}",
        "OPENCLAW_MAX_SWAP_USED_MB: ${OPENCLAW_MAX_SWAP_USED_MB:-512}",
    }
    collector_expected = worker_expected | {
        "COLLECTOR_SERVICE_RESOURCE_GUARD_ENABLED: ${COLLECTOR_SERVICE_RESOURCE_GUARD_ENABLED:-true}",
    }
    browser_expected = {
        "OPENCLAW_BROWSER_ARGS: ${OPENCLAW_BROWSER_ARGS:---no-sandbox --disable-dev-shm-usage --disable-gpu --disable-extensions --disable-background-networking --disable-sync --mute-audio --no-first-run --no-default-browser-check --renderer-process-limit=4}",
        "OPENCLAW_AUTOHOME_DIRECT_ENABLED: ${OPENCLAW_AUTOHOME_DIRECT_ENABLED:-false}",
        "OPENCLAW_AUTOHOME_BLOCK_RESOURCE_TYPES: ${OPENCLAW_AUTOHOME_BLOCK_RESOURCE_TYPES:-image,media,font}",
        "OPENCLAW_DCD_BLOCK_RESOURCE_TYPES: ${OPENCLAW_DCD_BLOCK_RESOURCE_TYPES:-}",
    }

    for service in ("worker", "temporal-worker"):
        block = _compose_service_block(service)
        for line in worker_expected | browser_expected:
            assert line in block

    for service in ("autohome-collector", "dongchedi-collector"):
        block = _compose_service_block(service)
        for line in collector_expected | browser_expected:
            assert line in block
