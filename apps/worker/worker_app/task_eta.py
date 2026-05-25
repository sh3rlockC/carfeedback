from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from typing import Any

from sqlalchemy import create_engine, text


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
    *,
    session: Any | None = None,
    database_url: str | None = None,
) -> StageDurationMetric:
    key = _metric_key(stage, platform, task_type)
    samples = _duration_samples[key]
    samples.append(max(0, int(round(float(duration_seconds)))))
    sorted_samples = sorted(samples)
    metric = StageDurationMetric(
        stage=key[0],
        platform=key[1],
        task_type=key[2],
        sample_count=len(sorted_samples),
        p50_seconds=_nearest_rank(sorted_samples, 0.50),
        p90_seconds=_nearest_rank(sorted_samples, 0.90),
    )
    if session is not None:
        _upsert_eta_metric(session, metric)
    elif database_url is not None:
        engine = create_engine(database_url, future=True, **_engine_kwargs(database_url))
        try:
            with engine.begin() as connection:
                _upsert_eta_metric(connection, metric)
        finally:
            engine.dispose()
    return metric


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


def _upsert_eta_metric(connection: Any, metric: StageDurationMetric) -> None:
    connection.execute(
        text(
            """
            INSERT INTO eta_metrics (
                stage, platform, task_type, sample_count, p50_seconds, p90_seconds, updated_at
            )
            VALUES (
                :stage, :platform, :task_type, :sample_count, :p50_seconds, :p90_seconds, :updated_at
            )
            ON CONFLICT(stage, platform, task_type) DO UPDATE SET
                sample_count = eta_metrics.sample_count + 1,
                p50_seconds = excluded.p50_seconds,
                p90_seconds = excluded.p90_seconds,
                updated_at = excluded.updated_at
            """
        ),
        {
            "stage": metric.stage,
            "platform": metric.platform,
            "task_type": metric.task_type,
            "sample_count": metric.sample_count,
            "p50_seconds": metric.p50_seconds,
            "p90_seconds": metric.p90_seconds,
            "updated_at": datetime.now(UTC).isoformat(),
        },
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
