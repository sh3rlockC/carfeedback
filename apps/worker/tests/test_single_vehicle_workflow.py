from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_task_store import create_schema, seed_task, seed_vehicle
from worker_app.corpus import upsert_platform_rows
from worker_app.task_store import TaskStore
from worker_app.temporal_activities import (
    DEFAULT_COLLECTOR_WAIT_POLL_SECONDS,
    DEFAULT_COLLECTOR_WAIT_TIMEOUT_SECONDS,
    TaskActivities,
)
from worker_app.temporal_workflows import SingleVehicleTaskWorkflow, run_single_vehicle_task
import worker_app.temporal_activities as temporal_activities


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


def task_payload(*, enabled_platforms: list[str] | None = None, dcd_series_id: str | None = "25398") -> dict[str, Any]:
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
                "dcd_series_id": dcd_series_id,
                "enabled_platforms": enabled_platforms or ["autohome", "dongchedi"],
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


def test_resolve_vehicle_inputs_ignores_user_disabled_platform() -> None:
    payload = task_payload(enabled_platforms=["autohome"], dcd_series_id=None)

    result = asyncio.run(
        TaskActivities().resolve_vehicle_inputs(
            {
                "task_id": "task_1",
                "vehicles": payload["vehicles"],
                "mode": "incremental",
            }
        )
    )

    assert result["ok"] is True
    assert set(result["platforms"]) == {"autohome"}
    assert result["failed_platforms"] == []
    assert result["enabled_platforms"] == ["autohome"]


