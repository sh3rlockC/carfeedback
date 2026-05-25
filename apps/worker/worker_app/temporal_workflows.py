from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from temporalio import workflow


ActivityRunner = Callable[[str, Any, timedelta], Awaitable[dict[str, Any]]]
ChildWorkflowRunner = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
CancelRequested = Callable[[], bool]
PLATFORMS = ("autohome", "dongchedi")
MAX_COMPARISON_VEHICLES = 5
MIN_COMPARISON_RESULTS = 2


def _failed_platform_name(failure: Any) -> str | None:
    if isinstance(failure, str):
        return failure
    if isinstance(failure, dict):
        platform = failure.get("platform")
        return str(platform) if platform else None
    return None


def _retryable_failures(failed_platforms: list[Any]) -> list[Any]:
    return [
        failure
        for failure in failed_platforms
        if isinstance(failure, dict) and failure.get("retryable") is True
    ]


def _successful_platforms(results: dict[str, Any]) -> set[str]:
    return {str(platform) for platform in results.get("successful_platforms", [])}


def _pending_platform_name(pending_platform: Any) -> str | None:
    if isinstance(pending_platform, str):
        return pending_platform
    if isinstance(pending_platform, dict):
        platform = pending_platform.get("platform")
        return str(platform) if platform else None
    return None


def _run_id_for_platform(results: dict[str, Any], platform: str) -> str | None:
    runs = results.get("runs") or {}
    run = runs.get(platform) if isinstance(runs, dict) else None
    if isinstance(run, dict) and run.get("run_id"):
        return str(run["run_id"])
    return None


def estimate_comparison_seconds(vehicle_estimates: list[int], summary_seconds: int) -> int:
    return max([0, *[max(0, int(value)) for value in vehicle_estimates]]) + max(0, int(summary_seconds))


def _comparison_vehicle_id(vehicle: dict[str, Any], fallback: int) -> int:
    try:
        return int(vehicle.get("id") or vehicle.get("vehicle_id") or fallback)
    except (TypeError, ValueError):
        return fallback


def _comparison_model_name(vehicle: dict[str, Any]) -> str:
    return str(vehicle.get("model_name") or vehicle.get("query") or vehicle.get("id") or "vehicle")


def _comparison_exclusion(result: dict[str, Any], vehicle: dict[str, Any]) -> dict[str, str]:
    return {
        "model_name": str(result.get("model_name") or _comparison_model_name(vehicle)),
        "reason": str(result.get("reason") or result.get("error_message") or "vehicle_result_unusable"),
    }


def _usable_comparison_result(result: dict[str, Any]) -> bool:
    if result.get("usable") is False:
        return False
    if result.get("usable") is True:
        return True
    return bool(result.get("snapshot"))


def _comparison_status(*, excluded: list[dict[str, str]], vehicles: list[dict[str, Any]], report: dict[str, Any]) -> str:
    if excluded or report.get("degraded") or any(vehicle.get("degraded") for vehicle in vehicles):
        return "completed_degraded"
    return "completed"


def _normalized_comparison_result(
    *,
    vehicle: dict[str, Any],
    result: dict[str, Any],
    reused: bool,
    fallback_position: int,
) -> dict[str, Any]:
    labels = [str(label) for label in result.get("labels", [])]
    incomplete_sources = [str(source) for source in result.get("incomplete_sources", [])]
    if incomplete_sources and "incomplete_source" not in labels:
        labels.append("incomplete_source")
    return {
        **result,
        "vehicle_id": int(result.get("vehicle_id") or _comparison_vehicle_id(vehicle, fallback_position)),
        "position": int(result.get("position") or vehicle.get("position") or fallback_position),
        "model_name": str(result.get("model_name") or _comparison_model_name(vehicle)),
        "source_job_id": result.get("source_job_id") or vehicle.get("source_job_id"),
        "reused": bool(result.get("reused", reused)),
        "degraded": bool(result.get("degraded") or incomplete_sources),
        "incomplete_sources": incomplete_sources,
        "labels": labels,
    }


def _collection_results_with_pending_timeouts(results: dict[str, Any]) -> dict[str, Any]:
    pending_platforms = list(results.get("pending_platforms") or [])
    if not pending_platforms:
        return results
    failed_platforms = list(results.get("failed_platforms") or [])
    failed_names = {_failed_platform_name(failure) for failure in failed_platforms}
    for pending_platform in pending_platforms:
        platform = _pending_platform_name(pending_platform)
        if not platform or platform in failed_names:
            continue
        failed_platforms.append(
            {
                "platform": platform,
                "run_id": _run_id_for_platform(results, platform),
                "failure_category": "collector_pending_timeout",
                "retryable": True,
            }
        )
    normalized = dict(results)
    normalized["failed_platforms"] = failed_platforms
    normalized["pending_platforms"] = []
    return normalized


