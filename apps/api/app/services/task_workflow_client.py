from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.config import Settings, get_settings


TemporalClientFactory = Callable[[str, str], Awaitable[Any]]

class TaskWorkflowClient(Protocol):
    def start_task(self, task_id: str, task_type: str) -> None:
        """Start asynchronous execution for an already-created task."""


def _workflow_name(task_type: str) -> str:
    if task_type == "single":
        return "SingleVehicleTaskWorkflow"
    if task_type == "comparison":
        return "ComparisonTaskWorkflow"
    raise ValueError(f"unsupported task type: {task_type}")


def _workflow_id_policy_kwargs() -> dict[str, Any]:
    try:
        from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
    except ModuleNotFoundError:
        return {}
    return {
        "id_reuse_policy": WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        "id_conflict_policy": WorkflowIDConflictPolicy.FAIL,
    }


async def _connect_temporal(address: str, namespace: str) -> Any:
    from temporalio.client import Client

    return await Client.connect(address, namespace=namespace)


class TemporalTaskWorkflowClient:
    def __init__(
        self,
        *,
        settings: Settings,
        client_factory: TemporalClientFactory = _connect_temporal,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory

    def start_task(self, task_id: str, task_type: str) -> None:
        asyncio.run(self._start_task(task_id, task_type))

    async def _start_task(self, task_id: str, task_type: str) -> None:
        workflow_name = _workflow_name(task_type)
        client = await self._client_factory(self._settings.temporal_address, self._settings.temporal_namespace)
        await client.start_workflow(
            workflow_name,
            task_id,
            id=f"{workflow_name}:{task_id}",
            task_queue=self._settings.temporal_task_queue,
            **_workflow_id_policy_kwargs(),
        )


def get_task_workflow_client() -> TaskWorkflowClient:
    return TemporalTaskWorkflowClient(settings=get_settings())