def test_single_platform_task_publishes_degraded_without_retrying_skipped_platform() -> None:
    task = FakeTask()
    runner = FakeActivityRunner(
        task,
        {
            "load_task": [task_payload(enabled_platforms=["autohome"], dcd_series_id=None)],
            "resolve_vehicle_inputs": [
                {
                    "ok": True,
                    "platforms": {
                        "autohome": {
                            "task_id": "task_1",
                            "platform": "autohome",
                            "query_key": "测试车",
                            "model_name": "测试车",
                            "series_id": "8089",
                            "mode": "incremental",
                        }
                    },
                    "failed_platforms": [],
                    "enabled_platforms": ["autohome"],
                }
            ],
            "create_or_join_collection_run": [{"run_id": "run_ah"}],
            "wait_for_collection_runs": [
                {
                    "successful_platforms": ["autohome"],
                    "failed_platforms": [],
                    "runs": {"autohome": {"run_id": "run_ah"}},
                }
            ],
            "import_run_rows_to_corpus": [{"imported_rows": 12}],
            "export_vehicle_workbooks": [{"artifact_paths": ["/tmp/raw.xlsx"]}],
            "run_postprocess": [{"artifact_paths": ["/tmp/analysis_facts.jsonl"], "skipped": False}],
            "run_llm_report": [{"artifact_paths": ["/tmp/final_report.json"], "skipped": False}],
            "publish_degraded_result": [{"status": "completed_degraded"}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "completed_degraded"}
    assert task.status == "completed_degraded"
    assert "schedule_retry" not in runner.call_names()
    assert "mark_task_failed" not in runner.call_names()


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
            "run_postprocess": [{"artifact_paths": ["/tmp/post.xlsx"], "skipped": False}],
            "run_llm_report": [{"artifact_paths": ["/tmp/report.json"], "skipped": False}],
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


def test_default_collector_wait_timeout_is_below_activity_timeout() -> None:
    assert DEFAULT_COLLECTOR_WAIT_TIMEOUT_SECONDS < 45 * 60
    assert DEFAULT_COLLECTOR_WAIT_POLL_SECONDS > 0


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
            "import_run_rows_to_corpus": [{"imported_rows": 12}],
            "export_vehicle_workbooks": [{"artifact_paths": ["/tmp/raw.xlsx"]}],
            "run_postprocess": [{"artifact_paths": ["/tmp/analysis_facts.jsonl"], "skipped": False}],
            "run_llm_report": [{"artifact_paths": ["/tmp/final_report.json"], "skipped": False}],
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
    assert runner.call_names().index("run_llm_report") < runner.call_names().index("publish_degraded_result")
    degraded_payload = dict(runner.calls)["publish_degraded_result"]
    assert degraded_payload["postprocess_result"]["artifact_paths"] == ["/tmp/analysis_facts.jsonl"]
    assert degraded_payload["report_result"]["artifact_paths"] == ["/tmp/final_report.json"]


def test_retry_paused_skips_automatic_retry_after_degraded_publish() -> None:
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
            "import_run_rows_to_corpus": [{"imported_rows": 12}],
            "export_vehicle_workbooks": [{"artifact_paths": ["/tmp/raw.xlsx"]}],
            "run_postprocess": [{"artifact_paths": ["/tmp/analysis_facts.jsonl"], "skipped": False}],
            "run_llm_report": [{"artifact_paths": ["/tmp/final_report.json"], "skipped": False}],
            "publish_degraded_result": [{"status": "completed_degraded"}],
            "is_retry_paused": [{"paused": True}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "completed_degraded"}
    assert "is_retry_paused" in runner.call_names()
    assert "schedule_retry" not in runner.call_names()
    assert "retry_failed_platforms" not in runner.call_names()


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
            "import_run_rows_to_corpus": [{"imported_rows": 12}, {"imported_rows": 30}],
            "export_vehicle_workbooks": [{"artifact_paths": ["/tmp/degraded_raw.xlsx"]}, {"artifact_paths": ["/tmp/raw.xlsx"]}],
            "run_postprocess": [
                {"artifact_paths": ["/tmp/degraded_analysis_facts.jsonl"], "skipped": False},
                {"artifact_paths": ["/tmp/post.xlsx"], "skipped": False},
            ],
            "run_llm_report": [
                {"artifact_paths": ["/tmp/degraded_final_report.json"], "skipped": False},
                {"artifact_paths": ["/tmp/report.json"], "skipped": False},
            ],
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


def test_all_platforms_failed_without_history_marks_task_failed() -> None:
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
            "import_run_rows_to_corpus": [{"imported_rows": 0}],
            "export_vehicle_workbooks": [{"artifact_paths": [], "task_raw_paths": [], "report_platforms": []}],
            "run_postprocess": [{"artifact_paths": [], "skipped": True}],
            "run_llm_report": [{"artifact_paths": [], "skipped": True}],
            "mark_task_failed": [{"status": "failed"}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "failed"}
    assert task.status == "failed"
    assert any(event.event_type == "task_failed" for event in task.events)


def test_all_platforms_failed_with_history_publishes_degraded_result() -> None:
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
                    "runs": {"autohome": {"run_id": "run_ah"}, "dongchedi": {"run_id": "run_dcd"}},
                }
            ],
            "import_run_rows_to_corpus": [{"imported_rows": 0}],
            "export_vehicle_workbooks": [
                {
                    "artifact_paths": ["/tmp/autohome.xlsx", "/tmp/dcd.xlsx"],
                    "task_raw_paths": ["/tmp/autohome.xlsx", "/tmp/dcd.xlsx"],
                    "report_platforms": ["autohome", "dongchedi"],
                    "platform_statuses": {
                        "autohome": {"status": "historical_unchecked", "label": "未查新增"},
                        "dongchedi": {"status": "historical_unchecked", "label": "未查新增"},
                    },
                }
            ],
            "run_postprocess": [{"artifact_paths": ["/tmp/analysis_facts.jsonl"], "skipped": False}],
            "run_llm_report": [{"artifact_paths": ["/tmp/final_report.json"], "skipped": False}],
            "publish_degraded_result": [{"status": "completed_degraded"}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "completed_degraded"}
    assert task.status == "completed_degraded"
    assert "mark_task_failed" not in runner.call_names()


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
            "import_run_rows_to_corpus": [{"imported_rows": 12}],
            "export_vehicle_workbooks": [{"artifact_paths": ["/tmp/raw.xlsx"]}],
            "run_postprocess": [{"artifact_paths": ["/tmp/analysis_facts.jsonl"], "skipped": False}],
            "run_llm_report": [{"artifact_paths": ["/tmp/final_report.json"], "skipped": False}],
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


