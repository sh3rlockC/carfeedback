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
from app.models import Task, TaskEvent
from app.services.passphrase import hash_passphrase
from app.services.task_tokens import hash_task_token
from app.services.task_workflow_client import get_task_workflow_client


class FakeTaskWorkflowClient:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start_task(self, task_id: str) -> None:
        self.started.append(task_id)


def make_client(tmp_path: Path) -> tuple[TestClient, FakeTaskWorkflowClient]:
    reset_engine_cache()
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'tasks-api.db'}",
        pass_phrase_hash=hash_passphrase("weekly-secret"),
        pass_phrase_version="2026-W17",
        session_secret="test-secret",
        artifact_root=str(tmp_path / "artifacts"),
        workspace_root="/Users/xyc/Documents/codexwork",
    )
    app = create_app(settings)
    fake_workflow = FakeTaskWorkflowClient()
    app.dependency_overrides[get_task_workflow_client] = lambda: fake_workflow
    return TestClient(app), fake_workflow


def authenticate(client: TestClient) -> None:
    response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert response.status_code == 200


def token_from_url(url: str, name: str) -> str:
    values = parse_qs(urlparse(url).query).get(name)
    assert values
    return values[0]


def create_single_task(client: TestClient) -> dict:
    authenticate(client)
    response = client.post(
        "/api/tasks",
        json={"task_type": "single", "vehicles": [{"query": "风云X3 PLUS"}]},
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
    assert fake_workflow.started == [payload["task_id"]]

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
        assert [event.event_type for event in task.events] == ["created"]
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
    client, _ = make_client(tmp_path)
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

    pause_response = client.post(f"/api/tasks/{payload['task_id']}/pause-retry?manage_token={manage_token}")
    assert pause_response.status_code == 200
    assert pause_response.json()["events"][-1]["event_type"] == "retry_paused"

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
