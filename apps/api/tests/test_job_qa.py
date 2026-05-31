from __future__ import annotations

import json
import sys
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
import app.services.qa_service as qa_service


class FakeQueue:
    def enqueue(self, func: str, kwargs: dict):
        raise AssertionError("queue should not be used in QA tests")


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'qa.db'}",
        pass_phrase_hash=hash_passphrase("weekly-secret"),
        pass_phrase_version="2026-W17",
        session_secret="test-secret",
        llm_provider="",
        llm_api_key="",
        llm_base_url="",
        llm_model_report="",
        llm_model_qa="",
        artifact_root=str(tmp_path / "artifacts"),
        workspace_root="/Users/xyc/Documents/codexwork",
    )
    app = create_app(settings)
    app.dependency_overrides[get_job_queue] = lambda: FakeQueue()
    return TestClient(app)


def seed_result_job(job_id: str, *, qa_chunks_path: Path | None = None, build_qa_chunks: bool = False) -> None:
    fixture_root = Path("/Users/xyc/Documents/codexwork/data/26.4.7/风云X3 PLUS")
    summary_path = fixture_root / "风云X3 PLUS_双平台口碑摘要.xlsx"
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
                artifact_path=str(summary_path),
                source_stage="summarizing",
            )
        ]
        if qa_chunks_path is not None:
            artifacts.append(
                JobArtifact(
                    job_id=job_id,
                    artifact_type="json",
                    artifact_path=str(qa_chunks_path),
                    source_stage="generating_hermes_outputs",
                )
            )
        session.add_all(artifacts)
        session.commit()
        if build_qa_chunks:
            qa_service.ensure_qa_chunks(
                session,
                job_id=job_id,
                summary_path=summary_path,
                model_name="风云X3 PLUS",
                hermes_chunks_path=qa_chunks_path,
            )
    finally:
        session.close()


def test_job_qa_uses_hermes_generated_chunks_when_available(tmp_path: Path) -> None:
    qa_chunks = tmp_path / "qa_chunks.json"
    qa_chunks.write_text(
        json.dumps(
            [
                {
                    "chunk_id": "hermes_strength_1",
                    "source_type": "hermes_evidence",
                    "text": "核心好评：用户最满意空间灵活、配置齐全。",
                    "tags": ["strength", "满意", "风云X3 PLUS"],
                    "metadata": {"source": "hermes"},
                }
            ],
            ensure_ascii=False,
        )
    )
    client = make_client(tmp_path)
    seed_result_job("job_qa_hermes_chunks", qa_chunks_path=qa_chunks)
    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.post(
        "/api/jobs/job_qa_hermes_chunks/qa",
        json={"question": "大家最满意什么？"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["insufficient_evidence"] is False
    assert "空间灵活" in payload["answer"]


def test_result_endpoint_marks_qa_available_after_chunk_build(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_result_job("job_qa_ready", build_qa_chunks=True)
    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.get("/api/jobs/job_qa_ready/result")
    assert response.status_code == 200
    payload = response.json()

    assert payload["qa_available"] is True


def test_job_qa_answers_grounded_question(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_result_job("job_qa_answer")
    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.post(
        "/api/jobs/job_qa_answer/qa",
        json={"question": "大家最不满意什么？"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["insufficient_evidence"] is False
    assert payload["citations"] == []
    assert payload["confidence"] in {"medium", "high"}
    assert payload["answer_source"] == "fallback"
    assert payload["model_used"] is None
    assert payload["llm_error"] == "llm_not_configured"
    assert "负向反馈" in payload["answer"] or "槽点" in payload["answer"] or "问题" in payload["answer"]


def test_job_qa_uses_llm_generated_answer_when_available(tmp_path: Path, monkeypatch) -> None:
    class FakeQAClient:
        def __init__(self) -> None:
            self.contexts: list[dict] = []

        def generate_answer(self, context: dict) -> str | None:
            self.contexts.append(context)
            return "LLM 生成：槽点主要集中在空间和内饰体验，需要优先处理。"

    fake_client = FakeQAClient()
    monkeypatch.setattr(qa_service, "build_qa_llm_client", lambda _settings: fake_client, raising=False)

    client = make_client(tmp_path)
    seed_result_job("job_qa_llm")
    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.post(
        "/api/jobs/job_qa_llm/qa",
        json={"question": "这款车主要槽点是什么？"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["answer"] == "LLM 生成：槽点主要集中在空间和内饰体验，需要优先处理。"
    assert payload["answer_source"] == "llm"
    assert payload["model_used"] is None
    assert payload["llm_error"] is None
    assert payload["citations"] == []
    assert fake_client.contexts
    assert fake_client.contexts[0]["question"] == "这款车主要槽点是什么？"
    assert fake_client.contexts[0]["evidence_chunks"]


def test_job_qa_audits_llm_fallback_when_client_returns_empty(tmp_path: Path, monkeypatch) -> None:
    class EmptyQAClient:
        def generate_answer(self, context: dict) -> str | None:
            return None

    monkeypatch.setattr(qa_service, "build_qa_llm_client", lambda _settings: EmptyQAClient(), raising=False)

    client = make_client(tmp_path)
    seed_result_job("job_qa_llm_empty")
    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.post(
        "/api/jobs/job_qa_llm_empty/qa",
        json={"question": "这款车主要槽点是什么？"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["answer_source"] == "fallback"
    assert payload["model_used"] is None
    assert payload["llm_error"] == "llm_empty_or_failed"


def test_job_qa_rejects_question_without_grounding(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_result_job("job_qa_unknown")
    verify = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify.status_code == 200

    response = client.post(
        "/api/jobs/job_qa_unknown/qa",
        json={"question": "明年销量会不会更高？"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["insufficient_evidence"] is True
    assert payload["citations"] == []
    assert payload["answer_source"] == "fallback"
    assert payload["llm_error"] == "insufficient_evidence"