def test_partial_success_missing_platform_result_degrades_instead_of_full_publish() -> None:
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
                    "pending_platforms": [],
                    "runs": {"autohome": {"run_id": "run_ah"}, "dongchedi": {"run_id": "run_dcd"}},
                }
            ],
            "import_run_rows_to_corpus": [{"imported_rows": 12}],
            "export_vehicle_workbooks": [{"artifact_paths": ["/tmp/raw.xlsx"]}],
            "run_postprocess": [{"artifact_paths": ["/tmp/analysis_facts.jsonl"], "skipped": False}],
            "run_llm_report": [{"artifact_paths": ["/tmp/final_report.json"], "skipped": False}],
            "publish_degraded_result": [{"status": "completed_degraded"}],
            "schedule_retry": [{"scheduled": True}],
            "retry_failed_platforms": [
                {
                    "successful_platforms": [],
                    "failed_platforms": [
                        {
                            "platform": "dongchedi",
                            "run_id": "run_dcd",
                            "failure_category": "collector_missing_result",
                            "retryable": True,
                        }
                    ],
                }
            ],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "completed_degraded"}
    assert "publish_full_result" not in runner.call_names()
    degraded_payload = dict(runner.calls)["publish_degraded_result"]
    assert degraded_payload["results"]["failed_platforms"] == [
        {
            "platform": "dongchedi",
            "run_id": "run_dcd",
            "failure_category": "collector_missing_result",
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
        {
            "platform": "postprocess_or_report",
            "failure_category": "postprocess_or_report_deferred",
            "retryable": False,
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


def test_skipped_postprocess_prevents_full_publish_and_marks_failed() -> None:
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
            "run_postprocess": [{"artifact_paths": [], "skipped": True}],
            "run_llm_report": [{"artifact_paths": [], "skipped": True}],
            "mark_task_failed": [{"status": "failed"}],
        },
    )

    result = run_workflow(runner)

    assert result == {"task_id": "task_1", "status": "failed"}
    assert "publish_full_result" not in runner.call_names()
    failure_payload = dict(runner.calls)["mark_task_failed"]
    assert failure_payload["results"]["failed_platforms"] == [
        {
            "platform": "postprocess_or_report",
            "failure_category": "postprocess_or_report_deferred",
            "retryable": False,
        }
    ]


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


def test_wait_for_collection_runs_polls_until_terminal_before_returning_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    autohome = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )
    dongchedi = store.create_or_join_collection_run(
        platform="dongchedi",
        query_key="测试车",
        model_name="测试车",
        series_id="25398",
        mode="incremental",
        task_id="task_1",
    )
    sleep_calls: list[float] = []

    async def complete_runs_after_first_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                "UPDATE collection_runs SET status = 'completed' WHERE run_id IN (?, ?)",
                (autohome.run_id, dongchedi.run_id),
            )
            connection.commit()
        finally:
            connection.close()

    monkeypatch.setenv("COLLECTOR_WAIT_POLL_SECONDS", "1")
    monkeypatch.setenv("COLLECTOR_WAIT_TIMEOUT_SECONDS", "30")
    async def no_dispatch(self, task_id, run_ids) -> None:
        return None

    monkeypatch.setattr(TaskActivities, "_dispatch_pending_collection_runs", no_dispatch)
    monkeypatch.setattr(
        temporal_activities,
        "asyncio",
        type("FakeAsyncio", (), {"sleep": complete_runs_after_first_sleep}),
        raising=False,
    )

    activity = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")
    result = asyncio.run(
        activity.wait_for_collection_runs(
            {"task_id": "task_1", "run_ids": [autohome.run_id, dongchedi.run_id]}
        )
    )

    assert sleep_calls == [1.0]
    assert result["successful_platforms"] == ["autohome", "dongchedi"]
    assert result["pending_platforms"] == []


def test_wait_for_collection_runs_dispatches_queued_runs_before_returning(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )
    dispatched: list[str] = []

    def fake_submit(self, started_run, *, owner_task_id: str | None = None) -> None:
        asyncio.run(asyncio.sleep(0))
        dispatched.append(started_run.run_id)
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                "UPDATE collection_runs SET status = 'succeeded', output_path = ? WHERE run_id = ?",
                (str(tmp_path / "run-output.xlsx"), started_run.run_id),
            )
            connection.commit()
        finally:
            connection.close()

    monkeypatch.setattr(TaskActivities, "_submit_collection_run", fake_submit, raising=False)
    monkeypatch.setenv("COLLECTOR_WAIT_TIMEOUT_SECONDS", "0")
    activity = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    result = asyncio.run(activity.wait_for_collection_runs({"task_id": "task_1", "run_ids": [run.run_id]}))

    connection = sqlite3.connect(db_path)
    try:
        task_row = connection.execute(
            "SELECT status, current_stage FROM tasks WHERE task_id = ?",
            ("task_1",),
        ).fetchone()
    finally:
        connection.close()

    assert dispatched == [run.run_id]
    assert result["successful_platforms"] == ["autohome"]
    assert result["pending_platforms"] == []
    assert task_row == ("running", "running")


