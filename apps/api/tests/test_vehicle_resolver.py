from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.main import create_app
from app.models import Base, ConfirmedVehicleSeries, SeriesAlias
from app.services.passphrase import hash_passphrase
from app.services.vehicle_aliases import create_alias, delete_alias, update_alias
from app.services.vehicle_resolver import VehicleResolver


def make_client(tmp_path: Path, *, access_control_enabled: bool = True) -> TestClient:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
        pass_phrase_hash=hash_passphrase("weekly-secret"),
        pass_phrase_version="2026-W17",
        access_control_enabled=access_control_enabled,
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


def test_vehicle_resolve_allows_direct_access_when_access_control_disabled(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path, access_control_enabled=False)

    sample_payload = {
        "query": "风云X3 PLUS",
        "autohome": {
            "best": {
                "series_id": "8089",
                "url": "https://k.autohome.com.cn/8089",
                "title": "风云X3 PLUS",
                "source": "fixture:autohome",
            },
            "candidates": [],
        },
        "dongchedi": {
            "best": {
                "series_id": "25398",
                "url": "https://www.dongchedi.com/auto/series/25398",
                "title": "风云X3 PLUS",
                "source": "fixture:dcd",
            },
            "candidates": [],
        },
    }

    monkeypatch.setattr("app.routes.vehicles.VehicleResolver.resolve", lambda self, query: sample_payload)

    response = client.post("/api/vehicles/resolve", json={"query": "风云X3 PLUS"})

    assert response.status_code == 200
    assert response.json()["query"] == "风云X3 PLUS"


def test_vehicle_resolve_rejects_malformed_service_output(tmp_path: Path, monkeypatch) -> None:
    client = make_client(tmp_path)
    client.post("/api/access/verify", json={"passphrase": "weekly-secret"})

    def raise_error(self, query: str):
        raise ValueError("bad resolver payload")

    monkeypatch.setattr("app.routes.vehicles.VehicleResolver.resolve", raise_error)

    response = client.post("/api/vehicles/resolve", json={"query": "风云X3 PLUS"})

    assert response.status_code == 502
    assert response.json()["detail"] == "bad resolver payload"


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
                query_key, query, platform, series_id, status, url, title, source, created_at, updated_at
            ) VALUES
                (
                    '风云x3 plus',
                    '风云X3 PLUS',
                    'autohome',
                    '8089',
                    'active',
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
                    'active',
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


def test_vehicle_resolver_uses_active_alias_for_confirmed_series(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        db.add(SeriesAlias(alias_key="奇瑞风云t11", alias="奇瑞风云T11", canonical_query="风云T11"))
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="autohome",
                series_id="7411",
                status="active",
            )
        )
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="dongchedi",
                series_id="9436",
                status="active",
            )
        )
        db.commit()

        resolver = VehicleResolver(settings=make_service_settings(tmp_path), db=db)
        result = resolver.resolve("奇瑞风云T11")

        assert result["query"] == "风云T11"
        assert result["autohome"]["best"]["series_id"] == "7411"
        assert result["dongchedi"]["best"]["series_id"] == "9436"
    finally:
        db.close()


def test_vehicle_resolver_returns_candidates_for_duplicate_alias(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias-dupe.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        db.add(SeriesAlias(alias_key="风云", alias="风云", canonical_query="风云T11"))
        db.add(SeriesAlias(alias_key="风云", alias="风云", canonical_query="风云 X3L"))
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="autohome",
                series_id="7411",
                status="active",
            )
        )
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="dongchedi",
                series_id="9436",
                status="active",
            )
        )
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云 x3l",
                query="风云 X3L",
                platform="autohome",
                series_id="8208",
                status="active",
            )
        )
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云 x3l",
                query="风云 X3L",
                platform="dongchedi",
                series_id="25545",
                status="active",
            )
        )
        db.commit()

        resolver = VehicleResolver(settings=make_service_settings(tmp_path), db=db)
        result = resolver.resolve("风云")

        assert result["query"] == "风云"
        assert {candidate["series_id"] for candidate in result["autohome"]["candidates"]} == {"7411", "8208"}
        assert result["autohome"]["best"] is None
        autohome_by_key = {candidate["canonical_query_key"]: candidate for candidate in result["autohome"]["candidates"]}
        dongchedi_by_key = {candidate["canonical_query_key"]: candidate for candidate in result["dongchedi"]["candidates"]}
        assert set(autohome_by_key) == {"风云t11", "风云 x3l"}
        assert set(dongchedi_by_key) == {"风云t11", "风云 x3l"}
        assert autohome_by_key["风云t11"]["canonical_query"] == "风云T11"
        assert dongchedi_by_key["风云 x3l"]["canonical_query"] == "风云 X3L"
    finally:
        db.close()


def test_vehicle_resolver_dedupes_alias_canonical_queries_by_query_key(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias-dedupe.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        db.add(SeriesAlias(alias_key="风云", alias="风云", canonical_query="风云 T11"))
        db.add(SeriesAlias(alias_key="风云", alias="风云", canonical_query="风云  T11"))
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云 t11",
                query="风云 T11",
                platform="autohome",
                series_id="7411",
                status="active",
            )
        )
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云 t11",
                query="风云 T11",
                platform="dongchedi",
                series_id="9436",
                status="active",
            )
        )
        db.commit()

        resolver = VehicleResolver(settings=make_service_settings(tmp_path), db=db)
        result = resolver.resolve("风云")

        assert result["query"] == "风云 T11"
        assert result["autohome"]["best"]["series_id"] == "7411"
        assert result["dongchedi"]["best"]["series_id"] == "9436"
    finally:
        db.close()


