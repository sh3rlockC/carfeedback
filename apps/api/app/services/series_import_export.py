from __future__ import annotations

import csv
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from sqlalchemy.orm import Session

from app.models import ConfirmedVehicleSeries, SeriesConflict, SeriesImportBatch
from app.services.confirmed_vehicle_series import PLATFORMS, query_key
from app.services.series_admin import now

IMPORT_SUMMARY_KEYS = ("new", "duplicate", "conflict", "invalid")
REQUIRED_FIELDS = ("query", "platform", "series_id")
OPTIONAL_FIELDS = ("url", "title", "source")
SUPPORTED_EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
HEADER_ERROR = "missing header row"


@dataclass(frozen=True)
class ImportRowPreview:
    row_number: int
    query: str | None
    platform: str | None
    series_id: str | None
    status: str
    query_key: str | None = None
    url: str | None = None
    title: str | None = None
    source: str | None = None
    error: str | None = None
    existing_value_json: dict[str, Any] | None = None
    incoming_value_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class ImportPreview:
    filename: str
    summary: dict[str, int]
    rows: list[ImportRowPreview]


def _cell_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_row(raw: dict[str, Any], row_number: int) -> dict[str, str]:
    row = {"_row_number": str(row_number)}
    for field in (*REQUIRED_FIELDS, *OPTIONAL_FIELDS):
        row[field] = _cell_to_text(raw.get(field))
    return row


def _validate_headers(headers: list[str]) -> None:
    normalized = {header.strip() for header in headers if header.strip()}
    if not normalized:
        raise ValueError(HEADER_ERROR)
    missing = [field for field in REQUIRED_FIELDS if field not in normalized]
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")


