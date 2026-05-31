from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from app.config import Settings
from app.db import get_session_local, reset_engine_cache
from app.main import create_app
from app.models import ConfirmedVehicleSeries, Task, TaskEvent
from app.services.passphrase import hash_passphrase
from app.services.task_tokens import hash_task_token
from app.services.task_workflow_client import get_task_workflow_client


class FakeTaskWorkflowClient:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []

    def start_task(self, task_id: str, task_type: str) -> None:
        self.started.append((task_id, task_type))


class FailingTaskWorkflowClient:
    def start_task(self, task_id: str, task_type: str) -> None:
        raise RuntimeError("temporal unavailable")


class FailOnSecondStartWorkflowClient:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []

    def start_task(self, task_id: str, task_type: str) -> None:
        self.started.append((task_id, task_type))
        if len(self.started) > 1:
            raise RuntimeError("temporal unavailable")


def make_client(
    tmp_path: Path,
    workflow_client: FakeTaskWorkflowClient | FailingTaskWorkflowClient | None = None,
    *,
    raise_server_exceptions: bool = True,
    access_control_enabled: bool = True,
) -> tuple[TestClient, FakeTaskWorkflowClient | FailingTaskWorkflowClient]:
    reset_engine_cache()
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'tasks-api.db'}",
        pass_phrase_hash=hash_passphrase("weekly-secret"),
        pass_phrase_version="2026-W17",
        access_control_enabled=access_control_enabled,
        session_secret="test-secret",
        artifact_root=str(tmp_path / "artifacts"),
        workspace_root="/Users/xyc/Documents/codexwork",
    )
    app = create_app(settings)
    fake_workflow = workflow_client or FakeTaskWorkflowClient()
    app.dependency_overrides[get_task_workflow_client] = lambda: fake_workflow
    return TestClient(app, raise_server_exceptions=raise_server_exceptions), fake_workflow


def authenticate(client: TestClient) -> None:
    response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert response.status_code == 200


def selected_candidates() -> dict:
    return {
        "autohome": {
            "series_id": "8089",
            "url": "https://k.autohome.com.cn/8089/",
            "title": "风云X3 PLUS",
            "source": "fixture",
        },
        "dongchedi": {
            "series_id": "25398",
            "url": "https://www.dongchedi.com/auto/series/25398",
            "title": "风云X3 PLUS",
            "source": "fixture",
        },
    }


def token_from_url(url: str, name: str) -> str:
    values = parse_qs(urlparse(url).query).get(name)
    assert values
    return values[0]


def create_single_task(client: TestClient) -> dict:
    authenticate(client)
    response = client.post(
        "/api/tasks",
        json={
            "task_type": "single",
            "vehicles": [
                {
                    "query": "风云X3 PLUS",
                    "selected_candidates": selected_candidates(),
                    "enabled_platforms": ["autohome", "dongchedi"],
                    "cache_confirmed_platforms": ["autohome", "dongchedi"],
                }
            ],
        },
    )
    assert response.status_code == 200
    return response.json()


def test_post_tasks_creates_single_vehicle_task_and_returns_access_urls(tmp_path: Path) -> None:
    client, fake_workflow = make_client(tmp_path)

    unauthorized = client.post(
        "/api/tasks",
        json={"task_type": "single", "vehicles": [{"query": "风云X3 PLUS"}]},
    )
    assert unauthorized.status_code == 401

    payload = create_single_task(client)

    assert payload["task_id"].startswith("task_")
    assert payload["status"] == "queued"
    assert payload["view_url"].startswith(f"/tasks/{payload['task_id']}?view_token=")
    assert payload["manage_url"].startswith(f"/tasks/{payload['task_id']}/manage?manage_token=")
    assert fake_workflow.started == [(payload["task_id"], "single")]