def test_wait_for_collection_runs_timeout_fails_pending_runs_and_releases_agent(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )
    claimed = store.claim_collection_run_with_agent(run.run_id, "autohome-1")
    assert claimed is not None

    async def no_dispatch(self, task_id, run_ids) -> None:
        return None

    async def no_poll(self, task_id, run_ids) -> None:
        return None

    monkeypatch.setattr(TaskActivities, "_dispatch_pending_collection_runs", no_dispatch)
    monkeypatch.setattr(TaskActivities, "_poll_running_collection_runs", no_poll)
    monkeypatch.setenv("COLLECTOR_WAIT_TIMEOUT_SECONDS", "0")
    activity = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    result = asyncio.run(activity.wait_for_collection_runs({"task_id": "task_1", "run_ids": [run.run_id]}))

    reloaded = store.load_collection_run(run.run_id)
    assert result["pending_platforms"] == []
    assert result["failed_platforms"] == [
        {
            "platform": "autohome",
            "run_id": run.run_id,
            "failure_category": "collector_pending_timeout",
            "retryable": True,
        }
    ]
    assert reloaded.status == "failed"
    assert reloaded.failure_category == "collector_pending_timeout"
    assert store.running_agent_ids_by_platform() == {}


def test_wait_for_collection_runs_fails_stalled_progress_and_releases_agent(tmp_path: Path, monkeypatch) -> None:
    from worker_app.collector_models import CollectorRunStatus

    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="full_refresh",
        task_id="task_1",
    )
    claimed = store.claim_collection_run_with_agent(run.run_id, "autohome-1")
    assert claimed is not None

    class FakeCollectorClient:
        cancelled_run_ids: list[str] = []

        def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
            assert base_url == "http://collector.test"

        def get_run(self, run_id: str):
            return CollectorRunStatus(
                run_id=run_id,
                platform="autohome",
                status="running",
                progress_current=398,
                progress_total=1000,
            )

        def cancel_run(self, run_id: str):
            self.cancelled_run_ids.append(run_id)
            return CollectorRunStatus(
                run_id=run_id,
                platform="autohome",
                status="cancel_requested",
                progress_current=398,
                progress_total=1000,
            )

    monotonic_values = iter([0.0, 0.0, 4.0])

    def fake_monotonic() -> float:
        return next(monotonic_values, 4.0)

    monkeypatch.setenv("AUTOHOME_COLLECTOR_SERVICE_URL", "http://collector.test")
    monkeypatch.setenv("COLLECTOR_WAIT_TIMEOUT_SECONDS", "4")
    monkeypatch.setenv("COLLECTOR_WAIT_POLL_SECONDS", "0")
    monkeypatch.setenv("COLLECTOR_PROGRESS_STALL_TIMEOUT_SECONDS", "3")
    monkeypatch.setattr(temporal_activities, "CollectorClient", FakeCollectorClient)
    monkeypatch.setattr(temporal_activities, "_monotonic", fake_monotonic)
    activity = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    result = asyncio.run(activity.wait_for_collection_runs({"task_id": "task_1", "run_ids": [run.run_id]}))

    reloaded = store.load_collection_run(run.run_id)
    assert result["pending_platforms"] == []
    assert result["failed_platforms"] == [
        {
            "platform": "autohome",
            "run_id": run.run_id,
            "failure_category": "collector_progress_stalled",
            "retryable": True,
        }
    ]
    assert reloaded.status == "failed"
    assert reloaded.failure_category == "collector_progress_stalled"
    assert FakeCollectorClient.cancelled_run_ids == [run.run_id]
    assert store.running_agent_ids_by_platform() == {}

    connection = sqlite3.connect(db_path)
    try:
        event = connection.execute(
            "SELECT payload_json FROM collector_events WHERE run_id = ? AND event_type = 'collector_progress_stalled'",
            (run.run_id,),
        ).fetchone()
    finally:
        connection.close()
    assert event is not None
    payload = json.loads(event[0])
    assert payload["progress_current"] == 398
    assert payload["cancel_requested"] is True


