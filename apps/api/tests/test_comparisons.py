from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import get_session_local
from app.main import create_app
from app.models import (
    ComparisonArtifact,
    ComparisonJob,
    ComparisonVehicle,
    ConfirmedVehicleSeries,
    Job,
    JobArtifact,
    JobCandidate,
    JobStageRun,
    Task,
)
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


def make_client(tmp_path: Path, *, app_env: str = "test") -> tuple[TestClient, FakeQueue]:
    settings = Settings(
        app_env=app_env,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'comparisons.db'}",
        pass_phrase_hash=hash_passphrase("weekly-secret"),
        pass_phrase_version="2026-W17",
        session_secret="test-secret",
        artifact_root=str(tmp_path / "artifacts"),
        workspace_root="/Users/xyc/Documents/codexwork",
        worker_job_timeout_seconds=2400,
    )
    app = create_app(settings)
    queue = FakeQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    return TestClient(app), queue


def test_legacy_comparison_creation_is_disabled_outside_test_env(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path, app_env="development")

    malformed_options_response = client.post("/api/comparisons/options", json={"query": "测试车A"})
    options_response = client.post("/api/comparisons/options", json={"vehicles": [{"query": "测试车A"}, {"query": "测试车B"}]})
    create_response = client.post(
        "/api/comparisons",
        json={
            "vehicles": [
                {"query": "测试车A", "selected_candidates": selected_candidates("1001", "2001", "测试车A")},
                {"query": "测试车B", "selected_candidates": selected_candidates("1002", "2002", "测试车B")},
            ]
        },
    )

    assert malformed_options_response.status_code == 410
    assert options_response.status_code == 410
    assert create_response.status_code == 410
    assert malformed_options_response.json()["detail"] == "legacy comparison API disabled; use /api/tasks"
    assert options_response.json()["detail"] == "legacy comparison API disabled; use /api/tasks"


def authorize(client: TestClient) -> None:
    response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert response.status_code == 200


def selected_candidates(autohome_id: str, dcd_id: str, title: str) -> dict:
    return {
        "autohome": {
            "series_id": autohome_id,
            "url": f"https://k.autohome.com.cn/{autohome_id}/",
            "title": title,
            "source": "fixture",
        },
        "dongchedi": {
            "series_id": dcd_id,
            "url": f"https://www.dongchedi.com/auto/series/{dcd_id}",
            "title": title,
            "source": "fixture",
        },
    }


def seed_confirmed_vehicle(query: str, autohome_id: str, dcd_id: str) -> None:
    session = get_session_local()()
    try:
        session.add_all(
            [
                ConfirmedVehicleSeries(
                    query_key=query.strip().lower(),
                    query=query,
                    platform="autohome",
                    series_id=autohome_id,
                    url=f"https://k.autohome.com.cn/{autohome_id}/",
                    title=query,
                    source="fixture",
                ),
                ConfirmedVehicleSeries(
                    query_key=query.strip().lower(),
                    query=query,
                    platform="dongchedi",
                    series_id=dcd_id,
                    url=f"https://www.dongchedi.com/auto/series/{dcd_id}",
                    title=query,
                    source="fixture",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()


def seed_completed_job(
    tmp_path: Path,
    *,
    job_id: str,
    query: str,
    model_name: str,
    finished_at: datetime,
    include_required_json: bool = True,
) -> dict[str, Path]:
    job_dir = tmp_path / "artifacts" / job_id / "outputs" / "ai"
    job_dir.mkdir(parents=True, exist_ok=True)
    final_report = job_dir / "final_report.json"
    analysis_facts = job_dir / "analysis_facts.jsonl"
    llm_metrics = job_dir / "llm_metrics.json"
    if include_required_json:
        final_report.write_text(json.dumps({"headline": f"{model_name} 结论"}, ensure_ascii=False), encoding="utf-8")
        analysis_facts.write_text(json.dumps({"comment_id": "autohome_0001", "model_name": model_name}, ensure_ascii=False) + "\n", encoding="utf-8")
    llm_metrics.write_text(json.dumps({"source": "fixture"}, ensure_ascii=False), encoding="utf-8")

    session = get_session_local()()
    try:
        session.add(
            Job(
                job_id=job_id,
                query=query,
                model_name=model_name,
                status="completed",
                current_stage="completed",
                degraded=False,
                passphrase_version="2026-W17",
                created_at=finished_at - timedelta(hours=1),
                started_at=finished_at - timedelta(hours=1),
                finished_at=finished_at,
            )
        )
        session.add_all(
            [
                JobCandidate(job_id=job_id, platform="autohome", series_id="1001", title=model_name, source="fixture", selected=True),
                JobCandidate(job_id=job_id, platform="dongchedi", series_id="2001", title=model_name, source="fixture", selected=True),
            ]
        )
        if include_required_json:
            session.add_all(
                [
                    JobArtifact(job_id=job_id, artifact_type="json", artifact_path=str(final_report), source_stage="generating_hermes_outputs"),
                    JobArtifact(job_id=job_id, artifact_type="jsonl", artifact_path=str(analysis_facts), source_stage="generating_hermes_outputs"),
                    JobArtifact(job_id=job_id, artifact_type="json", artifact_path=str(llm_metrics), source_stage="generating_hermes_outputs"),
                ]
            )
        session.commit()
    finally:
        session.close()
    return {"final_report": final_report, "analysis_facts": analysis_facts, "llm_metrics": llm_metrics}


def test_comparison_options_include_only_reusable_jobs_with_complete_json(tmp_path: Path) -> None:
    client, _queue = make_client(tmp_path)
    authorize(client)
    seed_confirmed_vehicle("测试车A", "1001", "2001")
    now = datetime.now(UTC)
    seed_completed_job(tmp_path, job_id="job_reusable", query="测试车A", model_name="测试车A", finished_at=now - timedelta(hours=2))
    seed_completed_job(tmp_path, job_id="job_expired", query="测试车A", model_name="测试车A", finished_at=now - timedelta(days=4))
    seed_completed_job(
        tmp_path,
        job_id="job_missing_json",
        query="测试车A",
        model_name="测试车A",
        finished_at=now - timedelta(hours=1),
        include_required_json=False,
    )

    response = client.post("/api/comparisons/options", json={"vehicles": [{"query": "测试车A"}, {"query": "测试车B"}]})

    assert response.status_code == 200
    payload = response.json()
    vehicle_a = payload["vehicles"][0]
    assert vehicle_a["query"] == "测试车A"
    assert vehicle_a["resolve"]["autohome"]["best"]["series_id"] == "1001"
    assert [item["job_id"] for item in vehicle_a["reuse_options"]] == ["job_reusable"]
    assert payload["vehicles"][1]["reuse_options"] == []


def test_create_comparison_validates_vehicle_count_and_enqueues_worker(tmp_path: Path) -> None:
    client, queue = make_client(tmp_path)
    authorize(client)

    invalid = client.post(
        "/api/comparisons",
        json={"vehicles": [{"query": "测试车A", "selected_candidates": selected_candidates("1001", "2001", "测试车A")}]},
    )
    assert invalid.status_code == 422

    response = client.post(
        "/api/comparisons",
        json={
            "vehicles": [
                {"query": "测试车A", "selected_candidates": selected_candidates("1001", "2001", "测试车A")},
                {"query": "测试车B", "selected_candidates": selected_candidates("1002", "2002", "测试车B")},
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["progress_url"] == f"/api/comparisons/{payload['comparison_id']}/progress"
    assert queue.calls[0]["func"] == "worker_jobs.run_comparison_job"
    assert queue.calls[0]["kwargs"]["comparison_id"] == payload["comparison_id"]


def test_comparison_progress_reports_reused_vehicle_with_zero_eta(tmp_path: Path) -> None:
    client, _queue = make_client(tmp_path)
    authorize(client)
    session = get_session_local()()
    try:
        comparison = ComparisonJob(
            comparison_id="cmp_eta",
            status="running",
            current_stage="collecting_models",
            passphrase_version="2026-W17",
            vehicle_count=2,
        )
        session.add(comparison)
        session.add_all(
            [
                ComparisonVehicle(
                    comparison_id="cmp_eta",
                    query="测试车A",
                    model_name="测试车A",
                    position=1,
                    status="reused",
                    source_job_id="job_reused",
                    selected_candidates=selected_candidates("1001", "2001", "测试车A"),
                ),
                ComparisonVehicle(
                    comparison_id="cmp_eta",
                    query="测试车B",
                    model_name="测试车B",
                    position=2,
                    status="queued",
                    selected_candidates=selected_candidates("1002", "2002", "测试车B"),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    response = client.get("/api/comparisons/cmp_eta/progress")

    assert response.status_code == 200
    payload = response.json()
    assert payload["estimated_remaining_minutes"] is not None
    reused = payload["vehicles"][0]
    assert reused["status"] == "reused"
    assert reused["estimated_remaining_seconds"] == 0
    assert reused["eta_label"] == "预计剩余 0 分钟"


def test_comparison_progress_and_result_expose_excluded_vehicle_details(tmp_path: Path) -> None:
    client, _queue = make_client(tmp_path)
    authorize(client)
    session = get_session_local()()
    try:
        session.add(
            ComparisonJob(
                comparison_id="cmp_degraded",
                status="completed_degraded",
                current_stage="completed_degraded",
                degraded=True,
                passphrase_version="2026-W17",
                vehicle_count=3,
                report_json={
                    "headline": "竞品口碑对比",
                    "vehicles": [{"model_name": "测试车A"}, {"model_name": "测试车B"}],
                },
            )
        )
        session.add_all(
            [
                ComparisonVehicle(
                    comparison_id="cmp_degraded",
                    query="测试车A",
                    model_name="测试车A",
                    position=1,
                    status="completed",
                    source_job_id="task_a",
                    selected_candidates=selected_candidates("1001", "2001", "测试车A"),
                ),
                ComparisonVehicle(
                    comparison_id="cmp_degraded",
                    query="测试车B",
                    model_name="测试车B",
                    position=2,
                    status="completed",
                    source_job_id="task_b",
                    selected_candidates=selected_candidates("1002", "2002", "测试车B"),
                ),
                ComparisonVehicle(
                    comparison_id="cmp_degraded",
                    query="零跑D19",
                    model_name="零跑D19",
                    position=3,
                    status="excluded",
                    child_job_id="task_child_failed",
                    selected_candidates={"autohome": {}, "dongchedi": {}},
                    error_code="series_not_found",
                    error_message="missing autohome_series_id and dcd_series_id",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    progress = client.get("/api/comparisons/cmp_degraded/progress")
    result = client.get("/api/comparisons/cmp_degraded")

    assert progress.status_code == 200
    progress_payload = progress.json()
    assert progress_payload["requested_vehicle_count"] == 3
    assert progress_payload["available_vehicle_count"] == 2
    assert progress_payload["excluded_vehicle_count"] == 1
    excluded = progress_payload["excluded_vehicles"][0]
    assert excluded["query"] == "零跑D19"
    assert excluded["status"] == "excluded"
    assert excluded["error_code"] == "series_not_found"
    assert excluded["error_message"] == "missing autohome_series_id and dcd_series_id"
    assert excluded["missing_platforms"] == ["autohome", "dongchedi"]
    assert excluded["child_task_id"] == "task_child_failed"

    assert result.status_code == 200
    result_payload = result.json()
    assert result_payload["requested_vehicle_count"] == 3
    assert result_payload["available_vehicle_count"] == 2
    assert result_payload["excluded_vehicle_count"] == 1
    assert result_payload["excluded_vehicles"][0]["error_code"] == "series_not_found"


def test_comparison_progress_uses_child_job_live_stage_progress(tmp_path: Path) -> None:
    client, _queue = make_client(tmp_path)
    authorize(client)
    progress_dir = tmp_path / "artifacts" / "job_child" / "progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    (progress_dir / "generating_hermes_outputs.progress.json").write_text(
        json.dumps({"percent": 70, "message": "Hermes 汇总批次结果"}, ensure_ascii=False),
        encoding="utf-8",
    )
    second_progress_dir = tmp_path / "artifacts" / "job_child_b" / "progress"
    second_progress_dir.mkdir(parents=True, exist_ok=True)
    (second_progress_dir / "generating_hermes_outputs.progress.json").write_text(
        json.dumps({"percent": 90, "message": "Hermes 汇总批次结果"}, ensure_ascii=False),
        encoding="utf-8",
    )

    session = get_session_local()()
    now = datetime.now(UTC)
    try:
        session.add(
            Job(
                job_id="job_child",
                query="测试车A",
                model_name="测试车A",
                status="generating_hermes_outputs",
                current_stage="generating_hermes_outputs",
                degraded=False,
                passphrase_version="2026-W17",
                started_at=now - timedelta(minutes=10),
            )
        )
        session.add(
            Job(
                job_id="job_child_b",
                query="测试车B",
                model_name="测试车B",
                status="generating_hermes_outputs",
                current_stage="generating_hermes_outputs",
                degraded=False,
                passphrase_version="2026-W17",
                started_at=now - timedelta(minutes=8),
            )
        )
        session.add_all(
            [
                JobStageRun(job_id="job_child", stage_name="collecting_autohome", status="success", duration_ms=1000),
                JobStageRun(job_id="job_child", stage_name="collecting_dcd", status="success", duration_ms=1000),
                JobStageRun(job_id="job_child", stage_name="postprocessing", status="success", duration_ms=1000),
                JobStageRun(job_id="job_child", stage_name="generating_hermes_outputs", status="running"),
                JobStageRun(job_id="job_child_b", stage_name="collecting_autohome", status="success", duration_ms=1000),
                JobStageRun(job_id="job_child_b", stage_name="collecting_dcd", status="success", duration_ms=1000),
                JobStageRun(job_id="job_child_b", stage_name="postprocessing", status="success", duration_ms=1000),
                JobStageRun(job_id="job_child_b", stage_name="generating_hermes_outputs", status="running"),
            ]
        )
        session.add(
            ComparisonJob(
                comparison_id="cmp_live_eta",
                status="running",
                current_stage="collecting_models",
                passphrase_version="2026-W17",
                vehicle_count=2,
            )
        )
        session.add_all(
            [
                ComparisonVehicle(
                    comparison_id="cmp_live_eta",
                    query="测试车A",
                    model_name="测试车A",
                    position=1,
                    status="running",
                    child_job_id="job_child",
                    selected_candidates=selected_candidates("1001", "2001", "测试车A"),
                ),
                ComparisonVehicle(
                    comparison_id="cmp_live_eta",
                    query="测试车B",
                    model_name="测试车B",
                    position=2,
                    status="running",
                    child_job_id="job_child_b",
                    selected_candidates=selected_candidates("1002", "2002", "测试车B"),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    response = client.get("/api/comparisons/cmp_live_eta/progress")

    assert response.status_code == 200
    payload = response.json()
    running = payload["vehicles"][0]
    second = payload["vehicles"][1]
    assert running["estimated_remaining_seconds"] == 270
    assert running["estimated_remaining_minutes"] == 5
    assert second["estimated_remaining_seconds"] == 90
    assert second["estimated_remaining_minutes"] == 2
    assert payload["estimated_remaining_seconds"] == 870
    assert payload["estimated_remaining_minutes"] == 15


def test_comparison_progress_uses_temporal_child_task_eta(tmp_path: Path) -> None:
    client, _queue = make_client(tmp_path)
    authorize(client)
    now = datetime.now(UTC)
    session = get_session_local()()
    try:
        session.add(
            Task(
                task_id="task_child_a",
                task_type="single_vehicle",
                display_name="测试车A",
                status="running",
                current_stage="collecting",
                degraded=False,
                upgraded_to_full=False,
                view_token_hash="view",
                manage_token_hash="manage",
                manage_token_expires_at=now + timedelta(days=7),
                eta_seconds=180,
                eta_reason="单车任务仍在采集",
                collection_mode="incremental",
            )
        )
        session.add(
            ComparisonJob(
                comparison_id="cmp_temporal_eta",
                status="running",
                current_stage="collecting_models",
                passphrase_version="2026-W17",
                vehicle_count=1,
            )
        )
        session.add(
            ComparisonVehicle(
                comparison_id="cmp_temporal_eta",
                query="测试车A",
                model_name="测试车A",
                position=1,
                status="running",
                child_job_id="task_child_a",
                selected_candidates=selected_candidates("1001", "2001", "测试车A"),
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.get("/api/comparisons/cmp_temporal_eta/progress")

    assert response.status_code == 200
    payload = response.json()
    assert payload["vehicles"][0]["estimated_remaining_seconds"] == 180
    assert payload["estimated_remaining_seconds"] == 780
    assert payload["estimated_remaining_minutes"] == 13


def test_comparison_progress_comparing_stage_keeps_summary_eta(tmp_path: Path) -> None:
    client, _queue = make_client(tmp_path)
    authorize(client)
    session = get_session_local()()
    try:
        session.add(
            ComparisonJob(
                comparison_id="cmp_summary_eta",
                status="running",
                current_stage="comparing",
                passphrase_version="2026-W17",
                vehicle_count=2,
            )
        )
        session.add_all(
            [
                ComparisonVehicle(
                    comparison_id="cmp_summary_eta",
                    query="测试车A",
                    model_name="测试车A",
                    position=1,
                    status="completed",
                    selected_candidates=selected_candidates("1001", "2001", "测试车A"),
                ),
                ComparisonVehicle(
                    comparison_id="cmp_summary_eta",
                    query="测试车B",
                    model_name="测试车B",
                    position=2,
                    status="completed",
                    selected_candidates=selected_candidates("1002", "2002", "测试车B"),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    response = client.get("/api/comparisons/cmp_summary_eta/progress")

    assert response.status_code == 200
    payload = response.json()
    assert payload["estimated_remaining_seconds"] == 600
    assert payload["estimated_remaining_minutes"] == 10


def test_get_comparison_result_returns_dimension_winner_fields(tmp_path: Path) -> None:
    client, _queue = make_client(tmp_path)
    authorize(client)
    session = get_session_local()()
    try:
        session.add(
            ComparisonJob(
                comparison_id="cmp_result_winner",
                status="completed",
                current_stage="completed",
                passphrase_version="2026-W17",
                vehicle_count=2,
                report_json={
                    "dimensions": [
                        {
                            "dimension": "空间",
                            "winner_model_names": ["车型A"],
                            "winner_score": 0.8,
                            "winner_label": "车型A",
                            "vehicles": [
                                {"model_name": "车型A", "positive_mentions": 4, "negative_mentions": 1},
                                {"model_name": "车型B", "positive_mentions": 3, "negative_mentions": 3},
                            ],
                        }
                    ]
                },
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.get("/api/comparisons/cmp_result_winner")

    assert response.status_code == 200
    dimension = response.json()["report_json"]["dimensions"][0]
    assert dimension["winner_model_names"] == ["车型A"]
    assert dimension["winner_score"] == 0.8
    assert dimension["winner_label"] == "车型A"


def test_comparison_zip_downloads_comparison_outputs_and_source_snapshots(tmp_path: Path) -> None:
    client, _queue = make_client(tmp_path)
    authorize(client)
    comparison_dir = tmp_path / "artifacts" / "cmp_zip" / "comparisons"
    vehicle_dir = comparison_dir / "测试车A"
    vehicle_dir.mkdir(parents=True)
    final_comparison = comparison_dir / "final_comparison.json"
    summary = comparison_dir / "comparison_summary.xlsx"
    dimension_matrix = comparison_dir / "comparison_dimension_matrix.xlsx"
    source_report = comparison_dir / "测试车A.final_report.json"
    source_facts = comparison_dir / "测试车A.analysis_facts.jsonl"
    metrics = comparison_dir / "llm_metrics.json"
    vehicle_summary = vehicle_dir / "测试车A_双平台口碑摘要.xlsx"
    vehicle_wordcloud = vehicle_dir / "测试车A_优点词云.png"
    final_comparison.write_text('{"headline":"竞品对比"}', encoding="utf-8")
    summary.write_text("xlsx-placeholder", encoding="utf-8")
    dimension_matrix.write_text("xlsx-placeholder", encoding="utf-8")
    source_report.write_text('{"headline":"测试车A"}', encoding="utf-8")
    source_facts.write_text('{"comment_id":"autohome_0001"}\n', encoding="utf-8")
    metrics.write_text('{"source":"fixture"}', encoding="utf-8")
    vehicle_summary.write_text("xlsx-placeholder", encoding="utf-8")
    vehicle_wordcloud.write_bytes(b"\x89PNG\r\n\x1a\nfixture")

    session = get_session_local()()
    try:
        session.add(
            ComparisonJob(
                comparison_id="cmp_zip",
                status="completed",
                current_stage="completed",
                passphrase_version="2026-W17",
                vehicle_count=2,
            )
        )
        session.add_all(
            [
                ComparisonArtifact(comparison_id="cmp_zip", artifact_type="comparison_json", artifact_path=str(final_comparison), source_stage="comparison"),
                ComparisonArtifact(comparison_id="cmp_zip", artifact_type="comparison_excel", artifact_path=str(summary), source_stage="comparison"),
                ComparisonArtifact(comparison_id="cmp_zip", artifact_type="comparison_dimension_excel", artifact_path=str(dimension_matrix), source_stage="comparison"),
                ComparisonArtifact(comparison_id="cmp_zip", artifact_type="source_final_report_json", artifact_path=str(source_report), source_stage="snapshot"),
                ComparisonArtifact(comparison_id="cmp_zip", artifact_type="source_analysis_facts_jsonl", artifact_path=str(source_facts), source_stage="snapshot"),
                ComparisonArtifact(comparison_id="cmp_zip", artifact_type="llm_metrics_json", artifact_path=str(metrics), source_stage="comparison"),
                ComparisonArtifact(comparison_id="cmp_zip", artifact_type="excel", artifact_path=str(vehicle_summary), source_stage="snapshot"),
                ComparisonArtifact(comparison_id="cmp_zip", artifact_type="image_png", artifact_path=str(vehicle_wordcloud), source_stage="snapshot"),
            ]
        )
        session.commit()
    finally:
        session.close()

    response = client.get("/api/comparisons/cmp_zip/artifacts.zip")

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())

    assert "comparison_summary.xlsx" in names
    assert "comparison_dimension_matrix.xlsx" in names
    assert "测试车A/测试车A_双平台口碑摘要.xlsx" in names
    assert "测试车A/测试车A_优点词云.png" in names
    assert not any(name.endswith((".json", ".jsonl")) for name in names)