def test_vehicle_resolver_falls_back_for_incomplete_duplicate_alias_options(tmp_path: Path) -> None:
    manifest = write_vehicle_finder_manifest(tmp_path)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias-partial.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    calls: list[str] = []

    class FallbackRunner:
        def run_json(self, cmd: list[str], *, cwd=None, timeout: int = 60) -> dict:
            site = cmd[cmd.index("--site") + 1]
            calls.append(site)
            return vehicle_payload(site, "风云")

    try:
        db.add(SeriesAlias(alias_key="风云", alias="风云", canonical_query="风云T11"))
        db.add(SeriesAlias(alias_key="风云", alias="风云", canonical_query="风云X3L"))
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="autohome",
                series_id="7411",
                status="active",
            )
        )
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="dongchedi",
                series_id="9436",
                status="active",
            )
        )
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云x3l",
                query="风云X3L",
                platform="autohome",
                series_id="8208",
                status="active",
            )
        )
        db.commit()

        resolver = VehicleResolver(
            manifest_path=manifest,
            tool_runner=FallbackRunner(),
            settings=make_service_settings(tmp_path),
            db=db,
        )
        result = resolver.resolve("风云")

        assert sorted(calls) == ["autohome", "dongchedi"]
        assert result["query"] == "风云"
        assert result["autohome"]["best"]["series_id"] == "8208"
        assert result["dongchedi"]["best"]["series_id"] == "25545"
    finally:
        db.close()


def test_vehicle_resolver_falls_back_when_alias_has_no_active_confirmed_series(tmp_path: Path) -> None:
    manifest = write_vehicle_finder_manifest(tmp_path)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias-inactive.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    calls: list[str] = []

    class FallbackRunner:
        def run_json(self, cmd: list[str], *, cwd=None, timeout: int = 60) -> dict:
            site = cmd[cmd.index("--site") + 1]
            calls.append(site)
            return vehicle_payload(site, "奇瑞风云T11")

    try:
        db.add(SeriesAlias(alias_key="奇瑞风云t11", alias="奇瑞风云T11", canonical_query="风云T11"))
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="autohome",
                series_id="7411",
                status="deleted",
            )
        )
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="dongchedi",
                series_id="9436",
                status="deleted",
            )
        )
        db.commit()

        resolver = VehicleResolver(
            manifest_path=manifest,
            tool_runner=FallbackRunner(),
            settings=make_service_settings(tmp_path),
            db=db,
        )
        result = resolver.resolve("奇瑞风云T11")

        assert sorted(calls) == ["autohome", "dongchedi"]
        assert result["query"] == "奇瑞风云T11"
        assert result["autohome"]["best"]["series_id"] == "8208"
        assert result["dongchedi"]["best"]["series_id"] == "25545"
    finally:
        db.close()


def test_alias_crud_helpers_leave_transactions_to_caller(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias-crud.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    db = SessionLocal()
    try:
        alias = create_alias(db, alias="奇瑞风云T11", canonical_query="风云T11")
        alias_id = alias.id
        assert alias_id is not None
        assert db.get(SeriesAlias, alias_id) is not None
        db.rollback()
    finally:
        db.close()

    db = SessionLocal()
    try:
        assert db.get(SeriesAlias, alias_id) is None

        alias = create_alias(db, alias="奇瑞风云T11", canonical_query="风云T11")
        db.commit()
        alias_id = alias.id

        updated = update_alias(db, alias_id, alias="风云", canonical_query="风云X3L")
        assert updated.alias_key == "风云"
        db.rollback()
        assert db.get(SeriesAlias, alias_id).canonical_query == "风云T11"

        delete_alias(db, alias_id)
        assert db.get(SeriesAlias, alias_id) is None
        db.rollback()
        assert db.get(SeriesAlias, alias_id) is not None
    finally:
        db.close()


def test_alias_crud_helpers_validate_blank_values(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias-validation.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        for alias, canonical_query in [("", "风云T11"), ("风云", ""), ("  ", "风云T11"), ("风云", "  ")]:
            try:
                create_alias(db, alias=alias, canonical_query=canonical_query)
            except ValueError:
                pass
            else:
                raise AssertionError("blank alias values should raise ValueError")

        record = create_alias(db, alias="风云", canonical_query="风云T11")
        for alias, canonical_query in [("", "风云T11"), ("风云", "")]:
            try:
                update_alias(db, record.id, alias=alias, canonical_query=canonical_query)
            except ValueError:
                pass
            else:
                raise AssertionError("blank alias update values should raise ValueError")
    finally:
        db.close()


def test_alias_crud_helpers_raise_value_error_when_not_found(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias-not-found.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        for action in [
            lambda: update_alias(db, 123, alias="风云", canonical_query="风云T11"),
            lambda: delete_alias(db, 123),
        ]:
            try:
                action()
            except ValueError:
                pass
            else:
                raise AssertionError("missing alias should raise ValueError")
    finally:
        db.close()