def test_wait_for_collection_runs_submits_and_polls_collector_service(tmp_path: Path, monkeypatch) -> None:
    from worker_app.collector_models import CollectorRunStatus

    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    store = TaskStore(f"sqlite+pysqlite:///{db_path}")
    run = store.create_or_join_collection_run(
        platform="autohome",
        query_key="测试车",
        model_name="测试车",
        series_id="8089",
        mode="incremental",
        task_id="task_1",
    )
    output_path = tmp_path / "ZJ测试车原始口碑.xlsx"
    submitted: list[dict[str, Any]] = []
    polled: list[str] = []

    class FakeCollectorClient:
        def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
            assert base_url == "http://collector.test"

        def submit_run(self, request):
            submitted.append(request.model_dump(exclude_none=True))
            return CollectorRunStatus(
                run_id=request.run_id,
                platform=request.platform,
                status="running",
                progress_current=0,
                progress_total=1,
            )

        def get_run(self, run_id: str):
            polled.append(run_id)
            return CollectorRunStatus(
                run_id=run_id,
                platform="autohome",
                status="succeeded",
                progress_current=1,
                progress_total=1,
                output_path=str(output_path),
                resume_cursor={"page": 1},
            )

    monkeypatch.setenv("AUTOHOME_COLLECTOR_SERVICE_URL", "http://collector.test")
    monkeypatch.setenv("COLLECTOR_WAIT_TIMEOUT_SECONDS", "0")
    monkeypatch.setattr(temporal_activities, "CollectorClient", FakeCollectorClient)
    activity = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    result = asyncio.run(activity.wait_for_collection_runs({"task_id": "task_1", "run_ids": [run.run_id]}))

    assert submitted == [
        {
            "run_id": run.run_id,
            "task_id": "task_1",
            "platform": "autohome",
            "query_key": "测试车",
            "model_name": "测试车",
            "series_id": "8089",
            "mode": "incremental",
            "known_links": [],
            "resume_cursor": {},
            "max_scan_pages": 10,
            "stop_after_known_pages": 2,
        }
    ]
    assert polled == [run.run_id]
    assert result["successful_platforms"] == ["autohome"]
    assert result["pending_platforms"] == []
    assert result["runs"]["autohome"]["output_path"] == str(output_path)

    connection = sqlite3.connect(db_path)
    try:
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM collector_events WHERE run_id = ? ORDER BY id",
                (run.run_id,),
            ).fetchall()
        ]
    finally:
        connection.close()
    assert event_types == ["collector_submitted", "collector_status_polled", "collector_succeeded"]


def test_publish_full_result_creates_task_center_download_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_vehicle(db_path, task_id="task_1", position=1, query="测试车", model_name="测试车")
    database_url = f"sqlite+pysqlite:///{db_path}"

    upsert_platform_rows(
        database_url=database_url,
        query="测试车",
        model_name="测试车",
        platform="autohome",
        series_id="8089",
        job_id="task_1",
        rows=[{"用户名": "车主A", "发表日期": "2026-05-01", "评价详情": "汽车之家评论", "来源链接": "https://a.example/1"}],
    )
    upsert_platform_rows(
        database_url=database_url,
        query="测试车",
        model_name="测试车",
        platform="dongchedi",
        series_id="25398",
        job_id="task_1",
        rows=[{"用户名": "车主B", "发布时间": "2026-05-02", "评价全文": "懂车帝评论", "来源链接": "https://d.example/1"}],
    )

    final_report = tmp_path / "ai" / "final_report.json"
    analysis_facts = tmp_path / "ai" / "analysis_facts.jsonl"
    summary = tmp_path / "summary" / "测试车_双平台口碑摘要.xlsx"
    final_report.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    final_report.write_text('{"headline":"测试车口碑摘要"}', encoding="utf-8")
    analysis_facts.write_text('{"fact":"ok"}\n', encoding="utf-8")
    summary.write_text("summary", encoding="utf-8")

    activity = TaskActivities(database_url=database_url)
    result = asyncio.run(
        activity.publish_full_result(
            {
                "task_id": "task_1",
                "task": {
                    "vehicles": [
                        {
                            "id": 1,
                            "position": 1,
                            "query": "测试车",
                            "model_name": "测试车",
                            "autohome_series_id": "8089",
                            "dcd_series_id": "25398",
                            "enabled_platforms": ["autohome", "dongchedi"],
                        }
                    ]
                },
                "results": {"successful_platforms": ["autohome", "dongchedi"], "failed_platforms": []},
                "postprocess_result": {"artifact_paths": [str(analysis_facts)]},
                "report_result": {"artifact_paths": [str(summary), str(final_report)]},
            }
        )
    )

    assert result == {"task_id": "task_1", "status": "completed"}

    connection = sqlite3.connect(db_path)
    try:
        artifact_rows = connection.execute(
            "SELECT artifact_type, path, downloadable FROM task_artifacts WHERE task_id = ? ORDER BY artifact_type, path",
            ("task_1",),
        ).fetchall()
    finally:
        connection.close()

    artifact_types = [row[0] for row in artifact_rows]
    assert "business_zip" in artifact_types
    assert "merged_raw_excel" in artifact_types


