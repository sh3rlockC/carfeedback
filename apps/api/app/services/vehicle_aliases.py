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


def _canonical_query_key(value: str) -> str:
    return query_key(value).replace(" ", "")


def _normalize_alias_values(*, alias: str, canonical_query: str) -> tuple[str, str]:
    normalized_alias = alias.strip()
    normalized_canonical_query = canonical_query.strip()
    if not normalized_alias:
        raise ValueError("alias must not be blank")
    if not normalized_canonical_query:
        raise ValueError("canonical_query must not be blank")
    return normalized_alias, normalized_canonical_query


def create_alias(db: Session, *, alias: str, canonical_query: str) -> SeriesAlias:
    normalized_alias, normalized_canonical_query = _normalize_alias_values(
        alias=alias,
        canonical_query=canonical_query,
    )
    record = SeriesAlias(
        alias_key=alias_key(normalized_alias),
        alias=normalized_alias,
        canonical_query=normalized_canonical_query,
    )
    db.add(record)
    db.flush()
    db.refresh(record)
    return record


def update_alias(db: Session, alias_id: int, *, alias: str, canonical_query: str) -> SeriesAlias:
    normalized_alias, normalized_canonical_query = _normalize_alias_values(
        alias=alias,
        canonical_query=canonical_query,
    )
    record = db.get(SeriesAlias, alias_id)
    if record is None:
        raise ValueError(f"alias {alias_id} not found")

    record.alias_key = alias_key(normalized_alias)
    record.alias = normalized_alias
    record.canonical_query = normalized_canonical_query
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
    canonical_by_key: dict[str, str] = {}
    for row in rows:
        canonical_query = row.canonical_query.strip()
        canonical_key = _canonical_query_key(canonical_query)
        if canonical_query and canonical_key not in canonical_by_key:
            canonical_by_key[canonical_key] = canonical_query
    canonical_queries = list(canonical_by_key.values())
    return AliasResolution(canonical_queries=canonical_queries)


def _candidate_payload(
    record: ConfirmedVehicleSeries,
    *,
    canonical_query: str | None = None,
    canonical_query_key: str | None = None,
) -> dict[str, Any]:
    title = record.title or record.query
    candidate = {
        "series_id": record.series_id,
        "url": record.url,
        "title": title,
        "source": record.source or "confirmed_vehicle_series",
        "evidence_url": record.url,
        "kind": "confirmed",
        "note": "来自服务器已确认车系编号",
    }
    if canonical_query is not None and canonical_query_key is not None:
        candidate["canonical_query"] = canonical_query
        candidate["canonical_query_key"] = canonical_query_key
    return candidate


def confirmed_payload_for_canonical_candidates(
    db: Session | None,
    original_query: str,
    canonical_queries: list[str],
) -> dict[str, Any] | None:
    if db is None or not canonical_queries:
        return None

    canonical_by_key = {_canonical_query_key(query): query.strip() for query in canonical_queries}
    canonical_keys = list(canonical_by_key)
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

    records_by_key_platform: dict[tuple[str, str], ConfirmedVehicleSeries] = {}
    for record in records:
        records_by_key_platform.setdefault((record.query_key, record.platform), record)

    complete_keys = [
        canonical_key
        for canonical_key in canonical_keys
        if all((canonical_key, platform) in records_by_key_platform for platform in PLATFORMS)
    ]
    if len(complete_keys) < 2:
        return None

    payload: dict[str, Any] = {"query": original_query.strip()}
    for platform in PLATFORMS:
        payload[platform] = {
            "best": None,
            "candidates": [
                _candidate_payload(
                    records_by_key_platform[(canonical_key, platform)],
                    canonical_query=canonical_by_key[canonical_key],
                    canonical_query_key=canonical_key,
                )
                for canonical_key in complete_keys
            ],
        }
    return payload
