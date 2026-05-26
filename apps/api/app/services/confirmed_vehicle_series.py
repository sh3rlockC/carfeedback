from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.models import ConfirmedVehicleSeries

PLATFORMS = ("autohome", "dongchedi")


def utc_now() -> datetime:
    return datetime.now(UTC)


def query_key(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip().lower()


def _candidate_value(candidate: Any, field: str) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(field)
    return getattr(candidate, field, None)


def candidate_canonical_key(candidate: Any) -> str | None:
    explicit_key = str(_candidate_value(candidate, "canonical_query_key") or "").strip()
    if explicit_key:
        return query_key(explicit_key)

    canonical_query = str(_candidate_value(candidate, "canonical_query") or "").strip()
    if canonical_query:
        return query_key(canonical_query)

    return None


def canonical_query_for_selected_candidates(
    *,
    query: str,
    selected_candidates: Mapping[str, Any],
) -> str:
    canonical_keys = []
    canonical_queries = []
    for platform in PLATFORMS:
        candidate = selected_candidates.get(platform)
        canonical_key = candidate_canonical_key(candidate)
        if canonical_key:
            canonical_keys.append(canonical_key)

        canonical_query = str(_candidate_value(candidate, "canonical_query") or "").strip()
        if canonical_query:
            canonical_queries.append(canonical_query)

    if len(canonical_keys) == len(PLATFORMS) and len(set(canonical_keys)) == 1 and canonical_queries:
        return canonical_queries[0]

    return query


def upsert_confirmed_vehicle_series(
    db: Session,
    *,
    query: str,
    selected_candidates: Mapping[str, Any],
) -> None:
    normalized_query = query.strip()
    key = query_key(normalized_query)
    now = utc_now()

    for platform in PLATFORMS:
        candidate = selected_candidates.get(platform)
        series_id = str(_candidate_value(candidate, "series_id") or "").strip()
        if not series_id:
            continue

        record = (
            db.query(ConfirmedVehicleSeries)
            .filter(
                ConfirmedVehicleSeries.query_key == key,
                ConfirmedVehicleSeries.platform == platform,
                ConfirmedVehicleSeries.status == "active",
            )
            .one_or_none()
        )
        if record is None:
            db.add(
                ConfirmedVehicleSeries(
                    query_key=key,
                    query=normalized_query,
                    platform=platform,
                    series_id=series_id,
                    url=_candidate_value(candidate, "url"),
                    title=_candidate_value(candidate, "title"),
                    source=_candidate_value(candidate, "source"),
                    created_at=now,
                    updated_at=now,
                )
            )
            continue

        record.query = normalized_query
        record.series_id = series_id
        record.url = _candidate_value(candidate, "url")
        record.title = _candidate_value(candidate, "title")
        record.source = _candidate_value(candidate, "source")
        record.updated_at = now


def confirmed_vehicle_series_payload(db: Session | None, query: str) -> dict[str, Any] | None:
    if db is None:
        return None

    key = query_key(query)
    records = (
        db.query(ConfirmedVehicleSeries)
        .filter(
            ConfirmedVehicleSeries.query_key == key,
            ConfirmedVehicleSeries.status == "active",
        )
        .all()
    )
    records_by_platform = {record.platform: record for record in records if record.series_id}
    if any(platform not in records_by_platform for platform in PLATFORMS):
        return None

    canonical_query = records_by_platform["autohome"].query or query.strip()
    payload: dict[str, Any] = {"query": canonical_query}
    for platform in PLATFORMS:
        record = records_by_platform[platform]
        candidate = {
            "series_id": record.series_id,
            "url": record.url,
            "title": record.title or canonical_query,
            "source": record.source or "confirmed_vehicle_series",
            "evidence_url": record.url,
            "kind": "confirmed",
            "note": "来自服务器已确认车系编号",
        }
        payload[platform] = {"best": candidate, "candidates": [candidate]}
    return payload
