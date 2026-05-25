from __future__ import annotations

from temporalio import workflow


@workflow.defn
class SingleVehicleTaskWorkflow:
    @workflow.run
    async def run(self, task_id: str) -> dict:
        return {"task_id": task_id, "status": "queued"}


@workflow.defn
class ComparisonTaskWorkflow:
    @workflow.run
    async def run(self, task_id: str) -> dict:
        return {"task_id": task_id, "status": "queued"}