def _read_csv_rows(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise ValueError(HEADER_ERROR)
    _validate_headers(reader.fieldnames)
    return [_normalize_row(raw, index) for index, raw in enumerate(reader, start=2)]


def _read_excel_rows(content: bytes) -> list[dict[str, str]]:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    worksheet = workbook.active
    rows = worksheet.iter_rows(values_only=True)
    try:
        header_values = next(rows)
    except StopIteration:
        raise ValueError(HEADER_ERROR)

    headers = [_cell_to_text(value) for value in header_values]
    _validate_headers(headers)
    parsed_rows: list[dict[str, str]] = []
    for index, values in enumerate(rows, start=2):
        raw = {header: value for header, value in zip(headers, values, strict=False) if header}
        parsed_rows.append(_normalize_row(raw, index))
    return parsed_rows


def _read_rows(filename: str, content: bytes) -> list[dict[str, str]]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        return _read_csv_rows(content)
    if suffix in SUPPORTED_EXCEL_SUFFIXES:
        return _read_excel_rows(content)
    raise ValueError("series import supports .csv, .xlsx, and .xlsm files")


def _snapshot(record: ConfirmedVehicleSeries) -> dict[str, Any]:
    return {
        "id": record.id,
        "query_key": record.query_key,
        "query": record.query,
        "platform": record.platform,
        "series_id": record.series_id,
        "status": record.status,
        "url": record.url,
        "title": record.title,
        "source": record.source,
        "import_batch_id": record.import_batch_id,
    }


def _active_snapshot(record: ConfirmedVehicleSeries) -> dict[str, Any]:
    return _snapshot(record)


def _incoming(row: dict[str, str], key: str) -> dict[str, Any]:
    return {
        "query_key": key,
        "query": row["query"],
        "platform": row["platform"],
        "series_id": row["series_id"],
        "url": row["url"] or None,
        "title": row["title"] or None,
        "source": row["source"] or None,
    }


def _active_working_set(db: Session) -> dict[tuple[str, str], dict[str, Any]]:
    records = (
        db.query(ConfirmedVehicleSeries)
        .filter(ConfirmedVehicleSeries.status == "active")
        .order_by(ConfirmedVehicleSeries.query.asc(), ConfirmedVehicleSeries.platform.asc(), ConfirmedVehicleSeries.id.asc())
        .all()
    )
    return {(record.query_key, record.platform): _active_snapshot(record) for record in records}


def _invalid_reason(row: dict[str, str]) -> str | None:
    missing = [field for field in REQUIRED_FIELDS if not row[field]]
    if missing:
        return f"missing required field: {', '.join(missing)}"
    if row["platform"] not in PLATFORMS:
        return "unsupported platform"
    return None


def _classify_rows(db: Session, *, filename: str, content: bytes) -> ImportPreview:
    summary = {key: 0 for key in IMPORT_SUMMARY_KEYS}
    previews: list[ImportRowPreview] = []
    working_set = _active_working_set(db)

    for row in _read_rows(filename, content):
        row_number = int(row["_row_number"])
        error = _invalid_reason(row)
        key = query_key(row["query"]) if row["query"] else None
        if error is not None:
            summary["invalid"] += 1
            previews.append(
                ImportRowPreview(
                    row_number=row_number,
                    query=row["query"] or None,
                    platform=row["platform"] or None,
                    series_id=row["series_id"] or None,
                    query_key=key,
                    url=row["url"] or None,
                    title=row["title"] or None,
                    source=row["source"] or None,
                    status="invalid",
                    error=error,
                )
            )
            continue

        assert key is not None
        incoming = _incoming(row, key)
        identity = (key, row["platform"])
        existing_value = working_set.get(identity)
        if existing_value is None:
            status = "new"
            working_set[identity] = incoming
        elif existing_value["series_id"] == row["series_id"]:
            status = "duplicate"
        else:
            status = "conflict"

        summary[status] += 1
        previews.append(
            ImportRowPreview(
                row_number=row_number,
                query=row["query"],
                platform=row["platform"],
                series_id=row["series_id"],
                query_key=key,
                url=row["url"] or None,
                title=row["title"] or None,
                source=row["source"] or None,
                status=status,
                existing_value_json=existing_value,
                incoming_value_json=incoming,
            )
        )

    return ImportPreview(filename=filename, summary=summary, rows=previews)


def preview_series_import(db: Session, *, filename: str, content: bytes) -> ImportPreview:
    return _classify_rows(db, filename=filename, content=content)


def commit_series_import(db: Session, *, filename: str, content: bytes, operator: str) -> ImportPreview:
    preview = _classify_rows(db, filename=filename, content=content)
    batch = SeriesImportBatch(source="import", filename=filename, operator=operator, summary_json=preview.summary)
    db.add(batch)
    db.flush()

    for row in preview.rows:
        if row.status == "new":
            current_time = now()
            db.add(
                ConfirmedVehicleSeries(
                    query_key=row.query_key or "",
                    query=row.query or "",
                    platform=row.platform or "",
                    series_id=row.series_id or "",
                    status="active",
                    url=row.url,
                    title=row.title,
                    source=row.source,
                    import_batch_id=batch.id,
                    created_at=current_time,
                    updated_at=current_time,
                )
            )
        elif row.status == "conflict":
            db.add(
                SeriesConflict(
                    conflict_type="series_import",
                    query_key=row.query_key or "",
                    query=row.query or "",
                    platform=row.platform,
                    existing_value_json=row.existing_value_json or {},
                    incoming_value_json=row.incoming_value_json or {},
                    status="open",
                    import_batch_id=batch.id,
                    created_at=now(),
                )
            )

    db.commit()
    return preview


def export_series_excel(db: Session) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "confirmed_vehicle_series"
    worksheet.append(["query", "platform", "series_id", "url", "title", "source", "status", "updated_at"])

    records = (
        db.query(ConfirmedVehicleSeries)
        .order_by(
            ConfirmedVehicleSeries.query.asc(),
            ConfirmedVehicleSeries.platform.asc(),
            ConfirmedVehicleSeries.status.asc(),
            ConfirmedVehicleSeries.id.asc(),
        )
        .all()
    )
    for record in records:
        worksheet.append(
            [
                record.query,
                record.platform,
                record.series_id,
                record.url,
                record.title,
                record.source,
                record.status,
                record.updated_at.isoformat() if record.updated_at else None,
            ]
        )

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
