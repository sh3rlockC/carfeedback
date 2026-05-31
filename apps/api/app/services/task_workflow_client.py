from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from fastapi import Depends

from app.config import Settings, get_settings


class TaskWorkflowClient(Protocol):
    def start_task(self, task_id: str, task_type: str) -> None:
        """Start asynchronous execution for an already-created task."""


class NoopTaskWorkflowClient:
    def start_task(self, task_id: str, task_type: str) -> None:
        return None


WORKFLOW_NAMES = {
    "single": "SingleVehicleTaskWorkflow",
    "single_vehicle": "SingleVehicleTaskWorkflow",
    "comparison": "ComparisonTaskWorkflow",
}


def _workflow_name(task_type: str) -> str:
    try:
        return WORKFLOW_NAMES[task_type]
    except KeyError as exc:
        raise ValueError(f"unsupported task type for workflow start: {task_type}") from exc


@dataclass
class TemporalTaskWorkflowClient:
    temporal_address: str
    temporal_namespace: str
    temporal_task_queue: str

    def start_task(self, task_id: str, task_type: str) -> None:
        asyncio.run(self._start_task(task_id, task_type))

    async def _start_task(self, task_id: str, task_type: str) -> None:
        from temporalio.client import Client

        workflow_name = _workflow_name(task_type)
        client = await Client.connect(self.temporal_address, namespace=self.temporal_namespace)
        await client.start_workflow(
            workflow_name,
            task_id,
            id=f"{workflow_name}:{task_id}",
            task_queue=self.temporal_task_queue,
        )


def get_task_workflow_client(settings: Settings = Depends(get_settings)) -> TaskWorkflowClient:
    return TemporalTaskWorkflowClient(
        temporal_address=settings.temporal_address,
        temporal_namespace=settings.temporal_namespace,
        temporal_task_queue=settings.temporal_task_queue,
    )