def test_export_vehicle_workbooks_exposes_failed_platform_history_as_unchecked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("KOUBEI_CORPUS_ROOT", str(tmp_path / "corpus"))
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_vehicle(db_path, task_id="task_1", position=1, query="测试车", model_name="测试车")
    database_url = f"sqlite+pysqlite:///{db_path}"

    upsert_platform_rows(
        database_url=database_url,
        query="测试车",
        model_name="测试车",
        platform="autohome",
        series_id="8089",
        job_id="history_autohome",
        rows=[{"用户名": "车主A", "发表日期": "2026-05-01", "评价详情": "汽车之家历史评论", "来源链接": "https://a.example/1"}],
    )
    upsert_platform_rows(
        database_url=database_url,
        query="测试车",
        model_name="测试车",
        platform="dongchedi",
        series_id="25398",
        job_id="task_1",
        rows=[{"用户名": "车主B", "发布时间": "2026-05-02", "评价全文": "懂车帝当前评论", "来源链接": "https://d.example/1"}],
    )

    activity = TaskActivities(database_url=database_url)
    result = asyncio.run(
        activity.export_vehicle_workbooks(
            {
                "task_id": "task_1",
                "task": task_payload(),
                "results": {
                    "successful_platforms": ["dongchedi"],
                    "failed_platforms": [{"platform": "autohome", "failure_category": "collector_missing_result"}],
                },
            }
        )
    )

    task_raw_paths = result["task_raw_paths"]
    assert task_raw_paths == [
        str(tmp_path / "artifacts" / "task_1" / "outputs" / "raw" / "ZJ测试车原始口碑.xlsx"),
        str(tmp_path / "artifacts" / "task_1" / "outputs" / "raw" / "DCD口碑_测试车.xlsx"),
    ]
    assert set(result["corpus"]["platforms"]) == {"autohome", "dongchedi"}
    assert result["report_platforms"] == ["autohome", "dongchedi"]
    assert result["platform_statuses"]["autohome"]["status"] == "historical_unchecked"
    assert result["platform_statuses"]["autohome"]["label"] == "未查新增"
    assert result["platform_statuses"]["dongchedi"]["status"] == "current_collected"
    assert any("autohome/source_status.json" in path for path in result["artifact_paths"])
    assert any("dongchedi/source_status.json" in path for path in result["artifact_paths"])