def _collection_results_with_missing_failures(results: dict[str, Any]) -> dict[str, Any]:
    successful_platforms = _successful_platforms(results)
    failed_platforms = list(results.get("failed_platforms") or [])
    failed_names = {_failed_platform_name(failure) for failure in failed_platforms}
    for platform in PLATFORMS:
        if platform in successful_platforms or platform in failed_names:
            continue
        failed_platforms.append(
            {
                "platform": platform,
                "run_id": _run_id_for_platform(results, platform),
                "failure_category": "collector_missing_result",
                "retryable": True,
            }
        )
    normalized = dict(results)
    normalized["failed_platforms"] = failed_platforms
    return normalized


def _merge_retry_results(results: dict[str, Any], retry_result: dict[str, Any]) -> dict[str, Any]:
    retry_successes = _successful_platforms(retry_result)
    successful_platforms = sorted(_successful_platforms(results) | retry_successes)
    failed_platforms = [
        failure
        for failure in results.get("failed_platforms", [])
        if _failed_platform_name(failure) not in retry_successes
    ]
    failed_platforms.extend(retry_result.get("failed_platforms", []))
    runs = dict(results.get("runs") or {})
    runs.update(retry_result.get("runs") or {})
    merged = dict(results)
    merged.update(retry_result)
    merged["successful_platforms"] = successful_platforms
    merged["failed_platforms"] = failed_platforms
    merged["runs"] = runs
    return merged


async def _cancel_if_requested(
    *,
    task_id: str,
    activity_runner: ActivityRunner,
    cancel_requested: CancelRequested | None,
) -> dict[str, str] | None:
    if cancel_requested is None or not cancel_requested():
        return None
    await activity_runner("cancel_task", {"task_id": task_id}, timedelta(minutes=2))
    return {"task_id": task_id, "status": "cancelled"}


def _required_artifact_failure(
    *,
    postprocessed: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any] | None:
    if postprocessed.get("skipped") is True or report.get("skipped") is True:
        return {
            "platform": "postprocess_or_report",
            "failure_category": "postprocess_or_report_deferred",
            "retryable": False,
        }
    if not postprocessed.get("artifact_paths") or not report.get("artifact_paths"):
        return {
            "platform": "postprocess_or_report",
            "failure_category": "postprocess_or_report_deferred",
            "retryable": False,
        }
    return None


async def _publish_full_pipeline(
    *,
    task_id: str,
    task: dict[str, Any],
    results: dict[str, Any],
    activity_runner: ActivityRunner,
    cancel_requested: CancelRequested | None = None,
) -> dict[str, str]:
    payload = {"task_id": task_id, "task": task, "results": results}
    artifacts = await _generate_result_artifacts(payload=payload, activity_runner=activity_runner)
    postprocessed = artifacts["postprocess_result"]
    report = artifacts["report_result"]
    artifact_failure = _required_artifact_failure(postprocessed=postprocessed, report=report)
    if artifact_failure is not None:
        return await _mark_artifact_pipeline_failed(
            task_id=task_id,
            payload=payload,
            results=results,
            artifacts=artifacts,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )
    cancellation = await _cancel_if_requested(
        task_id=task_id,
        activity_runner=activity_runner,
        cancel_requested=cancel_requested,
    )
    if cancellation is not None:
        return cancellation
    await activity_runner(
        "publish_full_result",
        {**payload, **artifacts},
        timedelta(minutes=20),
    )
    return {"task_id": task_id, "status": "completed"}


async def _publish_degraded_pipeline(
    *,
    task_id: str,
    task: dict[str, Any],
    results: dict[str, Any],
    activity_runner: ActivityRunner,
    cancel_requested: CancelRequested | None = None,
) -> dict[str, str]:
    payload = {"task_id": task_id, "task": task, "results": results}
    artifacts = await _generate_result_artifacts(payload=payload, activity_runner=activity_runner)
    postprocessed = artifacts["postprocess_result"]
    report = artifacts["report_result"]
    artifact_failure = _required_artifact_failure(postprocessed=postprocessed, report=report)
    if artifact_failure is not None:
        return await _mark_artifact_pipeline_failed(
            task_id=task_id,
            payload=payload,
            results=results,
            artifacts=artifacts,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )
    cancellation = await _cancel_if_requested(
        task_id=task_id,
        activity_runner=activity_runner,
        cancel_requested=cancel_requested,
    )
    if cancellation is not None:
        return cancellation
    await activity_runner(
        "publish_degraded_result",
        {**payload, **artifacts},
        timedelta(minutes=10),
    )
    return {"task_id": task_id, "status": "completed_degraded"}


