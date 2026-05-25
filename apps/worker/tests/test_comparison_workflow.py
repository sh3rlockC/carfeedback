from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.temporal_workflows import estimate_comparison_seconds, run_comparison_task


class FakeComparisonActivityRunner:
    def __init__(self, responses: dict[str, list[Any]]) -> None:
        self.responses = {name: list(values) for name, values in responses.items()}
        self.calls: list[tuple[str, Any]] = []

    async def __call__(self, name: str, payload: Any, timeout: Any) -> dict[str, Any]:
        self.calls.append((name, payload))
        values = self.responses.get(name)
        if not values:
            return {}
        value = values.pop(0)
        if callable(value):
            return value(payload)
        return value

    def call_names(self) -> list[str]:
        return [name for name, _payload in self.calls]

    def payloads(self, name: str) -> list[Any]:
        return [payload for call_name, payload in self.calls if call_name == name]


def run_workflow(runner: FakeComparisonActivityRunner) -> dict[str, Any]:
    return asyncio.run(run_comparison_task("cmp_1", runner))


def comparison_task() -> dict[str, Any]:
    return {
        "comparison_id": "cmp_1",
        "status": "queued",
        "start_date": None,
        "end_date": None,
        "passphrase_version": "2026-W17",
        "vehicles": [
            {"id": 1, "position": 1, "query": "测试车A", "model_name": "测试车A"},
            {"id": 2, "position": 2, "query": "测试车B", "model_name": "测试车B"},
            {
                "id": 3,
                "position": 3,
                "query": "测试车C",
                "model_name": "测试车C",
                "source_job_id": "job_reused_c",
            },
        ],
    }


def usable_vehicle(vehicle_id: int, model_name: str, *, reused: bool = False, **overrides: Any) -> dict[str, Any]:
    source_job_id = overrides.pop("source_job_id", f"job_{vehicle_id}")
    return {
        "vehicle_id": vehicle_id,
        "model_name": model_name,
        "source_job_id": source_job_id,
        "usable": True,
        "reused": reused,
        "degraded": False,
        "incomplete_sources": [],
        "labels": [],
        "snapshot": {
            "model_name": model_name,
            "source_job_id": source_job_id,
            "final_report_path": f"/tmp/{source_job_id}.final_report.json",
            "analysis_facts_path": f"/tmp/{source_job_id}.analysis_facts.jsonl",
        },
        **overrides,
    }


def test_comparison_collects_two_vehicles_and_reuses_existing_corpus_snapshot() -> None:
    def wait_result(payload: dict[str, Any]) -> dict[str, Any]:
        vehicle = payload["vehicle"]
        if vehicle["id"] == 1:
            return usable_vehicle(1, "测试车A")
        if vehicle["id"] == 2:
            return usable_vehicle(2, "测试车B")
        return usable_vehicle(3, "测试车C", reused=True, source_job_id="job_reused_c")

    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [
                {"child_workflow_id": "single_vehicle:cmp_1:1", "child_task_id": "job_1"},
                {"child_workflow_id": "single_vehicle:cmp_1:2", "child_task_id": "job_2"},
            ],
            "wait_for_vehicle_results": [wait_result, wait_result, wait_result],
            "generate_comparison_report": [
                {"report_json": {"headline": "竞品口碑对比"}, "artifact_paths": ["/tmp/final_comparison.json"]}
            ],
            "publish_comparison_result": [{"status": "completed"}],
        }
    )

    result = run_workflow(runner)

    assert result == {"comparison_id": "cmp_1", "status": "completed", "vehicle_count": 3, "excluded": []}
    ensure_payloads = runner.payloads("ensure_vehicle_subworkflow")
    assert [payload["vehicle"]["id"] for payload in ensure_payloads] == [1, 2]
    report_payload = runner.payloads("generate_comparison_report")[0]
    assert [vehicle["vehicle_id"] for vehicle in report_payload["vehicles"]] == [1, 2, 3]
    assert report_payload["vehicles"][2]["reused"] is True