def test_tasks_runtime_and_creation_work_without_passphrase_when_access_control_disabled(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, access_control_enabled=False)

    runtime_response = client.get("/api/admin/runtime")
    assert runtime_response.status_code == 200
    assert runtime_response.json()["access_control_enabled"] is False

    list_response = client.get("/api/tasks")
    assert list_response.status_code == 200
    assert list_response.json() == []

    response = client.post(
        "/api/tasks",
        json={
            "task_type": "single",
            "vehicles": [
                {
                    "query": "风云X3 PLUS",
                    "selected_candidates": selected_candidates(),
                    "enabled_platforms": ["autohome", "dongchedi"],
                    "cache_confirmed_platforms": ["autohome", "dongchedi"],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()

    view_token = token_from_url(payload["view_url"], "view_token")
    manage_token = token_from_url(payload["manage_url"], "manage_token")
    assert view_token != manage_token

    session = get_session_local()()
    try:
        task = session.get(Task, payload["task_id"])
        assert task is not None
        assert task.display_name == "风云X3 PLUS"
        assert task.task_type == "single"
        assert task.status == "queued"
        assert task.current_stage == "queued"
        assert task.view_token_hash == hash_task_token(view_token)
        assert task.manage_token_hash == hash_task_token(manage_token)
        assert view_token not in task.view_token_hash
        assert manage_token not in task.manage_token_hash
        assert len(task.vehicles) == 1
        assert task.vehicles[0].query == "风云X3 PLUS"
        assert task.vehicles[0].model_name == "风云X3 PLUS"
        assert task.vehicles[0].autohome_series_id == "8089"
        assert task.vehicles[0].dcd_series_id == "25398"
        assert task.vehicles[0].enabled_platforms == ["autohome", "dongchedi"]
        assert [event.event_type for event in task.events] == ["created"]

        confirmed = (
            session.query(ConfirmedVehicleSeries)
            .filter(ConfirmedVehicleSeries.query_key == "风云x3 plus")
            .order_by(ConfirmedVehicleSeries.platform)
            .all()
        )
        assert {record.platform: record.series_id for record in confirmed} == {
            "autohome": "8089",
            "dongchedi": "25398",
        }
    finally:
        session.close()


def test_post_tasks_persists_single_platform_selection_without_missing_series(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    authenticate(client)

    response = client.post(
        "/api/tasks",
        json={
            "task_type": "single",
            "vehicles": [
                {
                    "query": "风云X3 PLUS",
                    "selected_candidates": selected_candidates(),
                    "enabled_platforms": ["autohome"],
                    "cache_confirmed_platforms": ["autohome"],
                }
            ],
        },
    )

    assert response.status_code == 200
    session = get_session_local()()
    try:
        task = session.get(Task, response.json()["task_id"])
        assert task is not None
        vehicle = task.vehicles[0]
        assert vehicle.autohome_series_id == "8089"
        assert vehicle.dcd_series_id is None
        assert vehicle.enabled_platforms == ["autohome"]
    finally:
        session.close()


def test_post_tasks_requires_comparison_vehicle_candidates(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    authenticate(client)

    missing_candidates = client.post(
        "/api/tasks",
        json={
            "task_type": "comparison",
            "vehicles": [
                {"query": "测试车A"},
                {"query": "测试车B"},
            ],
        },
    )

    assert missing_candidates.status_code == 400
    assert "confirmed autohome series_id required" in missing_candidates.json()["detail"]

    response = client.post(
        "/api/tasks",
        json={
            "task_type": "comparison",
            "vehicles": [
                {
                    "query": "测试车A",
                    "selected_candidates": {
                        "autohome": {"series_id": "1001", "title": "测试车A", "source": "fixture"},
                        "dongchedi": {"series_id": "2001", "title": "测试车A", "source": "fixture"},
                    },
                },
                {
                    "query": "测试车B",
                    "selected_candidates": {
                        "autohome": {"series_id": "1002", "title": "测试车B", "source": "fixture"},
                        "dongchedi": {"series_id": "2002", "title": "测试车B", "source": "fixture"},
                    },
                },
            ],
        },
    )

    assert response.status_code == 200
    session = get_session_local()()
    try:
        task = session.get(Task, response.json()["task_id"])
        assert task is not None
        assert task.task_type == "comparison"
        assert [(vehicle.autohome_series_id, vehicle.dcd_series_id) for vehicle in task.vehicles] == [
            ("1001", "2001"),
            ("1002", "2002"),
        ]
    finally:
        session.close()


def test_get_tasks_returns_list_after_passphrase_access(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    create_payload = create_single_task(client)

    unauthenticated_client, _ = make_client(tmp_path)
    unauthorized = unauthenticated_client.get("/api/tasks")
    assert unauthorized.status_code == 401

    response = client.get("/api/tasks")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["task_id"] == create_payload["task_id"]
    assert items[0]["display_name"] == "风云X3 PLUS"
    assert "view_token_hash" not in items[0]
    assert "manage_token_hash" not in items[0]


def test_get_tasks_load_returns_project_load_instead_of_task_404(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    create_single_task(client)

    response = client.get("/api/tasks/load")

    assert response.status_code == 200
    payload = response.json()
    assert payload["queued_task_count"] == 1
    assert payload["running_task_count"] == 0
    assert "platforms" in payload


def test_post_tasks_marks_task_failed_when_workflow_start_fails(tmp_path: Path) -> None:
    client, _ = make_client(
        tmp_path,
        workflow_client=FailingTaskWorkflowClient(),
        raise_server_exceptions=False,
    )
    authenticate(client)

    response = client.post(
        "/api/tasks",
        json={
            "task_type": "single",
            "vehicles": [
                {
                    "query": "风云X3 PLUS",
                    "selected_candidates": selected_candidates(),
                    "enabled_platforms": ["autohome", "dongchedi"],
                }
            ],
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "task workflow unavailable"

    session = get_session_local()()
    try:
        tasks = session.query(Task).all()
        assert len(tasks) == 1
        task = tasks[0]
        assert task.status == "failed"
        assert task.current_stage == "workflow_start_failed"
        events = [event.event_type for event in task.events]
        assert events == ["created", "workflow_start_failed"]
        failure_event = task.events[-1]
        assert failure_event.payload_json == {"error": "temporal unavailable"}
    finally:
        session.close()


def test_get_task_detail_accepts_view_token_without_passphrase(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    payload = create_single_task(client)
    view_token = token_from_url(payload["view_url"], "view_token")

    public_client, _ = make_client(tmp_path)
    unauthorized = public_client.get(f"/api/tasks/{payload['task_id']}")
    assert unauthorized.status_code == 401

    wrong_token = public_client.get(f"/api/tasks/{payload['task_id']}?view_token=wrong")
    assert wrong_token.status_code == 403

    response = public_client.get(f"/api/tasks/{payload['task_id']}?view_token={view_token}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["task_id"] == payload["task_id"]
    assert detail["vehicles"][0]["query"] == "风云X3 PLUS"
    assert detail["events"][0]["event_type"] == "created"
    assert "view_token_hash" not in detail
    assert "manage_token_hash" not in detail


def test_management_action_requires_passphrase_session_and_manage_token(tmp_path: Path) -> None:
    client, fake_workflow = make_client(tmp_path)
    payload = create_single_task(client)
    manage_token = token_from_url(payload["manage_url"], "manage_token")

    public_client, _ = make_client(tmp_path)
    missing_session = public_client.post(f"/api/tasks/{payload['task_id']}/cancel?manage_token={manage_token}")
    assert missing_session.status_code == 401

    missing_token = client.post(f"/api/tasks/{payload['task_id']}/cancel")
    assert missing_token.status_code == 403

    wrong_token = client.post(f"/api/tasks/{payload['task_id']}/cancel?manage_token=wrong")
    assert wrong_token.status_code == 403

    response = client.post(f"/api/tasks/{payload['task_id']}/cancel?manage_token={manage_token}")

    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "cancelled"
    assert detail["current_stage"] == "cancelled"
    assert detail["events"][-1]["event_type"] == "cancel_requested"

    retry_response = client.post(f"/api/tasks/{payload['task_id']}/retry?manage_token={manage_token}")
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "queued"
    assert retry_response.json()["events"][-1]["event_type"] == "manual_retry_requested"
    assert fake_workflow.started == [
        (payload["task_id"], "single"),
        (payload["task_id"], "single"),
    ]

    pause_response = client.post(f"/api/tasks/{payload['task_id']}/pause-retry?manage_token={manage_token}")
    assert pause_response.status_code == 200
    pause_detail = pause_response.json()
    assert pause_detail["status"] == "retry_paused"
    assert pause_detail["current_stage"] == "retry_paused"
    assert pause_detail["events"][-1]["event_type"] == "retry_paused"

    session = get_session_local()()
    try:
        events = (
            session.query(TaskEvent)
            .filter(TaskEvent.task_id == payload["task_id"])
            .order_by(TaskEvent.id)
            .all()
        )
        assert [event.event_type for event in events] == [
            "created",
            "cancel_requested",
            "manual_retry_requested",
            "retry_paused",
        ]
    finally:
        session.close()


def test_retry_marks_task_failed_when_workflow_restart_fails(tmp_path: Path) -> None:
    workflow_client = FailOnSecondStartWorkflowClient()
    client, _ = make_client(tmp_path, workflow_client=workflow_client, raise_server_exceptions=False)
    payload = create_single_task(client)
    manage_token = token_from_url(payload["manage_url"], "manage_token")

    response = client.post(f"/api/tasks/{payload['task_id']}/retry?manage_token={manage_token}")

    assert response.status_code == 503
    assert response.json()["detail"] == "task workflow unavailable"

    session = get_session_local()()
    try:
        task = session.get(Task, payload["task_id"])
        assert task is not None
        assert task.status == "failed"
        assert task.current_stage == "workflow_start_failed"
        assert [event.event_type for event in task.events] == [
            "created",
            "manual_retry_requested",
            "workflow_start_failed",
        ]
        assert workflow_client.started == [
            (payload["task_id"], "single"),
            (payload["task_id"], "single"),
        ]
    finally:
        session.close()
