from __future__ import annotations

from typing import Protocol


class TaskWorkflowClient(Protocol):
    def start_task(self, task_id: str) -> None:
        """Start asynchronous execution for an already-created task."""


class NoopTaskWorkflowClient:
    def start_task(self, task_id: str) -> None:
        return None


def get_task_workflow_client() -> TaskWorkflowClient:
    return NoopTaskWorkflowClient()
