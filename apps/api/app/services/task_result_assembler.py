from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.models import Task, TaskArtifact
from app.services.result_reader import read_summary_workbook, read_wordcloud_terms_workbook_or_empty


def _artifact_url(task_id: str, artifact_id: int) -> str:
    return f"/api/tasks/{task_id}/artifacts/{artifact_id}"


def _path_name(path: str | Path) -> str:
    return Path(path).name


def _artifact_item(task_id: str, artifact: TaskArtifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "type": artifact.artifact_type,
        "path": artifact.path,
        "url": _artifact_url(task_id, artifact.id),
        "downloadable": artifact.downloadable,
        "created_at": artifact.created_at.isoformat() if artifact.created_at is not None else None,
    }


def _find_by_suffix(artifacts: list[TaskArtifact], *suffixes: str) -> TaskArtifact | None:
    lowered_suffixes = tuple(suffix.lower() for suffix in suffixes)
    for artifact in reversed(artifacts):
        if artifact.path.lower().endswith(lowered_suffixes):
            return artifact
    return None


def _find_png(artifacts: list[TaskArtifact], marker: str) -> TaskArtifact | None:
    for artifact in artifacts:
        path = artifact.path.lower()
        if path.endswith(".png") and marker in artifact.path:
            return artifact
    return None


