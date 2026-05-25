from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_task_store import create_schema, seed_task
from worker_app.task_store import TaskStore
from worker_app.temporal_workflows import SingleVehicleTaskWorkflow, run_single_vehicle_task


@dataclass
class FakeEvent:
    event_type: str


@dataclass
class FakeTask:
    status: str = "queued"
    degraded: bool = False
    upgraded_to_full: bool = False
    events: list[FakeEvent] = field(default_factory=list)


class FakeActivityRunner:
    def __init__(self, task: FakeTask, responses: dict[str, list[Any]]) -> None:
        self.task = task
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, name: str, payload: Any, timeout: Any) -> dict[str, Any]:
        self.calls.append((name, payload))
        if name == "publish_degraded_result":
            self.task.status = "completed_degraded"
            self.task.degraded = True
            self.task.events.append(FakeEvent("degraded_published"))
        elif name == "publish_full_result":
            self.task.status = "completed"
            if self.task.degraded:
                self.task.upgraded_to_full = True
                self.task.events.append(FakeEvent("upgraded_to_full"))
            self.task.events.append(FakeEvent("full_result_published"))
        elif name == "mark_task_failed":
            self.task.status = "failed"
            self.task.events.append(FakeEvent("task_failed"))
        elif name == "schedule_retry":
            self.task.events.append(FakeEvent("retry_scheduled"))
        elif name == "cancel_task":
            self.task.status = "cancelled"
            self.task.events.append(FakeEvent("task_cancelled"))

        values = self.responses.get(name)
        if not values:
            return {}
        value = values.pop(0)
        if callable(value):
            return value(payload)
        return value

    def call_names(self) -> list[str]:
        return [name for name, _payload in self.calls]


def run_workflow(runner: FakeActivityRunner) -> dict[str, Any]:
    return asyncio.run(run_single_vehicle_task("task_1", runner))


def task_payload() -> dict[str, Any]:
    return {
        "task_id": "task_1",
        "task_type": "single_vehicle",
        "status": "queued",
        "collection_mode": "incremental",
        "vehicles": [
            {
                "id": 1,
                "position": 1,
                "query": "测试车",
                "model_name": "测试车",
                "autohome_series_id": "8089",
                "dcd_series_id": "25398",
            }
        ],
    }


def resolved_inputs() -> dict[str, Any]:
    return {
        "ok": True,
        "platforms": {
            "autohome": {
                "task_id": "task_1",
                "platform": "autohome",
                "query_key": "测试车",
                "model_name": "测试车",
                "series_id": "8089",
                "mode": "incremental",
            },
            "dongchedi": {
                "task_id": "task_1",
                "platform": "dongchedi",
                "query_key": "测试车",
                "model_name": "测试车",
                "series_id": "25398",
                "mode": "incremental",
            },
        },
        "failed_platforms": [],
    }


