from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.models import VehicleResolveCache
from app.services.confirmed_vehicle_series import PLATFORMS, confirmed_vehicle_series_payload, query_key
from app.services.dependencies import discover_manifest_path, load_dependency_map
from app.services.tool_runner import ToolRunner
from app.services.vehicle_aliases import confirmed_payload_for_canonical_candidates, resolve_alias

try:
    from sqlalchemy.orm import Session
except ImportError:  # pragma: no cover
    Session = Any  # type: ignore[misc,assignment]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class VehicleResolver:
    def __init__(
        self,
        manifest_path: Path | None = None,
        tool_runner: ToolRunner | None = None,
        settings: Settings | None = None,
        db: Session | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.manifest_path = manifest_path or discover_manifest_path(
            source_path=Path(__file__),
            workspace_root=self.settings.workspace_root_path,
        )
        self.tool_runner = tool_runner or ToolRunner()
        self.db = db

    def _load_dependency(self, name: str) -> dict[str, Any]:
        dependency_map = load_dependency_map(self.manifest_path, self.settings.workspace_root_path)
        if name not in dependency_map:
            raise KeyError(f"dependency not found: {name}")
        return dependency_map[name]

    def _normalize_candidate(self, candidate: dict[str, Any] | None, query: str) -> dict[str, Any] | None:
        if not candidate:
            return None
        title = candidate.get("title")
        if not title:
            evidence_text = str(candidate.get("evidence_text") or "").strip()
            title = evidence_text.splitlines()[0].strip() if evidence_text else query
        return {
            "series_id": str(candidate.get("id")) if candidate.get("id") is not None else None,
            "url": candidate.get("url"),
            "title": title,
            "source": candidate.get("source"),
            "evidence_url": candidate.get("evidence_url"),
            "kind": candidate.get("kind"),
            "note": candidate.get("note"),
        }

    def _normalize_platform(self, platform_payload: dict[str, Any] | None, query: str) -> dict[str, Any]:
        platform_payload = platform_payload or {}
        candidates = [
            normalized
            for candidate in platform_payload.get("candidates", [])
            if (normalized := self._normalize_candidate(candidate, query)) is not None
        ]
        best = self._normalize_candidate(platform_payload.get("best"), query)
        return {
            "best": best,
            "candidates": candidates,
        }

    def _cached_payload(self, key: str) -> dict[str, Any] | None:
        if self.db is None or self.settings.vehicle_resolve_cache_ttl_seconds <= 0:
            return None

        cached = self.db.get(VehicleResolveCache, key)
        if cached is None:
            return None

        if _as_aware_utc(cached.expires_at) <= _utc_now():
            self.db.delete(cached)
            self.db.commit()
            return None

        return dict(cached.response_json)

    def _write_cache(self, *, key: str, query: str, payload: dict[str, Any]) -> None:
        if self.db is None or self.settings.vehicle_resolve_cache_ttl_seconds <= 0:
            return

        now = _utc_now()
        expires_at = now + timedelta(seconds=self.settings.vehicle_resolve_cache_ttl_seconds)
        cached = self.db.get(VehicleResolveCache, key)
        if cached is None:
            self.db.add(
                VehicleResolveCache(
                    query_key=key,
                    query=query,
                    response_json=payload,
                    created_at=now,
                    updated_at=now,
                    expires_at=expires_at,
                )
            )
        else:
            cached.query = query
            cached.response_json = payload
            cached.updated_at = now
            cached.expires_at = expires_at
        self.db.commit()

    def _resolve_platform(self, dependency: dict[str, Any], query: str, site: str) -> dict[str, Any] | None:
        try:
            payload = self.tool_runner.run_json(
                [sys.executable, dependency["entrypoint"], query, "--json", "--site", site],
                cwd=dependency["path"],
                timeout=self.settings.vehicle_resolve_platform_timeout_seconds,
            )
        except Exception as exc:
            raise RuntimeError(f"{site} resolver failed: {exc}") from exc
        return payload.get(site)

    def _resolve_uncached(self, query: str) -> dict[str, Any]:
        dependency = self._load_dependency("vehicle-id-finder")
        payload: dict[str, Any] = {"query": query}
        with ThreadPoolExecutor(max_workers=len(PLATFORMS)) as executor:
            futures = {
                executor.submit(self._resolve_platform, dependency, query, site): site
                for site in PLATFORMS
            }
            for future in as_completed(futures):
                site = futures[future]
                payload[site] = future.result()
        return payload

    def resolve(self, query: str) -> dict[str, Any]:
        normalized_query = query.strip()
        alias_resolution = resolve_alias(self.db, normalized_query)
        if len(alias_resolution.canonical_queries) == 1:
            confirmed = confirmed_vehicle_series_payload(self.db, alias_resolution.canonical_queries[0])
            if confirmed is not None:
                return confirmed
        elif len(alias_resolution.canonical_queries) > 1:
            alias_candidates = confirmed_payload_for_canonical_candidates(
                self.db,
                normalized_query,
                alias_resolution.canonical_queries,
            )
            if alias_candidates is not None:
                return alias_candidates

        confirmed = confirmed_vehicle_series_payload(self.db, normalized_query)
        if confirmed is not None:
            return confirmed

        key = query_key(normalized_query)
        cached = self._cached_payload(key)
        if cached is not None:
            return cached

        payload = self._resolve_uncached(normalized_query)
        result = {
            "query": payload.get("query", normalized_query),
            "autohome": self._normalize_platform(payload.get("autohome"), normalized_query),
            "dongchedi": self._normalize_platform(payload.get("dongchedi"), normalized_query),
        }
        self._write_cache(key=key, query=normalized_query, payload=result)
        return result
