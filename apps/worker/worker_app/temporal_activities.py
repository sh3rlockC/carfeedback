from __future__ import annotations

from temporalio import activity


class TaskActivities:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url

    @activity.defn
    async def load_task(self, task_id: str) -> dict:
        return {"task_id": task_id, "status": "loaded"}