async def _generate_result_artifacts(
    *,
    payload: dict[str, Any],
    activity_runner: ActivityRunner,
) -> dict[str, Any]:
    imported = await activity_runner("import_run_rows_to_corpus", payload, timedelta(minutes=10))
    exported = await activity_runner(
        "export_vehicle_workbooks",
        {**payload, "import_result": imported},
        timedelta(minutes=10),
    )
    postprocessed = await activity_runner(
        "run_postprocess",
        {**payload, "import_result": imported, "export_result": exported},
        timedelta(minutes=10),
    )
    report = await activity_runner(
        "run_llm_report",
        {
            **payload,
            "import_result": imported,
            "export_result": exported,
            "postprocess_result": postprocessed,
        },
        timedelta(minutes=20),
    )
    return {
        "import_result": imported,
        "export_result": exported,
        "postprocess_result": postprocessed,
        "report_result": report,
    }


async def _mark_artifact_pipeline_failed(
    *,
    task_id: str,
    payload: dict[str, Any],
    results: dict[str, Any],
    artifacts: dict[str, Any],
    activity_runner: ActivityRunner,
    cancel_requested: CancelRequested | None,
) -> dict[str, str]:
    artifact_failure = {
        "platform": "postprocess_or_report",
        "failure_category": "postprocess_or_report_deferred",
        "retryable": False,
    }
    failed_results = dict(results)
    failed_results["failed_platforms"] = [*list(results.get("failed_platforms") or []), artifact_failure]
    cancellation = await _cancel_if_requested(
        task_id=task_id,
        activity_runner=activity_runner,
        cancel_requested=cancel_requested,
    )
    if cancellation is not None:
        return cancellation
    await activity_runner(
        "mark_task_failed",
        {
            **payload,
            "results": failed_results,
            **artifacts,
        },
        timedelta(minutes=2),
    )
    return {"task_id": task_id, "status": "failed"}


