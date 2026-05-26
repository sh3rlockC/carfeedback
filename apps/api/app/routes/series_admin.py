from __future__ import annotations

from email.parser import BytesParser
from email.policy import default as email_policy

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import ConfirmedVehicleSeries, SeriesAlias, SeriesAuditLog
from app.schemas import (
    SeriesActionRequest,
    SeriesAliasListResponse,
    SeriesAliasRequest,
    SeriesAliasResponse,
    SeriesAuditResponse,
    SeriesImportPreviewResponse,
    SeriesListResponse,
    SeriesMutationRequest,
    SeriesRecordResponse,
)
from app.services.passphrase import require_passphrase_session
from app.services.confirmed_vehicle_series import query_key
from app.services.series_admin import (
    SeriesMutation,
    create_series_record,
    list_series_records,
    restore_series_record,
    soft_delete_series_record,
    update_series_record,
)
from app.services.series_import_export import commit_series_import, export_series_excel, preview_series_import
from app.services.vehicle_aliases import create_alias, delete_alias, update_alias

MAX_PAGE_SIZE = 200
MAX_IMPORT_UPLOAD_BYTES = 5 * 1024 * 1024
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _require_admin_access(request: Request, settings: Settings = Depends(get_settings)) -> None:
    require_passphrase_session(request, settings)


router = APIRouter(prefix="/api/admin/series", tags=["series-admin"], dependencies=[Depends(_require_admin_access)])


def _series_mutation(payload: SeriesMutationRequest) -> SeriesMutation:
    return SeriesMutation(
        query=payload.query,
        platform=payload.platform,
        series_id=payload.series_id,
        operator=payload.operator,
        reason=payload.reason,
        url=payload.url,
        title=payload.title,
        source=payload.source,
    )


def _not_found_or_bad_request(exc: ValueError) -> HTTPException:
    message = str(exc)
    if "not found" in message:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _page(limit: int, offset: int) -> tuple[int, int]:
    return min(max(0, limit), MAX_PAGE_SIZE), max(0, offset)


def _active_conflict(db: Session, *, query: str, platform: str, exclude_id: int | None = None) -> ConfirmedVehicleSeries | None:
    series_query = db.query(ConfirmedVehicleSeries).filter(
        ConfirmedVehicleSeries.query_key == query_key(query),
        ConfirmedVehicleSeries.platform == platform,
        ConfirmedVehicleSeries.status == "active",
    )
    if exclude_id is not None:
        series_query = series_query.filter(ConfirmedVehicleSeries.id != exclude_id)
    return series_query.one_or_none()


def _raise_conflict(message: str) -> None:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _audit_payload(row: SeriesAuditLog) -> dict:
    return {
        "id": row.id,
        "record_id": row.record_id,
        "action": row.action,
        "operator": row.operator,
        "reason": row.reason,
        "old_value": row.old_value_json,
        "new_value": row.new_value_json,
        "created_at": row.created_at,
    }


async def _read_import_upload(request: Request) -> tuple[str, bytes]:
    content_length = request.headers.get("content-length")
    try:
        if content_length and int(content_length) > MAX_IMPORT_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="series import file is too large")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid content-length")

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IMPORT_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="series import file is too large")
        chunks.append(chunk)
    body = b"".join(chunks)

    content_type = request.headers.get("content-type", "")
    if content_type.lower().startswith("multipart/form-data"):
        message = BytesParser(policy=email_policy).parsebytes(
            b"Content-Type: "
            + content_type.encode("latin-1")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + body
        )
        if not message.is_multipart():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid multipart upload")
        upload: tuple[str, bytes] | None = None
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                continue
            if part.get_param("name", header="content-disposition") != "file":
                continue
            if upload is not None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="multiple file fields are not supported")
            if part.get("content-transfer-encoding"):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="content-transfer-encoding is not supported")
            filename = (part.get_filename() or "").strip()
            if not filename:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing filename")
            upload = (filename, part.get_payload(decode=True) or b"")
        if upload is not None:
            return upload
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing file field")

    filename = request.headers.get("x-filename") or request.headers.get("filename")
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing filename")
    return filename, body