def test_run_postprocess_marks_task_stage_before_running(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_vehicle(db_path, task_id="task_1", position=1, query="测试车", model_name="测试车")
    activity = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    monkeypatch.setenv("TEMPORAL_REAL_SINGLE_TASK_PIPELINE_ENABLED", "true")
    monkeypatch.setattr(
        TaskActivities,
        "_build_stage_command",
        lambda self, **kwargs: (object(), None),
        raising=False,
    )
    monkeypatch.setattr(
        TaskActivities,
        "_run_stage_command",
        lambda self, **kwargs: {"status": "success", "artifact_paths": [str(tmp_path / "post.xlsx")], "output_metadata": {}},
        raising=False,
    )

    result = asyncio.run(
        activity.run_postprocess(
            {
                "task_id": "task_1",
                "task": task_payload(),
                "results": {"successful_platforms": ["autohome", "dongchedi"]},
            }
        )
    )

    connection = sqlite3.connect(db_path)
    try:
        task_row = connection.execute(
            "SELECT status, current_stage FROM tasks WHERE task_id = ?",
            ("task_1",),
        ).fetchone()
    finally:
        connection.close()

    assert result["fallback"] is False
    assert task_row == ("running", "postprocessing")


def test_run_llm_report_marks_task_stage_before_running(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_vehicle(db_path, task_id="task_1", position=1, query="测试车", model_name="测试车")
    final_report = tmp_path / "final_report.json"
    final_report.write_text('{"headline":"测试车"}', encoding="utf-8")
    activity = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    monkeypatch.setenv("TEMPORAL_REAL_SINGLE_TASK_PIPELINE_ENABLED", "true")
    monkeypatch.setattr(
        TaskActivities,
        "_build_stage_command",
        lambda self, **kwargs: (object(), None),
        raising=False,
    )
    monkeypatch.setattr(
        TaskActivities,
        "_run_stage_command",
        lambda self, **kwargs: {"status": "success", "artifact_paths": [str(final_report)], "output_metadata": {}},
        raising=False,
    )

    result = asyncio.run(
        activity.run_llm_report(
            {
                "task_id": "task_1",
                "task": task_payload(),
                "results": {"successful_platforms": ["autohome", "dongchedi"]},
            }
        )
    )

    connection = sqlite3.connect(db_path)
    try:
        task_row = connection.execute(
            "SELECT status, current_stage FROM tasks WHERE task_id = ?",
            ("task_1",),
        ).fetchone()
    finally:
        connection.close()

    assert result["fallback"] is False
    assert task_row == ("running", "generating_hermes_outputs")


def test_run_llm_report_marks_historical_platform_as_unchecked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    db_path = tmp_path / "worker.db"
    create_schema(db_path)
    seed_task(db_path, "task_1")
    seed_vehicle(db_path, task_id="task_1", position=1, query="测试车", model_name="测试车")
    activity = TaskActivities(database_url=f"sqlite+pysqlite:///{db_path}")

    result = asyncio.run(
        activity.run_llm_report(
            {
                "task_id": "task_1",
                "task": task_payload(),
                "results": {
                    "successful_platforms": ["dongchedi"],
                    "failed_platforms": [{"platform": "autohome", "failure_category": "collector_missing_result"}],
                },
                "export_result": {
                    "report_platforms": ["autohome", "dongchedi"],
                    "platform_statuses": {
                        "autohome": {
                            "platform": "autohome",
                            "status": "historical_unchecked",
                            "label": "未查新增",
                            "row_count": 12,
                        },
                        "dongchedi": {
                            "platform": "dongchedi",
                            "status": "current_collected",
                            "label": "已查新增",
                            "row_count": 5,
                        },
                    },
                },
            }
        )
    )

    final_report_path = next(Path(path) for path in result["artifact_paths"] if str(path).endswith("final_report.json"))
    report = json.loads(final_report_path.read_text(encoding="utf-8"))

    assert report["platform_source_status"]["autohome"]["label"] == "未查新增"
    assert "未查新增" in json.dumps(report["platform_difference_blocks"], ensure_ascii=False)


def test_run_llm_report_stage_execution_does_not_block_event_loop(tmp_path: Path, monkeypatch) -> None:
    final_report = tmp_path / "final_report.json"
    final_report.write_text("{}", encoding="utf-8")
    activity = TaskActivities(database_url=None)

    monkeypatch.setenv("TEMPORAL_REAL_SINGLE_TASK_PIPELINE_ENABLED", "true")
    monkeypatch.setattr(
        TaskActivities,
        "_build_stage_command",
        lambda self, **kwargs: (object(), None),
        raising=False,
    )

    def slow_stage(self, **kwargs):
        time.sleep(0.1)
        return {"status": "success", "artifact_paths": [str(final_report)], "output_metadata": {}}

    monkeypatch.setattr(TaskActivities, "_run_stage_command", slow_stage, raising=False)

    async def scenario() -> dict[str, Any]:
        report_task = asyncio.create_task(
            activity.run_llm_report(
                {
                    "task_id": "task_1",
                    "task": task_payload(),
                    "results": {"successful_platforms": ["autohome", "dongchedi"]},
                }
            )
        )
        started_at = time.perf_counter()
        await asyncio.sleep(0.02)
        elapsed = time.perf_counter() - started_at
        assert elapsed < 0.08
        assert not report_task.done()
        return await report_task

    result = asyncio.run(scenario())

    assert result["fallback"] is False