async def run_single_vehicle_task(
    task_id: str,
    activity_runner: ActivityRunner,
    *,
    cancel_requested: CancelRequested | None = None,
) -> dict[str, str]:
    task = await activity_runner("load_task", task_id, timedelta(seconds=30))
    cancellation = await _cancel_if_requested(
        task_id=task_id,
        activity_runner=activity_runner,
        cancel_requested=cancel_requested,
    )
    if cancellation is not None:
        return cancellation
    resolved = await activity_runner(
        "resolve_vehicle_inputs",
        {
            "task_id": task_id,
            "vehicles": task.get("vehicles", []),
            "mode": task.get("collection_mode", "incremental"),
        },
        timedelta(seconds=30),
    )

    platform_inputs = dict(resolved.get("platforms") or {})
    initial_failed_platforms = list(resolved.get("failed_platforms") or [])
    runs = {}
    for platform in PLATFORMS:
        run_input = platform_inputs.get(platform)
        if not run_input:
            continue
        run = await activity_runner("create_or_join_collection_run", run_input, timedelta(minutes=2))
        runs[platform] = run
        cancellation = await _cancel_if_requested(
            task_id=task_id,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )
        if cancellation is not None:
            return cancellation

    if not runs:
        results = {
            "successful_platforms": [],
            "failed_platforms": initial_failed_platforms,
            "runs": {},
        }
        cancellation = await _cancel_if_requested(
            task_id=task_id,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )
        if cancellation is not None:
            return cancellation
        await activity_runner("mark_task_failed", {"task_id": task_id, "results": results}, timedelta(minutes=2))
        return {"task_id": task_id, "status": "failed"}

    results = await activity_runner(
        "wait_for_collection_runs",
        {"task_id": task_id, "run_ids": [run["run_id"] for run in runs.values()]},
        timedelta(minutes=45),
    )
    results = {
        **results,
        "runs": {**runs, **dict(results.get("runs") or {})},
        "failed_platforms": [*initial_failed_platforms, *list(results.get("failed_platforms") or [])],
    }
    results = _collection_results_with_missing_failures(_collection_results_with_pending_timeouts(results))
    cancellation = await _cancel_if_requested(
        task_id=task_id,
        activity_runner=activity_runner,
        cancel_requested=cancel_requested,
    )
    if cancellation is not None:
        return cancellation

    successful_platforms = _successful_platforms(results)
    failed_platforms = list(results.get("failed_platforms") or [])
    if successful_platforms and failed_platforms:
        cancellation = await _cancel_if_requested(
            task_id=task_id,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )
        if cancellation is not None:
            return cancellation
        degraded_publish = await _publish_degraded_pipeline(
            task_id=task_id,
            task=task,
            results=results,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )
        if degraded_publish["status"] != "completed_degraded":
            return degraded_publish
        retryable = _retryable_failures(failed_platforms)
        if retryable:
            await activity_runner(
                "schedule_retry",
                {"task_id": task_id, "failed_platforms": retryable, "results": results},
                timedelta(minutes=2),
            )
            retry_result = await activity_runner(
                "retry_failed_platforms",
                {"task_id": task_id, "failed_platforms": retryable, "results": results},
                timedelta(minutes=45),
            )
            if retry_result.get("successful_platforms") or retry_result.get("failed_platforms"):
                results = _merge_retry_results(results, retry_result)
                successful_platforms = _successful_platforms(results)
                failed_platforms = list(results.get("failed_platforms") or [])
        if set(PLATFORMS).issubset(successful_platforms) and not failed_platforms:
            cancellation = await _cancel_if_requested(
                task_id=task_id,
                activity_runner=activity_runner,
                cancel_requested=cancel_requested,
            )
            if cancellation is not None:
                return cancellation
            return await _publish_full_pipeline(
                task_id=task_id,
                task=task,
                results=results,
                activity_runner=activity_runner,
                cancel_requested=cancel_requested,
            )
        return {"task_id": task_id, "status": "completed_degraded"}

    if set(PLATFORMS).issubset(successful_platforms):
        cancellation = await _cancel_if_requested(
            task_id=task_id,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )
        if cancellation is not None:
            return cancellation
        return await _publish_full_pipeline(
            task_id=task_id,
            task=task,
            results=results,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )

    cancellation = await _cancel_if_requested(
        task_id=task_id,
        activity_runner=activity_runner,
        cancel_requested=cancel_requested,
    )
    if cancellation is not None:
        return cancellation
    await activity_runner("mark_task_failed", {"task_id": task_id, "results": results}, timedelta(minutes=2))
    return {"task_id": task_id, "status": "failed"}


