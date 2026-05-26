from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import get_session_local
from app.main import create_app
from app.models import Job, JobArtifact, JobStageRun, KoubeiRawComment
from app.services.job_queue import get_job_queue
from app.services.passphrase import hash_passphrase


class FakeQueuedJob:
    def __init__(self, job_id: str):
        self.id = job_id


class FakeQueue:
    def __init__(self):
        self.calls: list[dict] = []

    def enqueue(self, func: str, kwargs: dict, **options):
        job_id = f"rq_{len(self.calls) + 1}"
        self.calls.append({"func": func, "kwargs": kwargs, "options": options, "id": job_id})
        return FakeQueuedJob(job_id)


class UnavailableQueue:
    def enqueue(self, func: str, kwargs: dict, **options):
        raise RedisConnectionError("connection refused")


def make_client(
    tmp_path: Path,
    queue: FakeQueue | UnavailableQueue | None = None,
    *,
    raise_server_exceptions: bool = True,
) -> tuple[TestClient, FakeQueue | UnavailableQueue]:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        pass_phrase_hash=hash_passphrase("weekly-secret"),
        pass_phrase_version="2026-W17",
        access_control_enabled=True,
        session_secret="test-secret",
        artifact_root=str(tmp_path / "artifacts"),
        workspace_root="/Users/xyc/Documents/codexwork",
        worker_job_timeout_seconds=2400,
    )
    app = create_app(settings)
    queue = queue or FakeQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    return TestClient(app, raise_server_exceptions=raise_server_exceptions), queue


def test_access_verify_sets_cookie(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "passphrase_version": "2026-W17"}
    assert "koubei_access" in response.cookies


