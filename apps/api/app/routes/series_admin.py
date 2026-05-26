from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import SeriesAlias, SeriesAuditLog
from app.schemas import (
    SeriesActionRequest,
    SeriesAliasListResponse,
    SeriesAliasRequest,
    SeriesAliasResponse,
    SeriesAuditResponse,
    SeriesListResponse,
    SeriesMutationRequest,
    SeriesRecordResponse,
)
from app.services.passphrase import require_passphrase_session
from app.services.series_admin import (
    SeriesMutation,
    create_series_record,
    list_series_records,
    restore_series_record,
    soft_delete_series_record,
    update_series_record,
)
from app.services.vehicle_aliases import create_alias, delete_alias, update_alias

router = APIRouter(prefix="/api/admin/series", tags=["series-admin"])


def _require_admin_access(request: Request, settings: Settings) -> None:
    require_passphrase_session(request, settings)


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


@router.get("", response_model=SeriesListResponse)
def list_series(
    request: Request,
    search: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesListResponse:
    _require_admin_access(request, settings)
    result = list_series_records(
        db,
        search=search,
        platform=platform,
        status=status,
        source=source,
        limit=limit,
        offset=offset,
    )
    return SeriesListResponse(items=result.items, total=result.total, limit=result.limit, offset=result.offset)


@router.post("", response_model=SeriesRecordResponse, status_code=status.HTTP_201_CREATED)
def create_series(
    payload: SeriesMutationRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesRecordResponse:
    _require_admin_access(request, settings)
    try:
        record = create_series_record(db, _series_mutation(payload))
        db.commit()
        db.refresh(record)
        return record
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc


@router.get("/audit", response_model=SeriesAuditResponse)
def list_series_audit(
    request: Request,
    record_id: int | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesAuditResponse:
    _require_admin_access(request, settings)
    query = db.query(SeriesAuditLog)
    if record_id is not None:
        query = query.filter(SeriesAuditLog.record_id == record_id)
    rows = query.order_by(SeriesAuditLog.id.asc()).all()
    return SeriesAuditResponse(items=[_audit_payload(row) for row in rows])


@router.get("/aliases", response_model=SeriesAliasListResponse)
def list_aliases(
    request: Request,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesAliasListResponse:
    _require_admin_access(request, settings)
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
    normalized_limit = max(0, limit)
    normalized_offset = max(0, offset)
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
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesAliasResponse:
    _require_admin_access(request, settings)
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
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesAliasResponse:
    _require_admin_access(request, settings)
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
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    _require_admin_access(request, settings)
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
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesRecordResponse:
    _require_admin_access(request, settings)
    try:
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
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesRecordResponse:
    _require_admin_access(request, settings)
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
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SeriesRecordResponse:
    _require_admin_access(request, settings)
    try:
        record = restore_series_record(db, record_id, operator=payload.operator, reason=payload.reason)
        db.commit()
        db.refresh(record)
        return record
    except ValueError as exc:
        db.rollback()
        raise _not_found_or_bad_request(exc) from exc
