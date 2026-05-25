from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from functools import cmp_to_key


@dataclass(frozen=True)
class QueuedRun:
    run_id: str
    task_id: str
    task_type: str
    waited_seconds: int
    platform: str = ""


@dataclass(frozen=True)
class DispatchDecision:
    run_id: str
    task_id: str
    platform: str


def is_crowded(
    platform_queue_lengths: Mapping[str, int],
    agent_capacity: Mapping[str, int],
    oldest_wait_seconds: int,
) -> bool:
    if oldest_wait_seconds > 300:
        return True

    return any(
        queue_length >= max(agent_capacity.get(platform, 0), 1)
        for platform, queue_length in platform_queue_lengths.items()
    )


def available_agent_counts(
    agent_ids: Mapping[str, Collection[str]],
    leases: Collection[str] | Mapping[str, object],
) -> dict[str, int]:
    leased_agent_ids = set(leases.keys() if isinstance(leases, Mapping) else leases)

    return {
        platform: max(len(ids) - len(set(ids) & leased_agent_ids), 0)
        for platform, ids in agent_ids.items()
    }


@dataclass(frozen=True)
class _Candidate:
    run: QueuedRun
    queue_platform: str
    decision_platform: str
    ordinal: int


def plan_dispatch_order(
    platform_queues: Mapping[str, Collection[QueuedRun]],
    agent_capacity: Mapping[str, int],
    active_lanes_by_task: Mapping[str, int],
    single_task_priority_weight: float,
) -> list[DispatchDecision]:
    platform_runs = {
        platform: list(runs)
        for platform, runs in platform_queues.items()
        if agent_capacity.get(platform, 0) > 0
    }
    if not platform_runs:
        return []

    queue_lengths = {platform: len(runs) for platform, runs in platform_runs.items()}
    oldest_wait_seconds = max(
        (run.waited_seconds for runs in platform_runs.values() for run in runs),
        default=0,
    )
    crowded = is_crowded(queue_lengths, agent_capacity, oldest_wait_seconds)
    platform_rank = _platform_priority(platform_runs, agent_capacity)

    pending: list[_Candidate] = []
    ordinal = 0
    for platform, runs in platform_runs.items():
        for run in runs:
            pending.append(
                _Candidate(
                    run=run,
                    queue_platform=platform,
                    decision_platform=run.platform or platform,
                    ordinal=ordinal,
                )
            )
            ordinal += 1

    remaining_capacity = {
        platform: agent_capacity.get(platform, 0)
        for platform in platform_runs
        if agent_capacity.get(platform, 0) > 0
    }
    consumed_run_ids: set[str] = set()
    tasks_seen_this_pass: set[str] = set()
    decisions: list[DispatchDecision] = []

    while any(count > 0 for count in remaining_capacity.values()):
        task_candidates = _best_candidate_by_task(
            pending=pending,
            consumed_run_ids=consumed_run_ids,
            tasks_seen_this_pass=tasks_seen_this_pass,
            remaining_capacity=remaining_capacity,
            active_lanes_by_task=active_lanes_by_task,
            crowded=crowded,
            platform_rank=platform_rank,
            single_task_priority_weight=single_task_priority_weight,
        )

        if not task_candidates:
            if tasks_seen_this_pass:
                tasks_seen_this_pass.clear()
                continue
            break

        candidate = sorted(
            task_candidates.values(),
            key=cmp_to_key(
                lambda left, right: _compare_candidates(
                    left,
                    right,
                    platform_rank,
                    single_task_priority_weight,
                )
            ),
        )[0]

        decisions.append(
            DispatchDecision(
                run_id=candidate.run.run_id,
                task_id=candidate.run.task_id,
                platform=candidate.decision_platform,
            )
        )
        consumed_run_ids.add(candidate.run.run_id)
        tasks_seen_this_pass.add(candidate.run.task_id)
        remaining_capacity[candidate.queue_platform] -= 1

    return decisions


def _platform_priority(
    platform_runs: Mapping[str, list[QueuedRun]],
    agent_capacity: Mapping[str, int],
) -> dict[str, int]:
    ranked_platforms = sorted(
        platform_runs,
        key=lambda platform: (
            -(len(platform_runs[platform]) / agent_capacity[platform]),
            -max((run.waited_seconds for run in platform_runs[platform]), default=0),
            platform,
        ),
    )
    return {platform: rank for rank, platform in enumerate(ranked_platforms)}


def _best_candidate_by_task(
    *,
    pending: list[_Candidate],
    consumed_run_ids: set[str],
    tasks_seen_this_pass: set[str],
    remaining_capacity: Mapping[str, int],
    active_lanes_by_task: Mapping[str, int],
    crowded: bool,
    platform_rank: Mapping[str, int],
    single_task_priority_weight: float,
) -> dict[str, _Candidate]:
    best_by_task: dict[str, _Candidate] = {}
    for candidate in pending:
        if candidate.run.run_id in consumed_run_ids:
            continue
        if remaining_capacity.get(candidate.queue_platform, 0) <= 0:
            continue
        if candidate.run.task_id in tasks_seen_this_pass:
            continue
        if crowded and active_lanes_by_task.get(candidate.run.task_id, 0) >= 1:
            continue

        current = best_by_task.get(candidate.run.task_id)
        if current is None or _compare_candidates(
            candidate,
            current,
            platform_rank,
            single_task_priority_weight,
        ) < 0:
            best_by_task[candidate.run.task_id] = candidate

    return best_by_task


def _compare_candidates(
    left: _Candidate,
    right: _Candidate,
    platform_rank: Mapping[str, int],
    single_task_priority_weight: float,
) -> int:
    left_rank = platform_rank.get(left.queue_platform, len(platform_rank))
    right_rank = platform_rank.get(right.queue_platform, len(platform_rank))
    if left_rank != right_rank:
        return left_rank - right_rank

    close_wait_threshold = max(int(round(300 * single_task_priority_weight)), 0)
    waited_delta = left.run.waited_seconds - right.run.waited_seconds
    if abs(waited_delta) <= close_wait_threshold:
        left_single = left.run.task_type == "single"
        right_single = right.run.task_type == "single"
        if left_single != right_single:
            return -1 if left_single else 1

    if waited_delta != 0:
        return -1 if waited_delta > 0 else 1

    return left.ordinal - right.ordinal
