from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from temporalio import workflow


ActivityRunner = Callable[[str, Any, timedelta], Awaitable[dict[str, Any]]]
CancelRequested = Callable[[], bool]
PLATFORMS = ("autohome", "dongchedi")


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


async def _publish_full_pipeline(
    *,
    task_id: str,
    task: dict[str, Any],
    results: dict[str, Any],
    activity_runner: ActivityRunner,
) -> None:
    payload = {"task_id": task_id, "task": task, "results": results}
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
    await activity_runner(
        "publish_full_result",
        {
            **payload,
            "import_result": imported,
            "export_result": exported,
            "postprocess_result": postprocessed,
            "report_result": report,
        },
        timedelta(minutes=20),
    )


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
    results = _collection_results_with_pending_timeouts(results)
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
        await activity_runner(
            "publish_degraded_result",
            {"task_id": task_id, "results": results},
            timedelta(minutes=10),
        )
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
            await _publish_full_pipeline(task_id=task_id, task=task, results=results, activity_runner=activity_runner)
            return {"task_id": task_id, "status": "completed"}
        return {"task_id": task_id, "status": "completed_degraded"}

    if successful_platforms:
        cancellation = await _cancel_if_requested(
            task_id=task_id,
            activity_runner=activity_runner,
            cancel_requested=cancel_requested,
        )
        if cancellation is not None:
            return cancellation
        await _publish_full_pipeline(task_id=task_id, task=task, results=results, activity_runner=activity_runner)
        return {"task_id": task_id, "status": "completed"}

    cancellation = await _cancel_if_requested(
        task_id=task_id,
        activity_runner=activity_runner,
        cancel_requested=cancel_requested,
    )
    if cancellation is not None:
        return cancellation
    await activity_runner("mark_task_failed", {"task_id": task_id, "results": results}, timedelta(minutes=2))
    return {"task_id": task_id, "status": "failed"}


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
        return {"task_id": task_id, "status": "queued"}
