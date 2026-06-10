from __future__ import annotations

from worker_app.openclaw_resource_gate import (
    OpenClawResourceSettings,
    ResourceSnapshot,
    decide_openclaw_admission,
)


def test_default_gate_allows_two_regular_and_one_healthy_burst_only() -> None:
    settings = OpenClawResourceSettings()

    regular = decide_openclaw_admission(
        active_collections=1,
        snapshot=ResourceSnapshot(mem_available_mb=3100, swap_used_mb=100),
        settings=settings,
    )
    blocked_burst = decide_openclaw_admission(
        active_collections=2,
        snapshot=ResourceSnapshot(mem_available_mb=3900, swap_used_mb=100),
        settings=settings,
    )
    allowed_burst = decide_openclaw_admission(
        active_collections=2,
        snapshot=ResourceSnapshot(mem_available_mb=4100, swap_used_mb=100),
        settings=settings,
    )
    fourth = decide_openclaw_admission(
        active_collections=3,
        snapshot=ResourceSnapshot(mem_available_mb=6000, swap_used_mb=100),
        settings=settings,
    )

    assert regular.allowed
    assert regular.reason == "within_regular_capacity"
    assert regular.effective_limit == 2
    assert not blocked_burst.allowed
    assert blocked_burst.reason == "burst_memory_low"
    assert allowed_burst.allowed
    assert allowed_burst.burst is True
    assert allowed_burst.effective_limit == 3
    assert not fourth.allowed
    assert fourth.reason == "active_limit"


def test_fourth_collection_requires_burst_memory_watermark() -> None:
    settings = OpenClawResourceSettings(
        max_active_collections=2,
        burst_active_collections=3,
        min_mem_available_mb=3000,
        burst_min_mem_available_mb=4000,
        max_swap_used_mb=512,
    )

    blocked = decide_openclaw_admission(
        active_collections=2,
        snapshot=ResourceSnapshot(mem_available_mb=3900, swap_used_mb=100),
        settings=settings,
    )
    allowed = decide_openclaw_admission(
        active_collections=2,
        snapshot=ResourceSnapshot(mem_available_mb=4100, swap_used_mb=100),
        settings=settings,
    )
    fourth = decide_openclaw_admission(
        active_collections=3,
        snapshot=ResourceSnapshot(mem_available_mb=6000, swap_used_mb=100),
        settings=settings,
    )

    assert not blocked.allowed
    assert blocked.reason == "burst_memory_low"
    assert allowed.allowed
    assert allowed.burst is True
    assert allowed.effective_limit == 3
    assert not fourth.allowed
    assert fourth.reason == "active_limit"


def test_regular_collection_blocks_when_memory_or_swap_watermark_is_unhealthy() -> None:
    settings = OpenClawResourceSettings(
        max_active_collections=2,
        burst_active_collections=3,
        min_mem_available_mb=3000,
        burst_min_mem_available_mb=4000,
        max_swap_used_mb=512,
    )

    low_memory = decide_openclaw_admission(
        active_collections=1,
        snapshot=ResourceSnapshot(mem_available_mb=2900, swap_used_mb=100),
        settings=settings,
    )
    high_swap = decide_openclaw_admission(
        active_collections=1,
        snapshot=ResourceSnapshot(mem_available_mb=5000, swap_used_mb=600),
        settings=settings,
    )

    assert not low_memory.allowed
    assert low_memory.reason == "memory_low"
    assert not high_swap.allowed
    assert high_swap.reason == "swap_high"
