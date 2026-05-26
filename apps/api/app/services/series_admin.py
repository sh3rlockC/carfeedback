from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import ConfirmedVehicleSeries, SeriesAuditLog, SeriesConflict
from app.services.confirmed_vehicle_series import query_key


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SeriesMutation:
    query: str
    platform: str
    series_id: str
    operator: str
    reason: str
    url: str | None = None
    title: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class SeriesListResult:
    items: list[ConfirmedVehicleSeries]
    total: int
    limit: int
    offset: int


def _snapshot(record: ConfirmedVehicleSeries | None) -> dict[str, Any]:
    if record is None:
        return {}

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
        "deleted_at": record.deleted_at.isoformat() if record.deleted_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _mutation_snapshot(mutation: SeriesMutation, *, record_id: int | None = None) -> dict[str, Any]:
    return {
        "id": record_id,
        "query_key": query_key(mutation.query),
        "query": mutation.query.strip(),
        "platform": mutation.platform.strip(),
        "series_id": mutation.series_id.strip(),
        "status": "active",
        "url": mutation.url,
        "title": mutation.title,
        "source": mutation.source,
    }


def _audit(
    db: Session,
    *,
    record_id: int | None,
    action: str,
    operator: str,
    reason: str,
    old_value: dict[str, Any],
    new_value: dict[str, Any],
) -> None:
    db.add(
        SeriesAuditLog(
            record_id=record_id,
            action=action,
            operator=operator,
            reason=reason,
            old_value_json=old_value,
            new_value_json=new_value,
            created_at=now(),
        )
    )


def _active_conflict(
    db: Session,
    *,
    query_key_value: str,
    platform: str,
    exclude_id: int | None = None,
) -> ConfirmedVehicleSeries | None:
    query = db.query(ConfirmedVehicleSeries).filter(
        ConfirmedVehicleSeries.query_key == query_key_value,
        ConfirmedVehicleSeries.platform == platform,
        ConfirmedVehicleSeries.status == "active",
    )
    if exclude_id is not None:
        query = query.filter(ConfirmedVehicleSeries.id != exclude_id)
    return query.one_or_none()


def _record_conflict(
    db: Session,
    *,
    existing: ConfirmedVehicleSeries,
    incoming: dict[str, Any],
    query: str,
    query_key_value: str,
    platform: str,
) -> None:
    db.add(
        SeriesConflict(
            conflict_type="query_platform",
            query_key=query_key_value,
            query=query,
            platform=platform,
            existing_value_json=_snapshot(existing),
            incoming_value_json=incoming,
            status="open",
            created_at=now(),
        )
    )


def create_series_record(db: Session, mutation: SeriesMutation) -> ConfirmedVehicleSeries:
    normalized_query = mutation.query.strip()
    normalized_platform = mutation.platform.strip()
    key = query_key(normalized_query)
    existing = _active_conflict(db, query_key_value=key, platform=normalized_platform)
    if existing is not None:
        _record_conflict(
            db,
            existing=existing,
            incoming=_mutation_snapshot(mutation),
            query=normalized_query,
            query_key_value=key,
            platform=normalized_platform,
        )
        db.flush()
        return existing

    current_time = now()
    record = ConfirmedVehicleSeries(
        query_key=key,
        query=normalized_query,
        platform=normalized_platform,
        series_id=mutation.series_id.strip(),
        status="active",
        url=mutation.url,
        title=mutation.title,
        source=mutation.source,
        created_at=current_time,
        updated_at=current_time,
    )
    db.add(record)
    db.flush()
    _audit(
        db,
        record_id=record.id,
        action="create",
        operator=mutation.operator,
        reason=mutation.reason,
        old_value={},
        new_value=_snapshot(record),
    )
    db.flush()
    return record