async def run_comparison_task(
    task_id: str,
    activity_runner: ActivityRunner,
    *,
    child_workflow_runner: ChildWorkflowRunner | None = None,
) -> dict[str, Any]:
    task = await activity_runner("load_comparison_task", task_id, timedelta(seconds=30))
    vehicles = sorted(
        [vehicle for vehicle in task.get("vehicles", []) if isinstance(vehicle, dict)],
        key=lambda vehicle: int(vehicle.get("position") or vehicle.get("id") or 0),
    )

    if len(vehicles) > MAX_COMPARISON_VEHICLES:
        payload = {
            "comparison_id": task_id,
            "task": task,
            "status": "failed",
            "error_code": "too_many_vehicles",
            "error_message": "竞品对比最多支持 5 个车型",
        }
        await activity_runner("publish_comparison_result", payload, timedelta(minutes=2))
        return {
            "comparison_id": task_id,
            "status": "failed",
            "error_code": "too_many_vehicles",
            "error_message": "竞品对比最多支持 5 个车型",
        }

    vehicle_states: list[dict[str, Any]] = []
    for index, vehicle in enumerate(vehicles, start=1):
        reused = bool(vehicle.get("source_job_id")) and vehicle.get("needs_collection") is not True
        subworkflow: dict[str, Any] = {"reused": True, "source_job_id": vehicle.get("source_job_id")}
        if not reused:
            subworkflow = await activity_runner(
                "ensure_vehicle_subworkflow",
                {"comparison_id": task_id, "task": task, "vehicle": vehicle},
                timedelta(minutes=2),
            )
        vehicle_states.append(
            {
                "index": index,
                "vehicle": vehicle,
                "reused": reused,
                "subworkflow": subworkflow,
            }
        )

    if child_workflow_runner is not None:
        for state in vehicle_states:
            if state["reused"]:
                continue
            subworkflow = dict(state["subworkflow"])
            child_task_id = str(subworkflow.get("child_task_id") or "")
            await child_workflow_runner(
                {
                    "comparison_id": task_id,
                    "task": task,
                    "vehicle": state["vehicle"],
                    "subworkflow": subworkflow,
                    "child_task_id": child_task_id,
                    "child_workflow_id": subworkflow.get("child_workflow_id"),
                }
            )

    available: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for state in vehicle_states:
        vehicle = state["vehicle"]
        result = await activity_runner(
            "wait_for_vehicle_results",
            {
                "comparison_id": task_id,
                "task": task,
                "vehicle": vehicle,
                "subworkflow": state["subworkflow"],
                "reused": state["reused"],
            },
            timedelta(minutes=45),
        )
        normalized = _normalized_comparison_result(
            vehicle=vehicle,
            result=result,
            reused=bool(state["reused"]),
            fallback_position=int(state["index"]),
        )
        if _usable_comparison_result(normalized):
            available.append(normalized)
        else:
            excluded.append(_comparison_exclusion(normalized, vehicle))

    if len(available) < MIN_COMPARISON_RESULTS:
        error_message = "竞品对比至少需要 2 个可用车型结果"
        payload = {
            "comparison_id": task_id,
            "task": task,
            "status": "failed",
            "error_code": "insufficient_available_vehicles",
            "error_message": error_message,
            "available_vehicle_count": len(available),
            "vehicles": available,
            "excluded": excluded,
        }
        await activity_runner("publish_comparison_result", payload, timedelta(minutes=2))
        return {
            "comparison_id": task_id,
            "status": "failed",
            "error_code": "insufficient_available_vehicles",
            "error_message": error_message,
            "available_vehicle_count": len(available),
            "excluded": excluded,
        }

    report = await activity_runner(
        "generate_comparison_report",
        {
            "comparison_id": task_id,
            "task": task,
            "vehicles": available,
            "excluded": excluded,
        },
        timedelta(minutes=20),
    )
    status = _comparison_status(excluded=excluded, vehicles=available, report=report)
    publish = await activity_runner(
        "publish_comparison_result",
        {
            "comparison_id": task_id,
            "task": task,
            "status": status,
            "vehicles": available,
            "excluded": excluded,
            "report": report,
            "degraded": status == "completed_degraded",
        },
        timedelta(minutes=5),
    )
    result = {
        "comparison_id": task_id,
        "status": str(publish.get("status") or status),
        "vehicle_count": len(available),
        "excluded": excluded,
    }

    upgraded_vehicle_ids = [
        int(vehicle["vehicle_id"])
        for vehicle in available
        if vehicle.get("upgraded_to_full") is True
    ]
    if upgraded_vehicle_ids:
        regenerated = await activity_runner(
            "regenerate_comparison_after_upgrade",
            {
                "comparison_id": task_id,
                "task": task,
                "vehicles": available,
                "excluded": excluded,
                "upgraded_vehicle_ids": upgraded_vehicle_ids,
                "previous_report": report,
            },
            timedelta(minutes=20),
        )
        result["status"] = str(regenerated.get("status") or "completed_upgraded")

    return result


@workflow.defn
class SingleVehicleTaskWorkflow:
    def __init__(self) -> None:
        self.cancel_requested = False

    @workflow.signal
    async def request_cancel(self) -> None:
        self.cancel_requested = True

    @workflow.run
    async def run(self, task_id: str) -> dict:
        async def activity_runner(name: str, payload: Any, timeout: timedelta) -> dict[str, Any]:
            if name == "create_or_join_collection_run":
                return await workflow.start_activity(name, payload, start_to_close_timeout=timeout)
            return await workflow.execute_activity(name, payload, start_to_close_timeout=timeout)

        return await run_single_vehicle_task(
            task_id,
            activity_runner,
            cancel_requested=lambda: self.cancel_requested,
        )


@workflow.defn
class ComparisonTaskWorkflow:
    @workflow.run
    async def run(self, task_id: str) -> dict:
        async def activity_runner(name: str, payload: Any, timeout: timedelta) -> dict[str, Any]:
            return await workflow.execute_activity(name, payload, start_to_close_timeout=timeout)

        async def child_workflow_runner(payload: dict[str, Any]) -> dict[str, Any]:
            child_task_id = str(payload["child_task_id"])
            child_workflow_id = str(payload.get("child_workflow_id") or f"SingleVehicleTaskWorkflow:{child_task_id}")
            await workflow.start_child_workflow(
                SingleVehicleTaskWorkflow.run,
                child_task_id,
                id=child_workflow_id,
                task_queue=workflow.info().task_queue,
            )
            return {
                "child_task_id": child_task_id,
                "child_workflow_id": child_workflow_id,
                "started": True,
            }

        return await run_comparison_task(
            task_id,
            activity_runner,
            child_workflow_runner=child_workflow_runner,
        )
