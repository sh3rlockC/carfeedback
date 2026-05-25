from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from worker_app.corpus import load_platform_state, read_workbook_rows, sync_vehicle_corpus_files, upsert_platform_rows


def _platform_summary_for_skip(
    *,
    database_url: str,
    query: str,
    platform: str,
    series_id: str,
    source_path: Path | None,
    reason: str,
) -> dict[str, Any]:
    state = load_platform_state(database_url, query=query, platform=platform, series_id=series_id)
    return {
        "platform": platform,
        "series_id": str(series_id),
        "source_path": str(source_path) if source_path is not None else None,
        "skipped": True,
        "reason": reason,
        "read_count": 0,
        "inserted_count": 0,
        "updated_count": 0,
        "total_count": state.existing_count,
    }


def _backfill_platform(
    *,
    database_url: str,
    query: str,
    model_name: str,
    platform: str,
    series_id: str,
    xlsx_path: str | Path | None,
    job_id: str,
) -> dict[str, Any]:
    if xlsx_path is None:
        return _platform_summary_for_skip(
            database_url=database_url,
            query=query,
            platform=platform,
            series_id=series_id,
            source_path=None,
            reason="not_provided",
        )

    source_path = Path(xlsx_path).expanduser().resolve()
    if not source_path.is_file():
        return _platform_summary_for_skip(
            database_url=database_url,
            query=query,
            platform=platform,
            series_id=series_id,
            source_path=source_path,
            reason="missing_file",
        )

    rows = read_workbook_rows(source_path)
    result = upsert_platform_rows(
        database_url=database_url,
        query=query,
        model_name=model_name,
        platform=platform,
        series_id=str(series_id),
        job_id=job_id,
        rows=rows,
    )
    return {
        "platform": platform,
        "series_id": str(series_id),
        "source_path": str(source_path),
        "skipped": False,
        "reason": None,
        "read_count": len(rows),
        "inserted_count": result.inserted_count,
        "updated_count": result.updated_count,
        "total_count": result.total_count,
    }


def _write_summary_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def backfill_raw_excels(
    *,
    database_url: str,
    corpus_root: str | Path,
    model_name: str,
    query: str,
    autohome_series_id: str,
    autohome_xlsx: str | Path | None,
    dcd_series_id: str,
    dcd_xlsx: str | Path | None,
    summary_json_path: str | Path | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    backfill_job_id = job_id or f"backfill_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    platforms = {
        "autohome": _backfill_platform(
            database_url=database_url,
            query=query,
            model_name=model_name,
            platform="autohome",
            series_id=str(autohome_series_id),
            xlsx_path=autohome_xlsx,
            job_id=backfill_job_id,
        ),
        "dongchedi": _backfill_platform(
            database_url=database_url,
            query=query,
            model_name=model_name,
            platform="dongchedi",
            series_id=str(dcd_series_id),
            xlsx_path=dcd_xlsx,
            job_id=backfill_job_id,
        ),
    }
    corpus_summary = sync_vehicle_corpus_files(
        database_url=database_url,
        corpus_root=corpus_root,
        query=query,
        model_name=model_name,
        autohome_series_id=str(autohome_series_id),
        dongchedi_series_id=str(dcd_series_id),
    )
    default_summary_path = Path(corpus_summary["vehicle_dir"]) / "backfill-summary.json"
    output_path = Path(summary_json_path).expanduser().resolve() if summary_json_path is not None else default_summary_path
    payload = {
        "event_type": "raw_excel_backfill",
        "job_id": backfill_job_id,
        "query": query,
        "model_name": model_name,
        "created_at": datetime.now(UTC).isoformat(),
        "platforms": platforms,
        "corpus": corpus_summary,
        "summary_json_path": str(output_path),
    }
    _write_summary_json(output_path, payload)
    return payload