def update_series_record(
    db: Session,
    record_id: int,
    mutation: SeriesMutation,
    *,
    allow_conflict: bool = False,
) -> ConfirmedVehicleSeries:
    record = db.get(ConfirmedVehicleSeries, record_id)
    if record is None:
        raise ValueError(f"series record {record_id} not found")

    normalized_query = mutation.query.strip()
    normalized_platform = mutation.platform.strip()
    key = query_key(normalized_query)
    conflict = _active_conflict(db, query_key_value=key, platform=normalized_platform, exclude_id=record.id)
    if conflict is not None:
        _record_conflict(
            db,
            existing=conflict,
            incoming=_mutation_snapshot(mutation, record_id=record.id),
            query=normalized_query,
            query_key_value=key,
            platform=normalized_platform,
        )
        db.flush()
        return record

    old_value = _snapshot(record)
    record.query_key = key
    record.query = normalized_query
    record.platform = normalized_platform
    record.series_id = mutation.series_id.strip()
    record.url = mutation.url
    record.title = mutation.title
    record.source = mutation.source
    record.updated_at = now()
    db.flush()
    _audit(
        db,
        record_id=record.id,
        action="update",
        operator=mutation.operator,
        reason=mutation.reason,
        old_value=old_value,
        new_value=_snapshot(record),
    )
    db.flush()
    return record


def soft_delete_series_record(
    db: Session,
    record_id: int,
    *,
    operator: str,
    reason: str,
) -> ConfirmedVehicleSeries:
    record = db.get(ConfirmedVehicleSeries, record_id)
    if record is None:
        raise ValueError(f"series record {record_id} not found")

    old_value = _snapshot(record)
    deletion_time = now()
    record.status = "deleted"
    record.deleted_at = deletion_time
    record.updated_at = deletion_time
    db.flush()
    _audit(
        db,
        record_id=record.id,
        action="delete",
        operator=operator,
        reason=reason,
        old_value=old_value,
        new_value=_snapshot(record),
    )
    db.flush()
    return record


def restore_series_record(
    db: Session,
    record_id: int,
    *,
    operator: str,
    reason: str,
) -> ConfirmedVehicleSeries:
    record = db.get(ConfirmedVehicleSeries, record_id)
    if record is None:
        raise ValueError(f"series record {record_id} not found")

    conflict = _active_conflict(db, query_key_value=record.query_key, platform=record.platform, exclude_id=record.id)
    if conflict is not None:
        _record_conflict(
            db,
            existing=conflict,
            incoming=_snapshot(record),
            query=record.query,
            query_key_value=record.query_key,
            platform=record.platform,
        )
        db.flush()
        return record

    old_value = _snapshot(record)
    record.status = "active"
    record.deleted_at = None
    record.updated_at = now()
    db.flush()
    _audit(
        db,
        record_id=record.id,
        action="restore",
        operator=operator,
        reason=reason,
        old_value=old_value,
        new_value=_snapshot(record),
    )
    db.flush()
    return record


def list_series_records(
    db: Session,
    *,
    search: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> SeriesListResult:
    query = db.query(ConfirmedVehicleSeries)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                ConfirmedVehicleSeries.query.ilike(pattern),
                ConfirmedVehicleSeries.query_key.ilike(pattern),
                ConfirmedVehicleSeries.series_id.ilike(pattern),
                ConfirmedVehicleSeries.title.ilike(pattern),
            )
        )
    if platform:
        query = query.filter(ConfirmedVehicleSeries.platform == platform)
    if status:
        query = query.filter(ConfirmedVehicleSeries.status == status)
    if source:
        query = query.filter(ConfirmedVehicleSeries.source == source)

    normalized_limit = max(0, limit)
    normalized_offset = max(0, offset)
    total = query.count()
    items = (
        query.order_by(ConfirmedVehicleSeries.updated_at.desc(), ConfirmedVehicleSeries.id.desc())
        .offset(normalized_offset)
        .limit(normalized_limit)
        .all()
    )
    return SeriesListResult(items=items, total=total, limit=normalized_limit, offset=normalized_offset)
