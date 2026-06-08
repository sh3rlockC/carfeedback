from __future__ import annotations

from worker_app.openclaw_resource_gate import (
    OpenClawResourceSettings,
    ResourceSnapshot,
    decide_openclaw_admission,
)


def test_fourth_collection_requires_burst_memory_watermark() -> None:
    settings = OpenClawResourceSettings(
        max_active_collections=3,
        burst_active_collections=4,
        min_mem_available_mb=2500,
        burst_min_mem_available_mb=3500,
        max_swap_used_mb=768,
    )

    blocked = decide_openclaw_admission(
        active_collections=3,
        snapshot=ResourceSnapshot(mem_available_mb=3400, swap_used_mb=100),
        settings=settings,
    )
    allowed = decide_openclaw_admission(
        active_collections=3,
        snapshot=ResourceSnapshot(mem_available_mb=3600, swap_used_mb=100),
        settings=settings,
    )

    assert not blocked.allowed
    assert blocked.reason == "burst_memory_low"
    assert allowed.allowed
    assert allowed.burst is True
    assert allowed.effective_limit == 4


def test_regular_collection_blocks_when_memory_or_swap_watermark_is_unhealthy() -> None:
    settings = OpenClawResourceSettings(
        max_active_collections=3,
        burst_active_collections=4,
        min_mem_available_mb=2500,
        burst_min_mem_available_mb=3500,
        max_swap_used_mb=768,
    )

    low_memory = decide_openclaw_admission(
        active_collections=1,
        snapshot=ResourceSnapshot(mem_available_mb=2400, swap_used_mb=100),
        settings=settings,
    )
    high_swap = decide_openclaw_admission(
        active_collections=1,
        snapshot=ResourceSnapshot(mem_available_mb=5000, swap_used_mb=900),
        settings=settings,
    )

    assert not low_memory.allowed
    assert low_memory.reason == "memory_low"
    assert not high_swap.allowed
    assert high_swap.reason == "swap_high"