def _read_json(path: str | Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _read_evidence_samples(path: str | Path, *, limit: int = 6) -> list[dict[str, str]]:
    samples: list[dict[str, str]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except Exception:
        return samples
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        text = (
            item.get("summary")
            or item.get("full_text")
            or item.get("positive_text")
            or item.get("negative_text")
            or item.get("text")
            or ""
        )
        if not text:
            sections = item.get("section_facts")
            if isinstance(sections, dict):
                text = " / ".join(str(value) for value in sections.values() if value)
        samples.append(
            {
                "comment_id": str(item.get("comment_id") or f"sample_{len(samples) + 1}"),
                "platform": str(item.get("platform") or item.get("source") or ""),
                "text": str(text)[:220],
            }
        )
        if len(samples) >= limit:
            break
    return samples


def _collection_summary(task: Task, sample_summary: dict[str, int]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {
        "autohome": {"existing_count": 0, "new_count": 0, "total_count": sample_summary.get("autohome_count", 0), "pages_scanned": 0, "mode": "", "stop_reason": None},
        "dongchedi": {"existing_count": 0, "new_count": 0, "total_count": sample_summary.get("dcd_count", 0), "pages_scanned": 0, "mode": "", "stop_reason": None},
    }
    for link in task.collection_run_links:
        run = link.run
        if run is None or run.platform not in summary:
            continue
        platform_summary = summary[run.platform]
        platform_summary["mode"] = run.mode
        for event in run.collector_events:
            payload = event.payload_json or {}
            if isinstance(payload, dict):
                pages = payload.get("pages_scanned") or payload.get("page_count")
                if pages is not None:
                    try:
                        platform_summary["pages_scanned"] = max(int(pages), int(platform_summary["pages_scanned"]))
                    except (TypeError, ValueError):
                        pass
                if payload.get("stop_reason"):
                    platform_summary["stop_reason"] = str(payload["stop_reason"])
    return summary


def task_result_payload(settings: Settings, task: Task) -> dict[str, Any]:
    artifacts = sorted(task.artifacts, key=lambda item: (item.created_at is None, item.created_at, item.id or 0))
    summary_artifact = _find_by_suffix(artifacts, "_双平台口碑摘要.xlsx")
    terms_artifact = _find_by_suffix(artifacts, "_词云词项清单.xlsx")
    final_report_artifact = _find_by_suffix(artifacts, "final_report.json")
    analysis_facts_artifact = _find_by_suffix(artifacts, "analysis_facts.jsonl")
    positive_wordcloud = _find_png(artifacts, "优点词云")
    negative_wordcloud = _find_png(artifacts, "槽点词云")
    pdf_artifact = _find_by_suffix(artifacts, ".pdf")

    summary_data = read_summary_workbook(summary_artifact.path) if summary_artifact and Path(summary_artifact.path).exists() else None
    ai_report = _read_json(final_report_artifact.path) if final_report_artifact else None
    keyword_rankings = (
        read_wordcloud_terms_workbook_or_empty(terms_artifact.path)
        if terms_artifact and Path(terms_artifact.path).exists()
        else {"positive": [], "negative": [], "combined": []}
    )
    sample_summary = summary_data["sample_counts"] if summary_data else {"autohome_count": 0, "dcd_count": 0}
    vehicle = sorted(task.vehicles, key=lambda item: (item.position, item.id or 0))[0] if task.vehicles else None
    model_name = vehicle.model_name if vehicle is not None else task.display_name
    evidence_samples = _read_evidence_samples(analysis_facts_artifact.path) if analysis_facts_artifact else []

    return {
        "task_id": task.task_id,
        "status": task.status,
        "current_stage": task.current_stage,
        "degraded": bool(task.degraded),
        "model_name": model_name,
        "display_name": task.display_name,
        "generated_at": task.completed_at.isoformat() if task.completed_at is not None else None,
        "report_ready": bool(summary_artifact and final_report_artifact and analysis_facts_artifact and pdf_artifact),
        "report_pdf_url": _artifact_url(task.task_id, pdf_artifact.id) if pdf_artifact else None,
        "zip_url": f"/api/tasks/{task.task_id}/artifacts.zip",
        "sample_summary": sample_summary,
        "collection_summary": _collection_summary(task, sample_summary),
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
            "positive_image_url": _artifact_url(task.task_id, positive_wordcloud.id) if positive_wordcloud else None,
            "negative_image_url": _artifact_url(task.task_id, negative_wordcloud.id) if negative_wordcloud else None,
            "terms_excel_url": _artifact_url(task.task_id, terms_artifact.id) if terms_artifact else None,
            "keyword_rankings": keyword_rankings,
        },
        "ai_report": ai_report,
        "ai_available": bool(ai_report and not ai_report.get("fallback")),
        "evidence_samples": evidence_samples,
        "artifacts": [_artifact_item(task.task_id, artifact) for artifact in artifacts],
        "retention_days": settings.job_artifact_retention_days,
    }


def task_result_bundle_entries(artifacts: list[TaskArtifact]) -> list[tuple[TaskArtifact, str]]:
    entries: list[tuple[TaskArtifact, str]] = []
    used: set[str] = set()
    for artifact in sorted(artifacts, key=lambda item: (item.created_at is None, item.created_at, item.id or 0)):
        path = Path(artifact.path)
        lower = path.name.lower()
        if lower.endswith(".pdf"):
            arcname = path.name
        elif lower == "final_report.json":
            arcname = "ai/final_report.json"
        elif lower == "analysis_facts.jsonl":
            arcname = "ai/analysis_facts.jsonl"
        elif lower == "llm_metrics.json":
            arcname = "ai/llm_metrics.json"
        elif lower == "qa_chunks.json":
            arcname = "ai/qa_chunks.json"
        elif lower.endswith("_双平台口碑摘要.xlsx"):
            arcname = f"summary/{path.name}"
        elif lower.endswith("_词云词项清单.xlsx"):
            arcname = f"wordcloud/{path.name}"
        elif lower.endswith(".png"):
            arcname = f"wordcloud/{path.name}"
        elif lower == "raw.xlsx" and path.parent.name in {"autohome", "dongchedi"}:
            arcname = f"raw/{path.parent.name}_raw.xlsx"
        elif lower.endswith(".xlsx"):
            arcname = f"data/{path.name}"
        else:
            arcname = f"artifacts/{path.name}"
        if arcname in used:
            arcname = f"{Path(arcname).parent}/{path.stem}_{artifact.id}{path.suffix}"
        used.add(arcname)
        entries.append((artifact, arcname))
    return entries
