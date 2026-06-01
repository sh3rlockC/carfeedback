from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.task_scheduler import (  # noqa: E402
    QueuedRun,
    available_agent_counts,
    is_crowded,
    plan_dispatch_order,
)


def test_available_agent_counts_reads_dynamic_agent_ids() -> None:
    agent_ids = {
        "autohome": ["autohome_1", "autohome_2", "autohome_3"],
        "dongchedi": ["dongchedi_1"],
    }
    leases = {"autohome_2", "unknown_agent"}

    assert available_agent_counts(agent_ids, leases) == {
        "autohome": 2,
        "dongchedi": 1,
    }


def test_available_agent_counts_accepts_mapping_leases() -> None:
    agent_ids = {
        "autohome": ["autohome_1", "autohome_2"],
        "dongchedi": ["dongchedi_1", "dongchedi_2"],
    }
    leases = {"autohome_1": "run_a", "dongchedi_2": "run_b"}

    assert available_agent_counts(agent_ids, leases) == {
        "autohome": 1,
        "dongchedi": 1,
    }


def test_available_agent_counts_deduplicates_configured_agent_ids() -> None:
    agent_ids = {
        "autohome": ["autohome_1", "autohome_1", "autohome_2"],
        "dongchedi": ["dongchedi_1", "dongchedi_1"],
    }
    leases = {"autohome_1": "run_a", "dongchedi_1": "run_b"}

    assert available_agent_counts(agent_ids, leases) == {
        "autohome": 1,
        "dongchedi": 0,
    }


def test_queued_run_supports_plan_positional_constructor_order() -> None:
    run = QueuedRun("run_a", "task_big", "comparison", "autohome", 60)

    assert run.platform == "autohome"
    assert run.waited_seconds == 60

    order = plan_dispatch_order(
        platform_queues={"autohome": [run]},
        agent_capacity={"autohome": 1},
        active_lanes_by_task={},
        single_task_priority_weight=0.1,
    )

    assert order[0].platform == "autohome"
    assert order[0].run_id == "run_a"


def test_is_crowded_when_queue_length_reaches_agent_count() -> None:
    assert is_crowded(
        platform_queue_lengths={"autohome": 2, "dongchedi": 0},
        agent_capacity={"autohome": 2, "dongchedi": 1},
        oldest_wait_seconds=60,
    )


def test_is_crowded_when_oldest_wait_exceeds_five_minutes() -> None:
    assert is_crowded(
        platform_queue_lengths={"autohome": 1},
        agent_capacity={"autohome": 4},
        oldest_wait_seconds=301,
    )


def test_is_not_crowded_below_capacity_and_wait_threshold() -> None:
    assert not is_crowded(
        platform_queue_lengths={"autohome": 1, "dongchedi": 0},
        agent_capacity={"autohome": 2, "dongchedi": 1},
        oldest_wait_seconds=300,
    )


def test_crowded_mode_permits_one_active_lane_per_task() -> None:
    order = plan_dispatch_order(
        platform_queues={
            "autohome": [
                QueuedRun("run_a", "task_big", "comparison", waited_seconds=70),
                QueuedRun("run_b", "task_other", "comparison", waited_seconds=65),
            ],
            "dongchedi": [
                QueuedRun("run_c", "task_big", "comparison", waited_seconds=70),
            ],
        },
        agent_capacity={"autohome": 2, "dongchedi": 1},
        active_lanes_by_task={"task_big": 1},
        single_task_priority_weight=0.1,
    )

    assert [(decision.run_id, decision.task_id) for decision in order] == [
        ("run_b", "task_other"),
    ]