@router.get("", response_model=SeriesListResponse)
def list_series(
    search: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> SeriesListResponse:
    normalized_limit, normalized_offset = _page(limit, offset)
    result = list_series_records(
        db,
        search=search,
        platform=platform,
        status=status,
        source=source,
        limit=normalized_limit,
        offset=normalized_offset,
    )
    return SeriesListResponse(items=result.items, total=result.total, limit=result.limit, offset=result.offset)


@router.post("", response_model=SeriesRecordResponse, status_code=status.HTTP_201_CREATED)
def create_series(
    payload: SeriesMutationRequest,
    db: Session = Depends(get_db),
) -> SeriesRecordResponse:
    try:
        if _active_conflict(db, query=payload.query, platform=payload.platform) is not None:
            create_series_record(db, _series_mutation(payload))
            db.commit()
            _raise_conflict("active series already exists for query and platform")
        record = create_series_record(db, _series_mutation(payload))
        db.commit()
        db.refresh(record)
        return record
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.post("/import/preview", response_model=SeriesImportPreviewResponse)
async def preview_series_import_route(
    request: Request,
    db: Session = Depends(get_db),
) -> SeriesImportPreviewResponse:
    filename, content = await _read_import_upload(request)
    try:
        return preview_series_import(db, filename=filename, content=content)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/import/commit", response_model=SeriesImportPreviewResponse)
async def commit_series_import_route(
    request: Request,
    operator: str,
    db: Session = Depends(get_db),
) -> SeriesImportPreviewResponse:
    normalized_operator = operator.strip()
    if not normalized_operator:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="operator must not be blank")
    filename, content = await _read_import_upload(request)
    try:
        preview = commit_series_import(db, filename=filename, content=content, operator=normalized_operator)
        db.commit()
        return preview
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/export.xlsx")
def export_series_route(db: Session = Depends(get_db)) -> Response:
    content = export_series_excel(db)
    return Response(
        content=content,
        media_type=EXCEL_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="confirmed_vehicle_series.xlsx"'},
    )


@router.get("/audit", response_model=SeriesAuditResponse)
def list_series_audit(
    record_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> SeriesAuditResponse:
    normalized_limit, normalized_offset = _page(limit, offset)
    query = db.query(SeriesAuditLog)
    if record_id is not None:
        query = query.filter(SeriesAuditLog.record_id == record_id)
    total = query.count()
    rows = query.order_by(SeriesAuditLog.id.asc()).offset(normalized_offset).limit(normalized_limit).all()
    return SeriesAuditResponse(
        items=[_audit_payload(row) for row in rows],
        total=total,
        limit=normalized_limit,
        offset=normalized_offset,
    )


@router.get("/aliases", response_model=SeriesAliasListResponse)
def list_aliases(
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> SeriesAliasListResponse:
    query = db.query(SeriesAlias)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                SeriesAlias.alias.ilike(pattern),
                SeriesAlias.alias_key.ilike(pattern),
                SeriesAlias.canonical_query.ilike(pattern),
            )
        )
    normalized_limit, normalized_offset = _page(limit, offset)
    total = query.count()
    items = (
        query.order_by(SeriesAlias.updated_at.desc(), SeriesAlias.id.desc())
        .offset(normalized_offset)
        .limit(normalized_limit)
        .all()
    )
    return SeriesAliasListResponse(items=items, total=total, limit=normalized_limit, offset=normalized_offset)


@router.post("/aliases", response_model=SeriesAliasResponse, status_code=status.HTTP_201_CREATED)
def create_series_alias(
    payload: SeriesAliasRequest,
    db: Session = Depends(get_db),
) -> SeriesAliasResponse:
    try:
        record = create_alias(db, alias=payload.alias, canonical_query=payload.canonical_query)
        db.commit()
        db.refresh(record)
        return record
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.patch("/aliases/{alias_id}", response_model=SeriesAliasResponse)
def update_series_alias(
    alias_id: int,
    payload: SeriesAliasRequest,
    db: Session = Depends(get_db),
) -> SeriesAliasResponse:
    try:
        record = update_alias(db, alias_id, alias=payload.alias, canonical_query=payload.canonical_query)
        db.commit()
        db.refresh(record)
        return record
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_series_alias(
    alias_id: int,
    db: Session = Depends(get_db),
) -> Response:
    try:
        delete_alias(db, alias_id)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.patch("/{record_id}", response_model=SeriesRecordResponse)
def update_series(
    record_id: int,
    payload: SeriesMutationRequest,
    db: Session = Depends(get_db),
) -> SeriesRecordResponse:
    try:
        if _active_conflict(db, query=payload.query, platform=payload.platform, exclude_id=record_id) is not None:
            update_series_record(db, record_id, _series_mutation(payload))
            db.commit()
            _raise_conflict("active series already exists for query and platform")
        record = update_series_record(db, record_id, _series_mutation(payload))
        db.commit()
        db.refresh(record)
        return record
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.delete("/{record_id}", response_model=SeriesRecordResponse)
def delete_series(
    record_id: int,
    payload: SeriesActionRequest,
    db: Session = Depends(get_db),
) -> SeriesRecordResponse:
    try:
        record = soft_delete_series_record(db, record_id, operator=payload.operator, reason=payload.reason)
        db.commit()
        db.refresh(record)
        return record
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.post("/{record_id}/restore", response_model=SeriesRecordResponse)
def restore_series(
    record_id: int,
    payload: SeriesActionRequest,
    db: Session = Depends(get_db),
) -> SeriesRecordResponse:
    try:
        record = db.get(ConfirmedVehicleSeries, record_id)
        if record is not None and _active_conflict(db, query=record.query, platform=record.platform, exclude_id=record_id) is not None:
            restore_series_record(db, record_id, operator=payload.operator, reason=payload.reason)
            db.commit()
            _raise_conflict("active series already exists for query and platform")
        record = restore_series_record(db, record_id, operator=payload.operator, reason=payload.reason)
        db.commit()
        db.refresh(record)
        return record
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc
