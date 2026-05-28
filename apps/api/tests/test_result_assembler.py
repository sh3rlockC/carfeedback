from __future__ import annotations

import sys
import zipfile
import json
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import get_session_local
from app.main import create_app
from app.models import Job, JobArtifact
from app.services.job_queue import get_job_queue
from app.services.passphrase import hash_passphrase


class FakeQueue:
    def enqueue(self, func: str, kwargs: dict):
        raise AssertionError("queue should not be used in result tests")


def make_client(tmp_path: Path, *, raise_server_exceptions: bool = True) -> TestClient:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'result.db'}",
        pass_phrase_hash=hash_passphrase("weekly-secret"),
        pass_phrase_version="2026-W17",
        session_secret="test-secret",
        artifact_root=str(tmp_path / "artifacts"),
        workspace_root="/Users/xyc/Documents/codexwork",
    )
    app = create_app(settings)
    app.dependency_overrides[get_job_queue] = lambda: FakeQueue()
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def seed_result_job(
    job_id: str,
    *,
    term_excel_path: Path | None = None,
    final_report_path: Path | None = None,
    extra_artifact_paths: list[Path] | None = None,
) -> None:
    fixture_root = Path("/Users/xyc/Documents/codexwork/data/26.4.7/风云X3 PLUS")
    term_excel_path = term_excel_path or fixture_root / "风云X3 PLUS_词云词项清单.xlsx"
    session = get_session_local()()
    try:
        job = Job(
            job_id=job_id,
            query="风云X3 PLUS",
            model_name="风云X3 PLUS",
            status="completed",
            current_stage="completed",
            degraded=False,
            passphrase_version="2026-W17",
        )
        session.add(job)
        session.flush()
        artifacts = [
            JobArtifact(
                job_id=job_id,
                artifact_type="excel",
                artifact_path=str(fixture_root / "风云X3 PLUS_双平台口碑摘要.xlsx"),
                source_stage="summarizing",
            ),
            JobArtifact(
                job_id=job_id,
                artifact_type="image_png",
                artifact_path=str(fixture_root / "风云X3 PLUS_优点词云.png"),
                source_stage="rendering_wordcloud",
            ),
            JobArtifact(
                job_id=job_id,
                artifact_type="image_png",
                artifact_path=str(fixture_root / "风云X3 PLUS_槽点词云.png"),
                source_stage="rendering_wordcloud",
            ),
            JobArtifact(
                job_id=job_id,
                artifact_type="excel",
                artifact_path=str(term_excel_path),
                source_stage="rendering_wordcloud",
            ),
        ]
        if final_report_path is not None:
            artifacts.append(
                JobArtifact(
                    job_id=job_id,
                    artifact_type="json",
                    artifact_path=str(final_report_path),
                    source_stage="generating_hermes_outputs",
                )
            )
        for path in extra_artifact_paths or []:
            artifacts.append(
                JobArtifact(
                    job_id=job_id,
                    artifact_type="json",
                    artifact_path=str(path),
                    source_stage="generating_hermes_outputs",
                )
            )
        session.add_all(artifacts)
        session.commit()
    finally:
        session.close()


def seed_expired_job(job_id: str) -> None:
    session = get_session_local()()
    try:
        job = Job(
            job_id=job_id,
            query="风云X3 PLUS",
            model_name="风云X3 PLUS",
            status="expired",
            current_stage="expired",
            degraded=False,
            passphrase_version="2026-W17",
        )
        session.add(job)
        session.commit()
    finally:
        session.close()


def test_result_endpoint_assembles_summary_and_wordclouds(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_result_job("job_fixture")

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_fixture/result")
    assert response.status_code == 200
    payload = response.json()

    assert payload["model_name"] == "风云X3 PLUS"
    assert payload["sample_summary"] == {"autohome_count": 169, "dcd_count": 20}
    assert payload["template_report"]["title"] == "双平台口碑一页纸总结"
    assert payload["wordcloud"]["positive_image_url"].endswith("/api/jobs/job_fixture/artifacts/2")
    assert payload["wordcloud"]["negative_image_url"].endswith("/api/jobs/job_fixture/artifacts/3")
    rankings = payload["wordcloud"]["keyword_rankings"]
    assert [item["term"] for item in rankings["positive"][:3]] == ["外观设计", "空间", "配置性价比"]
    assert rankings["positive"][0]["count"] == 127
    assert [item["term"] for item in rankings["negative"][:3]] == ["内饰质感", "外观设计", "空间"]
    assert rankings["negative"][0]["count"] == 59
    assert rankings["combined"][0] == {"term": "外观设计", "count": 186}
    assert payload["artifacts"][0]["type"] == "summary_excel"
    assert payload["ai_available"] is True
    assert payload["ai_report"]["headline"]
    assert payload["qa_available"] is True
    assert payload["retention_days"] == 3


def test_result_endpoint_returns_expired_job_without_artifacts(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_expired_job("job_expired")

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_expired/result")
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "expired"
    assert payload["sample_summary"] == {"autohome_count": 0, "dcd_count": 0}
    assert payload["artifacts"] == []
    assert payload["ai_available"] is False
    assert payload["qa_available"] is False
    assert payload["retention_days"] == 3


def test_result_endpoint_includes_collection_summary(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    session = get_session_local()()
    try:
        session.add(
            Job(
                job_id="job_collection_summary",
                query="风云X3 PLUS",
                model_name="风云X3 PLUS",
                status="completed",
                current_stage="completed",
                degraded=False,
                passphrase_version="2026-W17",
                collection_summary={
                    "autohome": {
                        "existing_count": 10,
                        "new_count": 2,
                        "total_count": 12,
                        "pages_scanned": 3,
                        "mode": "incremental",
                    },
                    "dongchedi": {
                        "existing_count": 5,
                        "new_count": 0,
                        "total_count": 5,
                        "pages_scanned": 2,
                        "mode": "incremental",
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_collection_summary/result")

    assert response.status_code == 200
    payload = response.json()
    assert payload["collection_summary"]["autohome"]["existing_count"] == 10
    assert payload["collection_summary"]["autohome"]["new_count"] == 2
    assert payload["collection_summary"]["dongchedi"]["total_count"] == 5


def test_result_endpoint_ignores_unreadable_wordcloud_terms(tmp_path: Path) -> None:
    broken_terms = tmp_path / "broken_词云词项清单.xlsx"
    broken_terms.write_text("not an xlsx", encoding="utf-8")
    client = make_client(tmp_path, raise_server_exceptions=False)
    seed_result_job("job_broken_terms_result", term_excel_path=broken_terms)

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_broken_terms_result/result")

    assert response.status_code == 200
    payload = response.json()
    assert payload["template_report"]["title"] == "双平台口碑一页纸总结"
    assert payload["wordcloud"]["keyword_rankings"] == {"positive": [], "negative": [], "combined": []}


def test_result_endpoint_prefers_hermes_final_report_json(tmp_path: Path) -> None:
    final_report = tmp_path / "final_report.json"
    final_report.write_text(
        json.dumps(
            {
                "headline": "Hermes 生成的结论",
                "executive_summary": "来自原评论的大模型解读",
                "strength_blocks": [{"title": "核心好评", "summary": "空间和配置最被认可", "evidence_ids": ["hermes.1"]}],
                "weakness_blocks": [],
                "platform_difference_blocks": [],
                "action_blocks": [],
                "boss_brief": ["先讲核心好评", "再讲核心槽点", "最后讲动作建议"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    client = make_client(tmp_path)
    seed_result_job("job_hermes_report", final_report_path=final_report)

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_hermes_report/result")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ai_report"]["headline"] == "Hermes 生成的结论"
    assert payload["ai_report"]["strength_blocks"][0]["title"] == "核心好评"
    assert any(item["type"] == "final_report_json" for item in payload["artifacts"])


def test_artifact_download_serves_file(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_result_job("job_download")

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_download/artifacts/1")
    assert response.status_code == 200
    assert "attachment" in response.headers.get("content-disposition", "").lower()


def test_result_zip_download_bundles_excel_and_wordcloud_files(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_result_job("job_zip")

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_zip/artifacts.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers.get("content-disposition", "").lower()

    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        rank_pngs = {
            "keyword_rank_positive.png": archive.read("keyword_rank_positive.png"),
            "keyword_rank_negative.png": archive.read("keyword_rank_negative.png"),
            "keyword_rank_combined.png": archive.read("keyword_rank_combined.png"),
        }

    assert "风云X3 PLUS_双平台口碑摘要.xlsx" in names
    assert "风云X3 PLUS_优点词云.png" in names
    assert "风云X3 PLUS_槽点词云.png" in names
    assert "风云X3 PLUS_词云词项清单.xlsx" in names
    assert "keyword_rank_positive.png" in names
    assert "keyword_rank_negative.png" in names
    assert "keyword_rank_combined.png" in names
    assert all(content.startswith(b"\x89PNG\r\n\x1a\n") for content in rank_pngs.values())


def test_result_zip_download_excludes_hermes_final_report_json(tmp_path: Path) -> None:
    final_report = tmp_path / "final_report.json"
    final_report.write_text('{"headline":"Hermes"}', encoding="utf-8")
    client = make_client(tmp_path)
    seed_result_job("job_zip_hermes", final_report_path=final_report)

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_zip_hermes/artifacts.zip")

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())

    assert "final_report.json" not in names


def test_result_zip_download_excludes_hermes_facts_and_metrics(tmp_path: Path) -> None:
    final_report = tmp_path / "final_report.json"
    analysis_facts = tmp_path / "analysis_facts.jsonl"
    llm_metrics = tmp_path / "llm_metrics.json"
    final_report.write_text('{"headline":"Hermes"}', encoding="utf-8")
    analysis_facts.write_text('{"comment_id":"autohome_0001"}\n', encoding="utf-8")
    llm_metrics.write_text('{"source":"hermes-deepseek-api"}', encoding="utf-8")
    client = make_client(tmp_path)
    seed_result_job(
        "job_zip_hermes_metrics",
        final_report_path=final_report,
        extra_artifact_paths=[analysis_facts, llm_metrics],
    )

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_zip_hermes_metrics/artifacts.zip")

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())

    assert "analysis_facts.jsonl" not in names
    assert "llm_metrics.json" not in names


def test_result_zip_download_skips_keyword_pngs_when_terms_excel_is_unreadable(tmp_path: Path) -> None:
    broken_terms = tmp_path / "broken_词云词项清单.xlsx"
    broken_terms.write_text("not an xlsx", encoding="utf-8")
    client = make_client(tmp_path, raise_server_exceptions=False)
    seed_result_job("job_broken_terms_zip", term_excel_path=broken_terms)

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_broken_terms_zip/artifacts.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())

    assert "风云X3 PLUS_双平台口碑摘要.xlsx" in names
    assert "风云X3 PLUS_优点词云.png" in names
    assert "风云X3 PLUS_槽点词云.png" in names
    assert "broken_词云词项清单.xlsx" in names
    assert "keyword_rank_positive.png" not in names
    assert "keyword_rank_negative.png" not in names
    assert "keyword_rank_combined.png" not in names


def test_result_zip_download_skips_keyword_pngs_when_rendering_fails(monkeypatch, tmp_path: Path) -> None:
    def fail_render(_rankings):
        raise RuntimeError("png render failed")

    monkeypatch.setattr("app.routes.jobs.build_keyword_rank_pngs", fail_render)
    client = make_client(tmp_path, raise_server_exceptions=False)
    seed_result_job("job_rank_render_failed")

    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_rank_render_failed/artifacts.zip")

    assert response.status_code == 200
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        names = set(archive.namelist())

    assert "风云X3 PLUS_双平台口碑摘要.xlsx" in names
    assert "风云X3 PLUS_优点词云.png" in names
    assert "风云X3 PLUS_槽点词云.png" in names
    assert "风云X3 PLUS_词云词项清单.xlsx" in names
    assert "keyword_rank_positive.png" not in names
    assert "keyword_rank_negative.png" not in names
    assert "keyword_rank_combined.png" not in names
