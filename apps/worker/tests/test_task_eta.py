from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.task_eta import (  # noqa: E402
    clear_eta_metrics,
    estimate_comparison_seconds,
    estimate_queue_seconds,
    eta_reason_for_platform,
    record_stage_duration,
)


def test_record_stage_duration_updates_p50_and_p90_from_observed_durations() -> None:
    clear_eta_metrics()

    observations = [10, 20, 30, 40, 100]
    metric = None
    for duration in observations:
        metric = record_stage_duration("platform_collection", "autohome", "single_vehicle", duration)

    assert metric is not None
    assert metric.sample_count == 5
    assert metric.p50_seconds == 30
    assert metric.p90_seconds == 100


def test_queue_eta_uses_queue_position_and_platform_agent_count() -> None:
    assert estimate_queue_seconds(position=3, agent_count=2, p50_seconds=60) == 120
    assert estimate_queue_seconds(position=0, agent_count=2, p50_seconds=60) == 0
    assert estimate_queue_seconds(position=3, agent_count=0, p50_seconds=60) == 180


def test_comparison_eta_uses_max_vehicle_path_not_sum() -> None:
    assert estimate_comparison_seconds([180, 30, 75], summary_seconds=45) == 225
    assert estimate_comparison_seconds([], summary_seconds=45) == 45


def test_eta_reason_changes_when_platform_queue_is_congested() -> None:
    normal = eta_reason_for_platform("autohome", queue_length=1, agent_count=2, retrying_count=0)
    congested = eta_reason_for_platform("autohome", queue_length=2, agent_count=2, retrying_count=0)

    assert normal != congested
    assert "normal" in normal
    assert "congested" in congested
    assert "autohome" in congested
