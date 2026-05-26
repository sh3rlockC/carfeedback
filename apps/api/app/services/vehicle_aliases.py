from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import ConfirmedVehicleSeries, SeriesAlias
from app.services.confirmed_vehicle_series import PLATFORMS, query_key


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AliasResolution:
    canonical_queries: list[str]


def alias_key(value: str) -> str:
    return query_key(value)


def create_alias(db: Session, *, alias: str, canonical_query: str) -> SeriesAlias:
    record = SeriesAlias(
        alias_key=alias_key(alias),
        alias=alias.strip(),
        canonical_query=canonical_query.strip(),
    )
    db.add(record)
    db.flush()
    db.refresh(record)
    return record


def update_alias(db: Session, alias_id: int, *, alias: str, canonical_query: str) -> SeriesAlias:
    record = db.get(SeriesAlias, alias_id)
    if record is None:
        raise ValueError(f"alias {alias_id} not found")

    record.alias_key = alias_key(alias)
    record.alias = alias.strip()
    record.canonical_query = canonical_query.strip()
    record.updated_at = now()
    db.flush()
    db.refresh(record)
    return record


def delete_alias(db: Session, alias_id: int) -> None:
    record = db.get(SeriesAlias, alias_id)
    if record is None:
        raise ValueError(f"alias {alias_id} not found")

    db.delete(record)
    db.flush()


def resolve_alias(db: Session | None, query: str) -> AliasResolution:
    if db is None:
        return AliasResolution(canonical_queries=[])

    rows = (
        db.query(SeriesAlias)
        .filter(SeriesAlias.alias_key == alias_key(query))
        .order_by(SeriesAlias.id.asc())
        .all()
    )
    canonical_queries = list(
        dict.fromkeys(row.canonical_query.strip() for row in rows if row.canonical_query.strip())
    )
    return AliasResolution(canonical_queries=canonical_queries)


def _candidate_payload(record: ConfirmedVehicleSeries) -> dict[str, Any]:
    title = record.title or record.query
    return {
        "series_id": record.series_id,
        "url": record.url,
        "title": title,
        "source": record.source or "confirmed_vehicle_series",
        "evidence_url": record.url,
        "kind": "confirmed",
        "note": "来自服务器已确认车系编号",
    }


def confirmed_payload_for_canonical_candidates(
    db: Session | None,
    original_query: str,
    canonical_queries: list[str],
) -> dict[str, Any] | None:
    if db is None or not canonical_queries:
        return None

    canonical_keys = [query_key(query) for query in canonical_queries]
    records = (
        db.query(ConfirmedVehicleSeries)
        .filter(
            ConfirmedVehicleSeries.query_key.in_(canonical_keys),
            ConfirmedVehicleSeries.platform.in_(PLATFORMS),
            ConfirmedVehicleSeries.status == "active",
        )
        .order_by(
            ConfirmedVehicleSeries.query_key.asc(),
            ConfirmedVehicleSeries.platform.asc(),
            ConfirmedVehicleSeries.id.asc(),
        )
        .all()
    )
    records = [record for record in records if record.series_id]
    if not records:
        return None

    payload: dict[str, Any] = {"query": original_query.strip()}
    for platform in PLATFORMS:
        payload[platform] = {
            "best": None,
            "candidates": [_candidate_payload(record) for record in records if record.platform == platform],
        }
    return payload
