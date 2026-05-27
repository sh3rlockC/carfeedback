from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.services.task_workflow_client import TemporalTaskWorkflowClient


class FakeTemporalClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start_workflow(self, workflow: str, arg: str, **kwargs) -> None:
        self.calls.append({"workflow": workflow, "arg": arg, **kwargs})


def test_temporal_task_workflow_client_starts_single_vehicle_workflow() -> None:
    fake_client = FakeTemporalClient()
    seen_connect: list[tuple[str, str]] = []

    async def fake_factory(address: str, namespace: str) -> FakeTemporalClient:
        seen_connect.append((address, namespace))
        return fake_client

    settings = Settings(
        temporal_address="temporal:7233",
        temporal_namespace="default",
        temporal_task_queue="vehicle-koubei-temporal",
    )
    client = TemporalTaskWorkflowClient(settings=settings, client_factory=fake_factory)

    client.start_task("task_1", "single")

    assert seen_connect == [("temporal:7233", "default")]
    assert fake_client.calls == [
        {
            "workflow": "SingleVehicleTaskWorkflow",
            "arg": "task_1",
            "id": "SingleVehicleTaskWorkflow:task_1",
            "task_queue": "vehicle-koubei-temporal",
        }
    ]


def test_temporal_task_workflow_client_starts_comparison_workflow() -> None:
    fake_client = FakeTemporalClient()

    async def fake_factory(address: str, namespace: str) -> FakeTemporalClient:
        return fake_client

    settings = Settings(temporal_task_queue="vehicle-koubei-temporal")
    client = TemporalTaskWorkflowClient(settings=settings, client_factory=fake_factory)

    client.start_task("task_cmp", "comparison")

    assert fake_client.calls[0]["workflow"] == "ComparisonTaskWorkflow"
    assert fake_client.calls[0]["id"] == "ComparisonTaskWorkflow:task_cmp"


def test_temporal_task_workflow_client_rejects_unknown_task_type() -> None:
    settings = Settings()
    client = TemporalTaskWorkflowClient(settings=settings)

    try:
        client.start_task("task_bad", "unknown")
    except ValueError as exc:
        assert str(exc) == "unsupported task type: unknown"
    else:
        raise AssertionError("expected ValueError")
