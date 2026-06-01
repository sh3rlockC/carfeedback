from __future__ import annotations

import sys
import threading
import time
import os
from pathlib import Path

import requests
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from app.config import Settings
from app.main import create_app
from app.models import Base
from app.services.passphrase import hash_passphrase
from app.services.vehicle_resolver import VehicleResolver


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        pass_phrase_hash=hash_passphrase("weekly-secret"),
        pass_phrase_version="2026-W17",
        session_secret="test-secret",
        workspace_root="/Users/xyc/Documents/codexwork",
    )
    return TestClient(create_app(settings))


def test_vehicle_resolve_returns_normalized_candidates(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    client.post("/api/access/verify", json={"passphrase": "weekly-secret"})

    sample_payload = {
        "query": "风云X3 PLUS",
        "autohome": {
            "best": {
                "series_id": "8089",
                "url": "https://k.autohome.com.cn/8089?dimensionid=10&order=0&yearid=0#listcontainer",
                "title": "风云X3 PLUS",
                "source": "fixture:autohome",
            },
            "candidates": [
                {
                    "series_id": "8089",
                    "url": "https://k.autohome.com.cn/8089?dimensionid=10&order=0&yearid=0#listcontainer",
                    "title": "风云X3 PLUS",
                    "source": "fixture:autohome",
                }
            ],
        },
        "dongchedi": {
            "best": {
                "series_id": "25398",
                "url": "https://www.dongchedi.com/auto/series/25398",
                "title": "风云X3 PLUS",
                "source": "fixture:dcd",
            },
            "candidates": [
                {
                    "series_id": "25398",
                    "url": "https://www.dongchedi.com/auto/series/25398",
                    "title": "风云X3 PLUS",
                    "source": "fixture:dcd",
                }
            ],
        },
    }

    monkeypatch.setattr("app.routes.vehicles.VehicleResolver.resolve", lambda self, query: sample_payload)

    response = client.post("/api/vehicles/resolve", json={"query": "风云X3 PLUS"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["autohome"]["best"]["series_id"] == "8089"
    assert payload["dongchedi"]["best"]["series_id"] == "25398"


def test_vehicle_resolve_rejects_malformed_service_output(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    client.post("/api/access/verify", json={"passphrase": "weekly-secret"})

    def raise_error(self, query: str):
        raise ValueError("bad resolver payload")

    monkeypatch.setattr("app.routes.vehicles.VehicleResolver.resolve", raise_error)

    response = client.post("/api/vehicles/resolve", json={"query": "风云X3 PLUS"})

    assert response.status_code == 502
    assert response.json()["detail"] == "bad resolver payload"


class FakeSeriesResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


def test_validate_series_marks_title_match_as_cacheable(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    client.post("/api/access/verify", json={"passphrase": "weekly-secret"})

    def fake_get(url: str, **_kwargs):
        assert url == "https://k.autohome.com.cn/8089/"
        return FakeSeriesResponse("<html><title>风云 X3 PLUS 口碑</title></html>")

    monkeypatch.setattr("app.services.series_validator.requests.get", fake_get)

    response = client.post(
        "/api/vehicles/validate-series",
        json={"query": "风云X3 PLUS", "platform": "autohome", "series_id": "8089"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "matched"
    assert payload["cacheable"] is True
    assert payload["requires_confirmation"] is False
    assert payload["url"] == "https://k.autohome.com.cn/8089/"


def test_validate_series_allows_network_failures_but_does_not_cache(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    client.post("/api/access/verify", json={"passphrase": "weekly-secret"})

    def fake_get(url: str, **_kwargs):
        raise requests.Timeout("blocked")

    monkeypatch.setattr("app.services.series_validator.requests.get", fake_get)

    response = client.post(
        "/api/vehicles/validate-series",
        json={"query": "零跑D19", "platform": "dongchedi", "series_id": "12345"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unverified"
    assert payload["cacheable"] is False
    assert payload["requires_confirmation"] is True
    assert payload["can_create"] is True


def test_job_creation_fails_without_confirmed_platform_candidates(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.post("/api/access/verify", json={"passphrase": "weekly-secret"})

    response = client.post("/api/jobs", json={"query": "风云X3 PLUS"})

    assert response.status_code == 422


def write_vehicle_finder_manifest(tmp_path: Path) -> Path:
    repo = tmp_path / "repos" / "vehicle-id-finder"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "find_vehicle_ids.py").write_text("# fixture", encoding="utf-8")
    manifest = tmp_path / "dependencies.yaml"
    manifest.write_text(
        "\n".join(
            [
                "dependencies:",
                "  - name: vehicle-id-finder",
                "    path: repos/vehicle-id-finder",
                "    runtime: python",
                "    entrypoint: repos/vehicle-id-finder/scripts/find_vehicle_ids.py",
            ]
        ),
        encoding="utf-8",
    )
    return manifest


def make_service_settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'resolver.db'}",
        workspace_root=str(tmp_path),
        vehicle_resolve_cache_ttl_seconds=3600,
    )


def vehicle_payload(site: str, query: str) -> dict:
    ids = {"autohome": "8208", "dongchedi": "25545"}
    urls = {
        "autohome": "https://k.autohome.com.cn/8208?dimensionid=10&order=0&yearid=0#listcontainer",
        "dongchedi": "https://www.dongchedi.com/auto/series/25545",
    }
    return {
        "query": query,
        site: {
            "best": {"id": ids[site], "url": urls[site], "title": query, "source": f"fixture:{site}"},
            "candidates": [{"id": ids[site], "url": urls[site], "title": query, "source": f"fixture:{site}"}],
        },
    }


def test_vehicle_resolver_runs_platform_lookups_in_parallel(tmp_path: Path) -> None:
    manifest = write_vehicle_finder_manifest(tmp_path)
    barrier = threading.Barrier(2)
    calls: list[str] = []

    class ParallelOnlyRunner:
        def run_json(self, cmd: list[str], *, cwd=None, timeout: int = 60) -> dict:
            site = cmd[cmd.index("--site") + 1]
            calls.append(site)
            barrier.wait(timeout=1)
            return vehicle_payload(site, "风云X3L")

    resolver = VehicleResolver(
        manifest_path=manifest,
        tool_runner=ParallelOnlyRunner(),
        settings=make_service_settings(tmp_path),
    )

    started_at = time.perf_counter()
    result = resolver.resolve("风云X3L")

    assert time.perf_counter() - started_at < 1
    assert sorted(calls) == ["autohome", "dongchedi"]
    assert result["autohome"]["best"]["series_id"] == "8208"
    assert result["dongchedi"]["best"]["series_id"] == "25545"


def test_vehicle_resolver_reuses_cached_result(tmp_path: Path) -> None:
    manifest = write_vehicle_finder_manifest(tmp_path)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'cache.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    calls: list[str] = []

    class CountingRunner:
        def run_json(self, cmd: list[str], *, cwd=None, timeout: int = 60) -> dict:
            site = cmd[cmd.index("--site") + 1]
            calls.append(site)
            return vehicle_payload(site, "风云X3L")

    try:
        resolver = VehicleResolver(
            manifest_path=manifest,
            tool_runner=CountingRunner(),
            settings=make_service_settings(tmp_path),
            db=db,
        )

        first = resolver.resolve("  风云X3L  ")
        second = resolver.resolve("风云X3L")
    finally:
        db.close()

    assert first == second
    assert sorted(calls) == ["autohome", "dongchedi"]


def test_vehicle_resolver_prefers_confirmed_series_store(tmp_path: Path) -> None:
    manifest = write_vehicle_finder_manifest(tmp_path)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'confirmed.db'}", future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS confirmed_vehicle_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_key TEXT NOT NULL,
                query TEXT NOT NULL,
                platform TEXT NOT NULL,
                series_id TEXT NOT NULL,
                url TEXT,
                title TEXT,
                source TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO confirmed_vehicle_series (
                query_key, query, platform, series_id, url, title, source, created_at, updated_at
            ) VALUES
                (
                    '风云x3 plus',
                    '风云X3 PLUS',
                    'autohome',
                    '8089',
                    'https://k.autohome.com.cn/8089/',
                    '风云X3 PLUS',
                    'fixture',
                    datetime('now'),
                    datetime('now')
                ),
                (
                    '风云x3 plus',
                    '风云X3 PLUS',
                    'dongchedi',
                    '25398',
                    'https://www.dongchedi.com/auto/series/25398',
                    '风云X3 PLUS',
                    'fixture',
                    datetime('now'),
                    datetime('now')
                )
            """
        )
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    calls: list[str] = []

    class FallbackRunner:
        def run_json(self, cmd: list[str], *, cwd=None, timeout: int = 60) -> dict:
            site = cmd[cmd.index("--site") + 1]
            calls.append(site)
            return vehicle_payload(site, "风云X3 PLUS")

    try:
        resolver = VehicleResolver(
            manifest_path=manifest,
            tool_runner=FallbackRunner(),
            settings=make_service_settings(tmp_path),
            db=db,
        )

        result = resolver.resolve("  风云X3 PLUS  ")
    finally:
        db.close()

    assert calls == []
    assert result["query"] == "风云X3 PLUS"
    assert result["autohome"]["best"]["series_id"] == "8089"
    assert result["autohome"]["best"]["kind"] == "confirmed"
    assert result["dongchedi"]["best"]["series_id"] == "25398"
    assert result["dongchedi"]["best"]["kind"] == "confirmed"