def test_access_verify_rejects_invalid_passphrase(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.post("/api/access/verify", json={"passphrase": "wrong"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid passphrase"


def test_jobs_require_session_and_can_be_queried(tmp_path: Path) -> None:
    client, fake_queue = make_client(tmp_path)

    unauthorized = client.post(
        "/api/jobs",
        json={
            "query": "风云X3 PLUS",
            "selected_candidates": {
                "autohome": {
                    "series_id": "8089",
                    "url": "https://k.autohome.com.cn/8089?dimensionid=10&order=0&yearid=0#listcontainer",
                    "title": "风云X3 PLUS",
                    "source": "fixture",
                },
                "dongchedi": {
                    "series_id": "25398",
                    "url": "https://www.dongchedi.com/auto/series/25398",
                    "title": "风云X3 PLUS",
                    "source": "fixture",
                },
            },
        },
    )
    assert unauthorized.status_code == 401

    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    create_response = client.post(
        "/api/jobs",
        json={
            "query": "风云X3 PLUS",
            "selected_candidates": {
                "autohome": {
                    "series_id": "8089",
                    "url": "https://k.autohome.com.cn/8089?dimensionid=10&order=0&yearid=0#listcontainer",
                    "title": "风云X3 PLUS",
                    "source": "fixture",
                },
                "dongchedi": {
                    "series_id": "25398",
                    "url": "https://www.dongchedi.com/auto/series/25398",
                    "title": "风云X3 PLUS",
                    "source": "fixture",
                },
            },
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["status"] == "queued"
    assert payload["current_stage"] == "queued"
    assert fake_queue.calls[0]["func"] == "worker_jobs.run_job"
    assert fake_queue.calls[0]["kwargs"]["artifact_root"] == str(tmp_path / "artifacts")
    assert fake_queue.calls[0]["options"]["job_timeout"] == 2400

    job_id = payload["job_id"]
    get_response = client.get(f"/api/jobs/{job_id}")
    assert get_response.status_code == 200
    assert get_response.json()["query"] == "风云X3 PLUS"
    assert get_response.json()["queue_job_id"] == "rq_1"

    progress_response = client.get(f"/api/jobs/{job_id}/progress")
    assert progress_response.status_code == 200
    progress_payload = progress_response.json()
    assert progress_payload["status"] == "queued"
    assert progress_payload["overall_percent"] == 5
    assert progress_payload["stages"][0]["name"] == "queued"
    assert progress_payload["stages"][0]["status"] == "queued"

    session = get_session_local()()
    try:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.collection_mode == "incremental"
    finally:
        session.close()


def test_create_job_accepts_full_refresh_collection_mode(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    create_response = client.post(
        "/api/jobs",
        json={
            "query": "风云X3 PLUS",
            "collection_mode": "full_refresh",
            "selected_candidates": {
                "autohome": {"series_id": "8089", "title": "风云X3 PLUS", "source": "fixture"},
                "dongchedi": {"series_id": "25398", "title": "风云X3 PLUS", "source": "fixture"},
            },
        },
    )

    assert create_response.status_code == 200
    job_id = create_response.json()["job_id"]
    session = get_session_local()()
    try:
        job = session.get(Job, job_id)
        assert job is not None
        assert job.collection_mode == "full_refresh"
    finally:
        session.close()


def test_create_job_rejects_mismatched_alias_canonical_candidates(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    response = client.post(
        "/api/jobs",
        json={
            "query": "风云",
            "selected_candidates": {
                "autohome": {
                    "series_id": "7411",
                    "title": "风云T11",
                    "canonical_query": "风云T11",
                    "canonical_query_key": "fengyun t11",
                },
                "dongchedi": {
                    "series_id": "25545",
                    "title": "风云X3L",
                    "canonical_query": "风云X3L",
                    "canonical_query_key": "fengyun x3l",
                },
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "selected candidates must belong to the same canonical vehicle"


def test_create_job_accepts_matching_alias_canonical_candidates(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    response = client.post(
        "/api/jobs",
        json={
            "query": "风云",
            "selected_candidates": {
                "autohome": {
                    "series_id": "7411",
                    "title": "风云T11",
                    "canonical_query": "风云T11",
                    "canonical_query_key": "fengyun t11",
                },
                "dongchedi": {
                    "series_id": "9436",
                    "title": "风云T11",
                    "canonical_query": "风云T11",
                    "canonical_query_key": "fengyun t11",
                },
            },
        },
    )

    assert response.status_code == 200


def test_progress_endpoint_supports_incremental_check_stage(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    session = get_session_local()()
    try:
        job = Job(
            job_id="job_incremental_progress",
            query="风云X3 PLUS",
            model_name="风云X3 PLUS",
            status="checking_incremental",
            current_stage="checking_incremental",
            degraded=False,
            passphrase_version="2026-W17",
        )
        session.add(job)
        session.add(
            JobStageRun(
                job_id="job_incremental_progress",
                stage_name="checking_incremental",
                attempt_no=1,
                status="running",
            )
        )
        session.commit()
    finally:
        session.close()

    progress_dir = tmp_path / "artifacts" / "job_incremental_progress" / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "checking_incremental.progress.json").write_text(
        json.dumps(
            {
                "percent": 100,
                "message": "历史语料：汽车之家 12 条，懂车帝 8 条；将扫描前页新增评论",
                "collection_summary": {
                    "autohome": {"existing_count": 12, "new_count": 0, "pages_scanned": 0},
                    "dongchedi": {"existing_count": 8, "new_count": 0, "pages_scanned": 0},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.get("/api/jobs/job_incremental_progress/progress")

    assert response.status_code == 200
    payload = response.json()
    assert payload["overall_percent"] == 12
    stages = {stage["name"]: stage for stage in payload["stages"]}
    assert stages["checking_incremental"]["progress_percent"] == 100
    assert "汽车之家 12 条" in stages["checking_incremental"]["progress_message"]


def test_create_job_persists_confirmed_series_ids_separately(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    create_response = client.post(
        "/api/jobs",
        json={
            "query": "风云X3 PLUS",
            "selected_candidates": {
                "autohome": {
                    "series_id": "8089",
                    "url": "https://k.autohome.com.cn/8089?dimensionid=10&order=0&yearid=0#listcontainer",
                    "title": "风云X3 PLUS",
                    "source": "fixture",
                },
                "dongchedi": {
                    "series_id": "25398",
                    "url": "https://www.dongchedi.com/auto/series/25398",
                    "title": "风云X3 PLUS",
                    "source": "fixture",
                },
            },
        },
    )
    assert create_response.status_code == 200

    session = get_session_local()()
    try:
        rows = session.execute(
            text(
                """
                SELECT query_key, query, platform, series_id, title, url, source
                FROM confirmed_vehicle_series
                ORDER BY platform
                """
            )
        ).mappings().all()
    finally:
        session.close()

    assert [row["platform"] for row in rows] == ["autohome", "dongchedi"]
    assert {row["platform"]: row["series_id"] for row in rows} == {"autohome": "8089", "dongchedi": "25398"}
    assert {row["platform"]: row["query_key"] for row in rows} == {
        "autohome": "风云x3 plus",
        "dongchedi": "风云x3 plus",
    }
    assert all(row["query"] == "风云X3 PLUS" for row in rows)
    assert rows[0]["title"] == "风云X3 PLUS"


def test_create_job_returns_service_unavailable_when_queue_backend_is_down(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, queue=UnavailableQueue(), raise_server_exceptions=False)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    response = client.post(
        "/api/jobs",
        json={
            "query": "风云X3 PLUS",
            "selected_candidates": {
                "autohome": {"series_id": "8089", "title": "风云X3 PLUS", "source": "fixture"},
                "dongchedi": {"series_id": "25398", "title": "风云X3 PLUS", "source": "fixture"},
            },
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "任务队列暂不可用，请确认 Redis 和 worker 已启动。"


def test_progress_endpoint_includes_collector_stage_percentages(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    create_response = client.post(
        "/api/jobs",
        json={
            "query": "风云X3 PLUS",
            "selected_candidates": {
                "autohome": {"series_id": "8089", "title": "风云X3 PLUS", "source": "fixture"},
                "dongchedi": {"series_id": "25398", "title": "风云X3 PLUS", "source": "fixture"},
            },
        },
    )
    job_id = create_response.json()["job_id"]

    progress_dir = tmp_path / "artifacts" / job_id / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "collecting_autohome.progress.json").write_text(
        json.dumps({"percent": 37, "message": "正在采集第 2 页"}),
        encoding="utf-8",
    )
    (progress_dir / "collecting_dcd.progress.json").write_text(
        json.dumps({"overall": {"percent": 12}, "message": "正在采集懂车帝第 1 页"}),
        encoding="utf-8",
    )

    session = get_session_local()()
    try:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = "collecting_autohome"
        job.current_stage = "collecting_autohome"
        session.add_all(
            [
                JobStageRun(job_id=job_id, stage_name="collecting_autohome", attempt_no=1, status="running"),
                JobStageRun(job_id=job_id, stage_name="collecting_dcd", attempt_no=1, status="queued"),
            ]
        )
        session.commit()
    finally:
        session.close()

    progress_response = client.get(f"/api/jobs/{job_id}/progress")

    assert progress_response.status_code == 200
    stages = {stage["name"]: stage for stage in progress_response.json()["stages"]}
    assert stages["collecting_autohome"]["progress_percent"] == 37
    assert stages["collecting_autohome"]["progress_message"] == "正在采集第 2 页"
    assert stages["collecting_dcd"]["progress_percent"] == 12
    assert stages["collecting_dcd"]["progress_message"] == "正在采集懂车帝第 1 页"


def test_progress_endpoint_estimates_remaining_time_from_stage_history(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    now = datetime.now(UTC)
    session = get_session_local()()
    try:
        history = Job(
            job_id="job_eta_history",
            query="历史车",
            model_name="历史车",
            status="completed",
            current_stage="completed",
            degraded=False,
            passphrase_version="2026-W17",
            created_at=now - timedelta(hours=3),
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=1),
        )
        current = Job(
            job_id="job_eta_current",
            query="测试车",
            model_name="测试车",
            status="collecting_autohome",
            current_stage="collecting_autohome",
            degraded=False,
            passphrase_version="2026-W17",
            created_at=now - timedelta(minutes=5),
            started_at=now - timedelta(minutes=5),
        )
        session.add_all([history, current])
        session.add_all(
            [
                JobStageRun(
                    job_id="job_eta_history",
                    stage_name="collecting_autohome",
                    attempt_no=1,
                    status="success",
                    started_at=now - timedelta(minutes=60),
                    ended_at=now - timedelta(minutes=50),
                    duration_ms=600_000,
                ),
                JobStageRun(
                    job_id="job_eta_history",
                    stage_name="collecting_dcd",
                    attempt_no=1,
                    status="success",
                    started_at=now - timedelta(minutes=60),
                    ended_at=now - timedelta(minutes=40),
                    duration_ms=1_200_000,
                ),
                JobStageRun(
                    job_id="job_eta_history",
                    stage_name="postprocessing",
                    attempt_no=1,
                    status="success",
                    started_at=now - timedelta(minutes=40),
                    ended_at=now - timedelta(minutes=38),
                    duration_ms=120_000,
                ),
                JobStageRun(
                    job_id="job_eta_history",
                    stage_name="generating_hermes_outputs",
                    attempt_no=1,
                    status="success",
                    started_at=now - timedelta(minutes=38),
                    ended_at=now - timedelta(minutes=32),
                    duration_ms=360_000,
                ),
                JobStageRun(
                    job_id="job_eta_current",
                    stage_name="collecting_autohome",
                    attempt_no=1,
                    status="running",
                    started_at=now - timedelta(minutes=5),
                ),
                JobStageRun(
                    job_id="job_eta_current",
                    stage_name="collecting_dcd",
                    attempt_no=1,
                    status="running",
                    started_at=now - timedelta(minutes=5),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    progress_dir = tmp_path / "artifacts" / "job_eta_current" / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "collecting_autohome.progress.json").write_text(
        json.dumps({"percent": 50, "message": "汽车之家完成一半"}),
        encoding="utf-8",
    )
    (progress_dir / "collecting_dcd.progress.json").write_text(
        json.dumps({"percent": 25, "message": "懂车帝完成四分之一"}),
        encoding="utf-8",
    )

    response = client.get("/api/jobs/job_eta_current/progress")

    assert response.status_code == 200
    payload = response.json()
    assert payload["estimated_remaining_seconds"] == 1380
    assert payload["estimated_remaining_minutes"] == 23
    assert payload["eta_label"] == "预计剩余 23 分钟"
    assert payload["eta_confidence"] == "history"


def test_progress_endpoint_returns_zero_eta_for_terminal_job(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    session = get_session_local()()
    try:
        session.add(
            Job(
                job_id="job_eta_done",
                query="测试车",
                model_name="测试车",
                status="completed",
                current_stage="completed",
                degraded=False,
                passphrase_version="2026-W17",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.get("/api/jobs/job_eta_done/progress")

    assert response.status_code == 200
    payload = response.json()
    assert payload["estimated_remaining_seconds"] == 0
    assert payload["estimated_remaining_minutes"] == 0
    assert payload["eta_label"] == "预计剩余 0 分钟"
    assert payload["eta_confidence"] == "done"


def test_progress_endpoint_returns_running_fallback_when_collector_progress_file_is_missing(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    create_response = client.post(
        "/api/jobs",
        json={
            "query": "风云X3 PLUS",
            "selected_candidates": {
                "autohome": {"series_id": "8089", "title": "风云X3 PLUS", "source": "fixture"},
                "dongchedi": {"series_id": "25398", "title": "风云X3 PLUS", "source": "fixture"},
            },
        },
    )
    job_id = create_response.json()["job_id"]

    session = get_session_local()()
    try:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = "collecting_dcd"
        job.current_stage = "collecting_dcd"
        session.add_all(
            [
                JobStageRun(job_id=job_id, stage_name="collecting_autohome", attempt_no=1, status="running"),
                JobStageRun(job_id=job_id, stage_name="collecting_dcd", attempt_no=1, status="running"),
            ]
        )
        session.commit()
    finally:
        session.close()

    progress_response = client.get(f"/api/jobs/{job_id}/progress")

    assert progress_response.status_code == 200
    stages = {stage["name"]: stage for stage in progress_response.json()["stages"]}
    assert stages["collecting_autohome"]["progress_percent"] == 1
    assert stages["collecting_autohome"]["progress_message"] == "采集已启动，等待页面进度"
    assert stages["collecting_dcd"]["progress_percent"] == 1
    assert stages["collecting_dcd"]["progress_message"] == "采集已启动，等待页面进度"


def test_admin_failed_jobs_requires_passphrase_session(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)

    response = client.get("/api/admin/jobs/failed")

    assert response.status_code == 401
    assert response.json()["detail"] == "passphrase session required"


def test_admin_failed_delete_expires_failed_jobs_and_keeps_raw_corpus(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    artifact_root = tmp_path / "artifacts"
    failed_dir = artifact_root / "job_failed_old"
    failed_dir.mkdir(parents=True)
    (failed_dir / "raw.xlsx").write_text("job artifact", encoding="utf-8")

    session = get_session_local()()
    try:
        session.add(
            Job(
                job_id="job_failed_old",
                query="风云X3 PLUS",
                model_name="风云X3 PLUS",
                status="failed",
                current_stage="failed",
                degraded=False,
                passphrase_version="2026-W17",
            )
        )
        session.add(
            JobArtifact(
                job_id="job_failed_old",
                artifact_type="excel",
                artifact_path=str(failed_dir / "raw.xlsx"),
                source_stage="collecting_autohome",
            )
        )
        session.add(
            KoubeiRawComment(
                query_key="风云x3 plus",
                query="风云X3 PLUS",
                model_name="风云X3 PLUS",
                platform="autohome",
                series_id="8089",
                source_link="https://k.autohome.com.cn/detail/view_01abc.html",
                dedupe_key="link:https://k.autohome.com.cn/detail/view_01abc.html",
                row_json={"用户名": "tester", "评价详情": "历史评论"},
                first_seen_job_id="job_failed_old",
                last_seen_job_id="job_failed_old",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.delete("/api/admin/jobs/failed")

    assert response.status_code == 200
    payload = response.json()
    assert "job_failed_old" in payload["db_expired_job_ids"]
    assert not failed_dir.exists()

    session = get_session_local()()
    try:
        job = session.get(Job, "job_failed_old")
        assert job is not None
        assert job.status == "expired"
        assert session.query(JobArtifact).filter(JobArtifact.job_id == "job_failed_old").count() == 0
        assert session.query(KoubeiRawComment).filter(KoubeiRawComment.query_key == "风云x3 plus").count() == 1
    finally:
        session.close()
