from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Job, JobAIReport, JobArtifact, JobQAChunk
from app.services.report_validator import validate_report_payload
from app.services.result_reader import read_summary_workbook, read_wordcloud_terms_workbook_or_empty


def _artifact_url(job_id: str, artifact_id: int) -> str:
    return f"/api/jobs/{job_id}/artifacts/{artifact_id}"


def _artifact_type_label(path: str) -> str:
    lower_path = path.lower()
    if lower_path.endswith("_双平台口碑摘要.xlsx"):
        return "summary_excel"
    if lower_path.endswith("_词云词项清单.xlsx"):
        return "wordcloud_terms_excel"
    if lower_path.endswith("final_report.json"):
        return "final_report_json"
    if lower_path.endswith("qa_chunks.json"):
        return "qa_chunks_json"
    if lower_path.endswith("analysis_facts.jsonl"):
        return "analysis_facts_jsonl"
    if lower_path.endswith("llm_metrics.json"):
        return "llm_metrics_json"
    if lower_path.endswith("优点词云.png"):
        return "wordcloud_positive"
    if lower_path.endswith("槽点词云.png"):
        return "wordcloud_negative"
    if lower_path.endswith(".validation.json"):
        return "validation_json"
    if lower_path.endswith(".xlsx"):
        return "excel"
    if lower_path.endswith(".png"):
        return "image_png"
    return "file"


def _read_ai_report_artifact(path: str | None) -> dict | None:
    if not path:
        return None
    artifact_path = Path(path)
    if not artifact_path.exists():
        return None
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    ok, _errors = validate_report_payload(payload) if isinstance(payload, dict) else (False, ["invalid report"])
    return payload if ok and isinstance(payload, dict) else None


def assemble_job_result(db: Session, settings: Settings, job_id: str) -> dict | None:
    job = db.get(Job, job_id)
    if job is None:
        return None

    artifacts = (
        db.query(JobArtifact)
        .filter(JobArtifact.job_id == job_id)
        .order_by(JobArtifact.id.asc())
        .all()
    )

    artifact_items = [
        {
            "id": artifact.id,
            "type": _artifact_type_label(artifact.artifact_path),
            "path": artifact.artifact_path,
            "url": artifact.artifact_url or _artifact_url(job_id, artifact.id),
            "source_stage": artifact.source_stage,
        }
        for artifact in artifacts
    ]

    summary_artifact = next((item for item in artifact_items if item["type"] == "summary_excel"), None)
    final_report_artifact = next((item for item in artifact_items if item["type"] == "final_report_json"), None)
    qa_chunks_artifact = next((item for item in artifact_items if item["type"] == "qa_chunks_json"), None)
    summary_data = read_summary_workbook(summary_artifact["path"]) if summary_artifact else None
    ai_report = (
        db.query(JobAIReport)
        .filter(JobAIReport.job_id == job_id)
        .order_by(JobAIReport.id.desc())
        .first()
    )
    ai_report_payload = ai_report.report_json if ai_report else _read_ai_report_artifact(final_report_artifact["path"] if final_report_artifact else None)

    qa_available = (
        db.query(JobQAChunk.id)
        .filter(JobQAChunk.job_id == job_id)
        .first()
        is not None
    ) or (
        qa_chunks_artifact is not None
        and Path(qa_chunks_artifact["path"]).exists()
    )

    positive_wordcloud = next((item for item in artifact_items if item["type"] == "wordcloud_positive"), None)
    negative_wordcloud = next((item for item in artifact_items if item["type"] == "wordcloud_negative"), None)
    term_excel = next((item for item in artifact_items if item["type"] == "wordcloud_terms_excel"), None)
    keyword_rankings = (
        read_wordcloud_terms_workbook_or_empty(term_excel["path"])
        if term_excel and Path(term_excel["path"]).exists()
        else {"positive": [], "negative": [], "combined": []}
    )

    return {
        "job_id": job.job_id,
        "status": job.status,
        "degraded": job.degraded,
        "model_name": job.model_name,
        "retention_days": settings.job_artifact_retention_days,
        "sample_summary": summary_data["sample_counts"] if summary_data else {"autohome_count": 0, "dcd_count": 0},
        "collection_summary": job.collection_summary or {},
        "template_report": {
            "title": summary_data["one_pager_lines"][0] if summary_data and summary_data["one_pager_lines"] else "",
            "highlights": summary_data["one_pager_lines"][1:8] if summary_data else [],
        },
        "structured_sections": {
            "overview": summary_data["overview_rows"] if summary_data else [],
            "compare": summary_data["compare_rows"] if summary_data else [],
            "business": summary_data["business_rows"] if summary_data else [],
            "opportunities": summary_data["opportunity_rows"] if summary_data else [],
        },
        "wordcloud": {
            "positive_image_url": positive_wordcloud["url"] if positive_wordcloud else None,
            "negative_image_url": negative_wordcloud["url"] if negative_wordcloud else None,
            "terms_excel_url": term_excel["url"] if term_excel else None,
            "keyword_rankings": keyword_rankings,
        },
        "artifacts": artifact_items,
        "ai_report": ai_report_payload,
        "ai_available": ai_report_payload is not None,
        "qa_available": qa_available,
    }
