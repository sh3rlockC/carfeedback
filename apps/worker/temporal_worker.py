from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from worker_app.temporal_activities import TaskActivities
from worker_app.temporal_workflows import ComparisonTaskWorkflow, SingleVehicleTaskWorkflow


async def main() -> None:
    temporal_address = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
    temporal_namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    temporal_task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "vehicle-koubei-temporal")
    database_url = os.getenv("DATABASE_URL")

    client = await Client.connect(temporal_address, namespace=temporal_namespace)
    activities = TaskActivities(database_url=database_url)
    worker = Worker(
        client,
        task_queue=temporal_task_queue,
        workflows=[SingleVehicleTaskWorkflow, ComparisonTaskWorkflow],
        activities=[
            activities.load_task,
            activities.load_comparison_task,
            activities.resolve_vehicle_inputs,
            activities.create_or_join_collection_run,
            activities.wait_for_collection_run,
            activities.wait_for_collection_runs,
            activities.ensure_vehicle_subworkflow,
            activities.wait_for_vehicle_results,
            activities.generate_comparison_report,
            activities.publish_comparison_result,
            activities.regenerate_comparison_after_upgrade,
            activities.import_run_rows_to_corpus,
            activities.export_vehicle_workbooks,
            activities.run_postprocess,
            activities.run_llm_report,
            activities.publish_degraded_result,
            activities.publish_full_result,
            activities.schedule_retry,
            activities.retry_failed_platforms,
            activities.mark_task_failed,
            activities.cancel_task,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