def test_crowded_mode_counts_planned_lanes_for_each_task() -> None:
    order = plan_dispatch_order(
        platform_queues={
            "autohome": [
                QueuedRun("run_a", "task_big", "comparison", waited_seconds=70),
                QueuedRun("run_b", "task_other", "comparison", waited_seconds=65),
            ],
            "dongchedi": [
                QueuedRun("run_c", "task_big", "comparison", waited_seconds=69),
            ],
        },
        agent_capacity={"autohome": 2, "dongchedi": 1},
        active_lanes_by_task={},
        single_task_priority_weight=0.1,
    )

    assert [decision.task_id for decision in order].count("task_big") == 1


def test_round_robin_gives_each_task_one_opportunity() -> None:
    order = plan_dispatch_order(
        platform_queues={
            "autohome": [
                QueuedRun("run_a", "task_big", "comparison", waited_seconds=120),
                QueuedRun("run_b", "task_big", "comparison", waited_seconds=119),
                QueuedRun("run_c", "task_other", "comparison", waited_seconds=118),
            ],
        },
        agent_capacity={"autohome": 4},
        active_lanes_by_task={},
        single_task_priority_weight=0.1,
    )

    assert [decision.task_id for decision in order] == [
        "task_big",
        "task_other",
        "task_big",
    ]


def test_single_task_boost_breaks_close_tie_only() -> None:
    order = plan_dispatch_order(
        platform_queues={
            "autohome": [
                QueuedRun("run_a", "task_big", "comparison", waited_seconds=60),
                QueuedRun("run_b", "task_single", "single", waited_seconds=58),
            ],
            "dongchedi": [
                QueuedRun("run_c", "task_big", "comparison", waited_seconds=60),
            ],
        },
        agent_capacity={"autohome": 2, "dongchedi": 1},
        active_lanes_by_task={},
        single_task_priority_weight=0.1,
    )

    assert order[0].task_id == "task_single"


def test_single_task_boost_does_not_beat_much_older_comparison() -> None:
    order = plan_dispatch_order(
        platform_queues={
            "autohome": [
                QueuedRun("run_a", "task_big", "comparison", waited_seconds=180),
                QueuedRun("run_b", "task_single", "single", waited_seconds=60),
            ],
        },
        agent_capacity={"autohome": 2},
        active_lanes_by_task={},
        single_task_priority_weight=0.1,
    )

    assert order[0].task_id == "task_big"


def test_bottleneck_platform_queue_is_prioritized() -> None:
    order = plan_dispatch_order(
        platform_queues={
            "autohome": [
                QueuedRun("run_a", "task_autohome", "comparison", waited_seconds=100),
                QueuedRun("run_b", "task_autohome_2", "comparison", waited_seconds=90),
            ],
            "dongchedi": [
                QueuedRun("run_c", "task_dcd", "comparison", waited_seconds=80),
                QueuedRun("run_d", "task_dcd_2", "comparison", waited_seconds=70),
            ],
        },
        agent_capacity={"autohome": 4, "dongchedi": 1},
        active_lanes_by_task={},
        single_task_priority_weight=0.1,
    )

    assert order[0].platform == "dongchedi"
    assert order[0].run_id == "run_c"


def test_no_decisions_for_platforms_without_capacity() -> None:
    order = plan_dispatch_order(
        platform_queues={
            "autohome": [
                QueuedRun("run_a", "task_autohome", "comparison", waited_seconds=100),
            ],
            "dongchedi": [
                QueuedRun("run_b", "task_dcd", "comparison", waited_seconds=90),
            ],
        },
        agent_capacity={"autohome": 0},
        active_lanes_by_task={},
        single_task_priority_weight=0.1,
    )

    assert order == []


def test_dispatch_decision_uses_platform_queue_key_when_run_platform_is_empty() -> None:
    order = plan_dispatch_order(
        platform_queues={
            "autohome": [
                QueuedRun("run_a", "task_autohome", "comparison", waited_seconds=100),
            ],
        },
        agent_capacity={"autohome": 1},
        active_lanes_by_task={},
        single_task_priority_weight=0.1,
    )

    assert order == [
        order[0].__class__("run_a", "task_autohome", "autohome"),
    ]