def test_degraded_vehicle_enters_comparison_with_incomplete_source_label() -> None:
    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [{"child_task_id": "job_1"}, {"child_task_id": "job_2"}],
            "wait_for_vehicle_results": [
                usable_vehicle(
                    1,
                    "测试车A",
                    degraded=True,
                    incomplete_sources=["dongchedi"],
                    labels=["incomplete_source"],
                ),
                usable_vehicle(2, "测试车B"),
                usable_vehicle(3, "测试车C", reused=True, source_job_id="job_reused_c"),
            ],
            "generate_comparison_report": [{"report_json": {}, "artifact_paths": [], "degraded": True}],
            "publish_comparison_result": [{"status": "completed_degraded"}],
        }
    )

    result = run_workflow(runner)

    report_payload = runner.payloads("generate_comparison_report")[0]
    degraded_vehicle = report_payload["vehicles"][0]
    assert result["status"] == "completed_degraded"
    assert degraded_vehicle["usable"] is True
    assert degraded_vehicle["degraded"] is True
    assert degraded_vehicle["incomplete_sources"] == ["dongchedi"]
    assert "incomplete_source" in degraded_vehicle["labels"]


def test_upgraded_vehicle_regenerates_comparison_after_initial_publish() -> None:
    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [{"child_task_id": "job_1"}, {"child_task_id": "job_2"}],
            "wait_for_vehicle_results": [
                usable_vehicle(1, "测试车A", degraded=True, upgraded_to_full=True),
                usable_vehicle(2, "测试车B"),
                usable_vehicle(3, "测试车C", reused=True, source_job_id="job_reused_c"),
            ],
            "generate_comparison_report": [{"report_json": {"version": "initial"}, "artifact_paths": ["/tmp/initial.json"]}],
            "publish_comparison_result": [{"status": "completed_degraded"}],
            "regenerate_comparison_after_upgrade": [
                {"status": "completed_upgraded", "artifact_paths": ["/tmp/upgraded.json"], "upgraded": True}
            ],
        }
    )

    result = run_workflow(runner)

    assert result["status"] == "completed_upgraded"
    assert runner.call_names().index("publish_comparison_result") < runner.call_names().index(
        "regenerate_comparison_after_upgrade"
    )
    regenerate_payload = runner.payloads("regenerate_comparison_after_upgrade")[0]
    assert regenerate_payload["upgraded_vehicle_ids"] == [1]
    assert regenerate_payload["previous_report"]["report_json"] == {"version": "initial"}


def test_fewer_than_two_usable_vehicles_marks_comparison_failed() -> None:
    runner = FakeComparisonActivityRunner(
        {
            "load_comparison_task": [comparison_task()],
            "ensure_vehicle_subworkflow": [{"child_task_id": "job_1"}, {"child_task_id": "job_2"}],
            "wait_for_vehicle_results": [
                usable_vehicle(1, "测试车A"),
                {"vehicle_id": 2, "model_name": "测试车B", "usable": False, "reason": "collection_failed"},
                {"vehicle_id": 3, "model_name": "测试车C", "usable": False, "reason": "missing_snapshot"},
            ],
            "publish_comparison_result": [{"status": "failed"}],
        }
    )

    result = run_workflow(runner)

    assert result == {
        "comparison_id": "cmp_1",
        "status": "failed",
        "error_code": "insufficient_available_vehicles",
        "error_message": "竞品对比至少需要 2 个可用车型结果",
        "available_vehicle_count": 1,
        "excluded": [
            {"model_name": "测试车B", "reason": "collection_failed"},
            {"model_name": "测试车C", "reason": "missing_snapshot"},
        ],
    }
    assert "generate_comparison_report" not in runner.call_names()
    failure_payload = runner.payloads("publish_comparison_result")[0]
    assert failure_payload["status"] == "failed"
    assert failure_payload["error_code"] == "insufficient_available_vehicles"


def test_comparison_eta_uses_slowest_vehicle_path_plus_summary_time() -> None:
    assert estimate_comparison_seconds([180, 30, 75], summary_seconds=45) == 225
    assert estimate_comparison_seconds([], summary_seconds=45) == 45