def test_both_platforms_succeed_completes_full_result() -> None:
    task = FakeTask()
    runner = FakeActivityRunner(
        task,
        {
            "load_task": [task_payload()],
            "resolve_vehicle_inputs": [resolved_inputs()],
            "create_or_join_collection_run": [{"run_id": "run_ah"}, {"run_id": "run_dcd"}],
            "wait_for_collection_runs": [
                {
                    "successful_platforms": ["autohome", "dongchedi"],
                    "failed_platforms": [],
                    "runs": {"autohome": {"run_id": "run_ah"}, "dongchedi": {"run_id": "run_dcd"}},
                }
            ],
            "import_run_rows_to_corpus": [{"imported_rows": 24}],
            "export_vehicle_workbooks": [{"artifact_paths": ["/tmp/raw.xlsx"]}],
            "run_postprocess": [{"artifact_paths": ["/tmp/post.xlsx"]}],
            "run_llm_report": [{"artifact_paths": ["/tmp/report.json"]}],
            "publish_full_result": [{"status": "completed"}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "completed"}
    assert task.status == "completed"
    assert task.degraded is False
    assert task.upgraded_to_full is False
    assert runner.call_names() == [
        "load_task",
        "resolve_vehicle_inputs",
        "create_or_join_collection_run",
        "create_or_join_collection_run",
        "wait_for_collection_runs",
        "import_run_rows_to_corpus",
        "export_vehicle_workbooks",
        "run_postprocess",
        "run_llm_report",
        "publish_full_result",
    ]


def test_one_retryable_platform_failure_publishes_degraded_and_schedules_retry() -> None:
    task = FakeTask()
    runner = FakeActivityRunner(
        task,
        {
            "load_task": [task_payload()],
            "resolve_vehicle_inputs": [resolved_inputs()],
            "create_or_join_collection_run": [{"run_id": "run_ah"}, {"run_id": "run_dcd"}],
            "wait_for_collection_runs": [
                {
                    "successful_platforms": ["autohome"],
                    "failed_platforms": [
                        {"platform": "dongchedi", "run_id": "run_dcd", "failure_category": "timeout", "retryable": True}
                    ],
                }
            ],
            "publish_degraded_result": [{"status": "completed_degraded"}],
            "schedule_retry": [{"scheduled": True}],
            "retry_failed_platforms": [
                {
                    "successful_platforms": [],
                    "failed_platforms": [
                        {"platform": "dongchedi", "run_id": "run_dcd", "failure_category": "timeout", "retryable": True}
                    ],
                }
            ],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "completed_degraded"}
    assert task.status in {"completed", "completed_degraded"}
    assert task.degraded is True
    assert task.upgraded_to_full is False
    assert any(event.event_type == "degraded_published" for event in task.events)
    assert any(event.event_type == "retry_scheduled" for event in task.events)
    assert "publish_full_result" not in runner.call_names()


def test_failed_platform_retry_success_upgrades_task_to_full() -> None:
    task = FakeTask()
    runner = FakeActivityRunner(
        task,
        {
            "load_task": [task_payload()],
            "resolve_vehicle_inputs": [resolved_inputs()],
            "create_or_join_collection_run": [{"run_id": "run_ah"}, {"run_id": "run_dcd"}],
            "wait_for_collection_runs": [
                {
                    "successful_platforms": ["autohome"],
                    "failed_platforms": [
                        {"platform": "dongchedi", "run_id": "run_dcd", "failure_category": "timeout", "retryable": True}
                    ],
                }
            ],
            "publish_degraded_result": [{"status": "completed_degraded"}],
            "schedule_retry": [{"scheduled": True}],
            "retry_failed_platforms": [
                {
                    "successful_platforms": ["dongchedi"],
                    "failed_platforms": [],
                    "runs": {"dongchedi": {"run_id": "run_dcd_retry"}},
                }
            ],
            "import_run_rows_to_corpus": [{"imported_rows": 30}],
            "export_vehicle_workbooks": [{"artifact_paths": ["/tmp/raw.xlsx"]}],
            "run_postprocess": [{"artifact_paths": ["/tmp/post.xlsx"]}],
            "run_llm_report": [{"artifact_paths": ["/tmp/report.json"]}],
            "publish_full_result": [{"status": "completed"}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "completed"}
    assert task.status == "completed"
    assert task.degraded is True
    assert task.upgraded_to_full is True
    assert any(event.event_type == "degraded_published" for event in task.events)
    assert any(event.event_type == "upgraded_to_full" for event in task.events)


def test_all_platforms_failed_marks_task_failed() -> None:
    task = FakeTask()
    runner = FakeActivityRunner(
        task,
        {
            "load_task": [task_payload()],
            "resolve_vehicle_inputs": [resolved_inputs()],
            "create_or_join_collection_run": [{"run_id": "run_ah"}, {"run_id": "run_dcd"}],
            "wait_for_collection_runs": [
                {
                    "successful_platforms": [],
                    "failed_platforms": [
                        {"platform": "autohome", "run_id": "run_ah", "failure_category": "timeout", "retryable": True},
                        {"platform": "dongchedi", "run_id": "run_dcd", "failure_category": "timeout", "retryable": True},
                    ],
                }
            ],
            "mark_task_failed": [{"status": "failed"}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "failed"}
    assert task.status == "failed"
    assert any(event.event_type == "task_failed" for event in task.events)


def test_pending_with_one_success_times_out_to_degraded_without_full_publish() -> None:
    task = FakeTask()
    runner = FakeActivityRunner(
        task,
        {
            "load_task": [task_payload()],
            "resolve_vehicle_inputs": [resolved_inputs()],
            "create_or_join_collection_run": [{"run_id": "run_ah"}, {"run_id": "run_dcd"}],
            "wait_for_collection_runs": [
                {
                    "successful_platforms": ["autohome"],
                    "failed_platforms": [],
                    "pending_platforms": ["dongchedi"],
                    "runs": {"autohome": {"run_id": "run_ah"}, "dongchedi": {"run_id": "run_dcd"}},
                }
            ],
            "publish_degraded_result": [{"status": "completed_degraded"}],
            "schedule_retry": [{"scheduled": True}],
            "retry_failed_platforms": [
                {
                    "successful_platforms": [],
                    "failed_platforms": [
                        {
                            "platform": "dongchedi",
                            "run_id": "run_dcd",
                            "failure_category": "collector_pending_timeout",
                            "retryable": True,
                        }
                    ],
                }
            ],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "completed_degraded"}
    assert any(event.event_type == "degraded_published" for event in task.events)
    assert any(event.event_type == "retry_scheduled" for event in task.events)
    assert "publish_full_result" not in runner.call_names()
    degraded_payload = dict(runner.calls)["publish_degraded_result"]
    assert degraded_payload["results"]["failed_platforms"] == [
        {
            "platform": "dongchedi",
            "run_id": "run_dcd",
            "failure_category": "collector_pending_timeout",
            "retryable": True,
        }
    ]


def test_all_pending_marks_task_failed_instead_of_publishing_full() -> None:
    task = FakeTask()
    runner = FakeActivityRunner(
        task,
        {
            "load_task": [task_payload()],
            "resolve_vehicle_inputs": [resolved_inputs()],
            "create_or_join_collection_run": [{"run_id": "run_ah"}, {"run_id": "run_dcd"}],
            "wait_for_collection_runs": [
                {
                    "successful_platforms": [],
                    "failed_platforms": [],
                    "pending_platforms": ["autohome", "dongchedi"],
                    "runs": {"autohome": {"run_id": "run_ah"}, "dongchedi": {"run_id": "run_dcd"}},
                }
            ],
            "mark_task_failed": [{"status": "failed"}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "failed"}
    assert any(event.event_type == "task_failed" for event in task.events)
    assert "publish_full_result" not in runner.call_names()
    failure_payload = dict(runner.calls)["mark_task_failed"]
    assert failure_payload["results"]["failed_platforms"] == [
        {
            "platform": "autohome",
            "run_id": "run_ah",
            "failure_category": "collector_pending_timeout",
            "retryable": True,
        },
        {
            "platform": "dongchedi",
            "run_id": "run_dcd",
            "failure_category": "collector_pending_timeout",
            "retryable": True,
        },
    ]


def test_cancellation_orchestration_calls_cancel_task_before_publish() -> None:
    task = FakeTask()
    cancel_requested = False

    def request_cancel_after_wait(payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal cancel_requested
        cancel_requested = True
        return {
            "successful_platforms": ["autohome", "dongchedi"],
            "failed_platforms": [],
            "runs": {"autohome": {"run_id": "run_ah"}, "dongchedi": {"run_id": "run_dcd"}},
        }

    runner = FakeActivityRunner(
        task,
        {
            "load_task": [task_payload()],
            "resolve_vehicle_inputs": [resolved_inputs()],
            "create_or_join_collection_run": [{"run_id": "run_ah"}, {"run_id": "run_dcd"}],
            "wait_for_collection_runs": [request_cancel_after_wait],
            "cancel_task": [{"status": "cancelled"}],
        },
    )

    result = asyncio.run(
        run_single_vehicle_task("task_1", runner, cancel_requested=lambda: cancel_requested)
    )

    assert result == {"task_id": "task_1", "status": "cancelled"}
    assert task.status == "cancelled"
    assert "cancel_task" in runner.call_names()
    assert "publish_full_result" not in runner.call_names()
    cancel_payload = dict(runner.calls)["cancel_task"]
    assert cancel_payload == {"task_id": "task_1"}


def test_single_vehicle_workflow_exposes_cancel_signal() -> None:
    assert hasattr(SingleVehicleTaskWorkflow, "request_cancel")


def test_cancellation_detaches_task_and_cancels_run_only_when_no_task_remains(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_task(db_path, "task_2")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )
    store.attach_task_to_run("task_2", run.run_id)

    first = store.cancel_task_and_detach_runs("task_1")
    second = store.cancel_task_and_detach_runs("task_2")

    connection = sqlite3.connect(db_path)
    try:
        run_row = connection.execute(
            "SELECT status, shared_by_task_ids FROM collection_runs WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
        task_rows = connection.execute(
            "SELECT task_id, status, current_stage FROM tasks ORDER BY task_id ASC",
        ).fetchall()
        event_rows = connection.execute(
            "SELECT task_id, event_type, payload_json FROM task_events ORDER BY id ASC",
        ).fetchall()
    finally:
        connection.close()

    assert first["cancelled_run_ids"] == []
    assert first["detached_runs"] == [{"run_id": run.run_id, "remaining_task_ids": ["task_2"], "cancelled": False}]
    assert second["cancelled_run_ids"] == [run.run_id]
    assert run_row == ("cancelled", "[]")
    assert task_rows == [
        ("task_1", "cancelled", "cancelled"),
        ("task_2", "cancelled", "cancelled"),
    ]
    assert [(task_id, event_type) for task_id, event_type, _payload in event_rows] == [
        ("task_1", "task_cancelled"),
        ("task_2", "task_cancelled"),
    ]
    assert json.loads(event_rows[-1][2])["cancelled_run_ids"] == [run.run_id]


def test_publish_degraded_then_full_updates_task_flags_and_events(tmp_path: Path) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")

    store.publish_degraded_result("task_1", {"successful_platforms": ["autohome"]})
    store.publish_full_result("task_1", {"successful_platforms": ["autohome", "dongchedi"]})

    connection = sqlite3.connect(db_path)
    try:
        task_row = connection.execute(
            "SELECT status, current_stage, degraded, upgraded_to_full FROM tasks WHERE task_id = ?",
            ("task_1",),
        ).fetchone()
        event_rows = connection.execute(
            "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY id ASC",
            ("task_1",),
        ).fetchall()
    finally:
        connection.close()

    assert task_row == ("completed", "completed", 1, 1)
    assert [row[0] for row in event_rows] == [
        "degraded_published",
        "upgraded_to_full",
        "full_result_published",
    ]
