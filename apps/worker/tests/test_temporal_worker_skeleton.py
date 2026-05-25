from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.temporal_activities import TaskActivities
from worker_app.temporal_workflows import ComparisonTaskWorkflow, SingleVehicleTaskWorkflow

import temporal_worker


def test_temporal_workflow_class_names_are_stable() -> None:
    assert SingleVehicleTaskWorkflow.__name__ == "SingleVehicleTaskWorkflow"
    assert ComparisonTaskWorkflow.__name__ == "ComparisonTaskWorkflow"


def test_task_activities_exposes_load_task() -> None:
    assert hasattr(TaskActivities, "load_task")


def test_temporal_worker_exposes_main_without_connecting() -> None:
    assert hasattr(temporal_worker, "main")
