from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil


MAX_DURATION_SAMPLES = 100


@dataclass(frozen=True)
class StageDurationMetric:
    stage: str
    platform: str | None
    task_type: str | None
    sample_count: int
    p50_seconds: int
    p90_seconds: int


_duration_samples: dict[tuple[str, str | None, str | None], deque[int]] = defaultdict(
    lambda: deque(maxlen=MAX_DURATION_SAMPLES)
)


def clear_eta_metrics() -> None:
    _duration_samples.clear()


def _metric_key(stage: str, platform: str | None, task_type: str | None) -> tuple[str, str | None, str | None]:
    return (str(stage), str(platform) if platform else None, str(task_type) if task_type else None)


def _nearest_rank(sorted_values: list[int], percentile: float) -> int:
    if not sorted_values:
        return 0
    index = max(0, ceil(len(sorted_values) * percentile) - 1)
    return sorted_values[min(index, len(sorted_values) - 1)]


def record_stage_duration(
    stage: str,
    platform: str | None,
    task_type: str | None,
    duration_seconds: int | float,
) -> StageDurationMetric:
    key = _metric_key(stage, platform, task_type)
    samples = _duration_samples[key]
    samples.append(max(0, int(round(float(duration_seconds)))))
    sorted_samples = sorted(samples)
    return StageDurationMetric(
        stage=key[0],
        platform=key[1],
        task_type=key[2],
        sample_count=len(sorted_samples),
        p50_seconds=_nearest_rank(sorted_samples, 0.50),
        p90_seconds=_nearest_rank(sorted_samples, 0.90),
    )


def estimate_queue_seconds(position: int, agent_count: int, p50_seconds: int) -> int:
    queue_position = max(0, int(position))
    if queue_position == 0:
        return 0
    agents = max(1, int(agent_count))
    stage_seconds = max(0, int(p50_seconds))
    return ceil(queue_position / agents) * stage_seconds


def estimate_comparison_seconds(vehicle_estimates: Iterable[int], summary_seconds: int) -> int:
    vehicle_path_seconds = max([0, *[max(0, int(value)) for value in vehicle_estimates]])
    return vehicle_path_seconds + max(0, int(summary_seconds))


def eta_reason_for_platform(platform: str, queue_length: int, agent_count: int, retrying_count: int) -> str:
    platform_name = str(platform)
    queue = max(0, int(queue_length))
    agents = max(1, int(agent_count))
    retrying = max(0, int(retrying_count))
    if queue >= agents:
        return (
            f"{platform_name}:congested queue_length={queue} "
            f"agent_count={agents} retrying_count={retrying}"
        )
    if retrying:
        return (
            f"{platform_name}:retrying queue_length={queue} "
            f"agent_count={agents} retrying_count={retrying}"
        )
    return f"{platform_name}:normal queue_length={queue} agent_count={agents} retrying_count=0"
