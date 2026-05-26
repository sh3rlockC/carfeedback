from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import _active_status_predicate_is_valid, _postgres_active_index_is_valid, init_db, reset_engine_cache
from app.main import create_app
from app.models import ConfirmedVehicleSeries, SeriesAlias, SeriesAuditLog, SeriesConflict, SeriesImportBatch
from app.services.passphrase import hash_passphrase
from app.services.confirmed_vehicle_series import confirmed_vehicle_series_payload, upsert_confirmed_vehicle_series
from app.services.series_admin import (
    SeriesMutation,
    create_series_record,
    list_series_records,
    restore_series_record,
    soft_delete_series_record,
    update_series_record,
)


def make_admin_client(
    tmp_path: Path,
    *,
    access_control_enabled: bool = False,
    raise_server_exceptions: bool = True,
) -> TestClient:
    reset_engine_cache()
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'series-routes.db'}",
        access_control_enabled=access_control_enabled,
        pass_phrase_hash=hash_passphrase("weekly-secret"),
    )
    return TestClient(create_app(settings), raise_server_exceptions=raise_server_exceptions)


def test_active_series_index_predicate_validation_is_exact() -> None:
    assert _active_status_predicate_is_valid("status = 'active'")
    assert _active_status_predicate_is_valid("status = 'active'::text")
    assert _active_status_predicate_is_valid("((status)::text = 'active'::text)")
    assert _postgres_active_index_is_valid(
        {
            "indisunique": True,
            "indisvalid": True,
            "indisready": True,
            "indnkeyatts": 2,
            "has_no_expressions": True,
            "columns": ["query_key", "platform"],
            "predicate": "(status)::text = 'active'::text",
        }
    )

    assert not _active_status_predicate_is_valid("status <> 'inactive'")
    assert not _active_status_predicate_is_valid("status in ('active', 'pending')")
    assert not _postgres_active_index_is_valid(
        {
            "indisunique": True,
            "indisvalid": True,
            "indisready": True,
            "indnkeyatts": 2,
            "has_no_expressions": True,
            "columns": ["platform", "query_key"],
            "predicate": "status = 'active'",
        }
    )
    assert not _postgres_active_index_is_valid(
        {
            "indisunique": True,
            "indisvalid": True,
            "indisready": True,
            "indnkeyatts": 2,
            "has_no_expressions": False,
            "columns": [None, "platform"],
            "predicate": "status = 'active'",
        }
    )
    assert not _postgres_active_index_is_valid(
        {
            "indisunique": True,
            "indisvalid": False,
            "indisready": True,
            "indnkeyatts": 2,
            "has_no_expressions": True,
            "columns": ["query_key", "platform"],
            "predicate": "status = 'active'",
        }
    )
    assert not _postgres_active_index_is_valid(
        {
            "indisunique": True,
            "indisvalid": True,
            "indisready": False,
            "indnkeyatts": 2,
            "has_no_expressions": True,
            "columns": ["query_key", "platform"],
            "predicate": "status = 'active'",
        }
    )


def test_series_admin_tables_and_columns_exist(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")

    init_db(settings)

    engine = create_engine(settings.database_url, future=True)
    inspector = inspect(engine)
    assert "confirmed_vehicle_series" in inspector.get_table_names()
    assert "series_aliases" in inspector.get_table_names()
    assert "series_audit_logs" in inspector.get_table_names()
    assert "series_conflicts" in inspector.get_table_names()
    assert "series_import_batches" in inspector.get_table_names()

    confirmed_columns = {column["name"] for column in inspector.get_columns("confirmed_vehicle_series")}
    assert {"status", "deleted_at", "import_batch_id"}.issubset(confirmed_columns)
    confirmed_indexes = {index["name"] for index in inspector.get_indexes("confirmed_vehicle_series")}
    assert "uq_confirmed_vehicle_series_active_query_platform" in confirmed_indexes


def test_series_admin_models_persist_status_alias_audit_and_conflict(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        batch = SeriesImportBatch(source="legacy_sync", filename="legacy.xlsx", operator="tester", summary_json={"rows": 1})
        db.add(batch)
        db.flush()
        record = ConfirmedVehicleSeries(
            query_key="fengyun t11",
            query="风云T11",
            platform="autohome",
            series_id="7411",
            status="active",
            source="legacy",
            import_batch_id=batch.id,
        )
        db.add(record)
        alias = SeriesAlias(alias_key="奇瑞风云t11", alias="奇瑞风云T11", canonical_query="风云T11")
        duplicate_alias = SeriesAlias(alias_key="奇瑞风云t11", alias="风云T11", canonical_query="风云T11 Pro")
        db.add_all([alias, duplicate_alias])
        audit = SeriesAuditLog(
            record_id=1,
            action="create",
            operator="tester",
            reason="initial import",
            old_value_json={},
            new_value_json={"series_id": "7411"},
        )
        db.add(audit)
        conflict = SeriesConflict(
            conflict_type="series_id",
            query_key="fengyun t11",
            query="风云T11",
            platform=None,
            existing_value_json={"series_id": "7411"},
            incoming_value_json={"series_id": "9999"},
            status="open",
            import_batch_id=batch.id,
            resolved_by="reviewer",
            resolution_json={"action": "keep_existing"},
        )
        db.add(conflict)
        db.commit()

        assert db.query(SeriesImportBatch).one().source == "legacy_sync"
        assert db.query(ConfirmedVehicleSeries).one().status == "active"
        assert db.query(SeriesAlias).filter_by(alias_key="奇瑞风云t11").count() == 2
        audit_row = db.query(SeriesAuditLog).one()
        assert audit_row.operator == "tester"
        assert audit_row.reason == "initial import"
        conflict_row = db.query(SeriesConflict).one()
        assert conflict_row.platform is None
        assert conflict_row.status == "open"
        assert conflict_row.resolved_by == "reviewer"
        assert conflict_row.resolution_json == {"action": "keep_existing"}


def test_legacy_confirmed_vehicle_series_schema_sync_without_jobs(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'legacy-series.db'}"
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE confirmed_vehicle_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_key VARCHAR(255) NOT NULL,
                    query VARCHAR(255) NOT NULL,
                    platform VARCHAR(32) NOT NULL,
                    series_id VARCHAR(64) NOT NULL,
                    url TEXT,
                    title VARCHAR(255),
                    source TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO confirmed_vehicle_series (
                    query_key,
                    query,
                    platform,
                    series_id,
                    url,
                    title,
                    source,
                    created_at,
                    updated_at
                )
                VALUES (
                    'fengyun t11',
                    '风云T11',
                    'autohome',
                    '7411',
                    NULL,
                    NULL,
                    'legacy',
                    '2026-05-26 00:00:00',
                    '2026-05-26 00:00:00'
                )
                """
            )
        )

    reset_engine_cache()
    init_db(Settings(app_env="test", database_url=database_url))

    inspector = inspect(engine)
    confirmed_columns = {column["name"] for column in inspector.get_columns("confirmed_vehicle_series")}
    assert {"status", "deleted_at", "import_batch_id"}.issubset(confirmed_columns)

    with engine.connect() as conn:
        row = conn.execute(text("SELECT status, import_batch_id FROM confirmed_vehicle_series")).one()
        assert row.status == "active"
        assert row.import_batch_id is None


def test_legacy_confirmed_vehicle_series_unique_constraint_migrates_to_active_index(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'legacy-unique-series.db'}"
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE confirmed_vehicle_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_key VARCHAR(255) NOT NULL,
                    query VARCHAR(255) NOT NULL,
                    platform VARCHAR(32) NOT NULL,
                    series_id VARCHAR(64) NOT NULL,
                    url TEXT,
                    title VARCHAR(255),
                    source TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_confirmed_vehicle_series_query_platform UNIQUE (query_key, platform)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO confirmed_vehicle_series (
                    query_key,
                    query,
                    platform,
                    series_id,
                    url,
                    title,
                    source,
                    created_at,
                    updated_at
                )
                VALUES (
                    '风云t11',
                    '风云T11',
                    'autohome',
                    '7411',
                    NULL,
                    NULL,
                    'legacy',
                    '2026-05-26 00:00:00',
                    '2026-05-26 00:00:00'
                )
                """
            )
        )

    reset_engine_cache()
    init_db(Settings(app_env="test", database_url=database_url))

    inspector = inspect(engine)
    confirmed_indexes = {index["name"] for index in inspector.get_indexes("confirmed_vehicle_series")}
    assert "uq_confirmed_vehicle_series_active_query_platform" in confirmed_indexes

    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        legacy = db.query(ConfirmedVehicleSeries).filter_by(query_key="风云t11", platform="autohome").one()
        soft_delete_series_record(db, legacy.id, operator="tester", reason="replace legacy")

        replacement = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7412", operator="tester", reason="replacement"),
        )

        assert replacement.id != legacy.id
        rows = db.query(ConfirmedVehicleSeries).filter_by(query_key="风云t11", platform="autohome").all()
        assert sorted(row.status for row in rows) == ["active", "deleted"]
        assert [row.series_id for row in rows if row.status == "active"] == ["7412"]


def test_legacy_confirmed_vehicle_series_unique_migration_is_idempotent(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'legacy-idempotent-series.db'}"
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE confirmed_vehicle_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_key VARCHAR(255) NOT NULL,
                    query VARCHAR(255) NOT NULL,
                    platform VARCHAR(32) NOT NULL,
                    series_id VARCHAR(64) NOT NULL,
                    url TEXT,
                    title VARCHAR(255),
                    source TEXT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_confirmed_vehicle_series_query_platform UNIQUE (query_key, platform)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO confirmed_vehicle_series (
                    query_key,
                    query,
                    platform,
                    series_id,
                    created_at,
                    updated_at
                )
                VALUES ('风云t11', '风云T11', 'autohome', '7411', '2026-05-26 00:00:00', '2026-05-26 00:00:00')
                """
            )
        )

    reset_engine_cache()
    settings = Settings(app_env="test", database_url=database_url)
    init_db(settings)
    init_db(settings)

    inspector = inspect(engine)
    confirmed_indexes = {index["name"] for index in inspector.get_indexes("confirmed_vehicle_series")}
    assert "uq_confirmed_vehicle_series_active_query_platform" in confirmed_indexes

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT query_key, platform, series_id, status FROM confirmed_vehicle_series")).all()
    assert rows == [("风云t11", "autohome", "7411", "active")]


def test_confirmed_vehicle_series_sync_adds_active_index_without_rebuild_when_no_legacy_unique(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'missing-active-index-series.db'}"
    engine = create_engine(database_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE confirmed_vehicle_series (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query_key VARCHAR(255) NOT NULL,
                    query VARCHAR(255) NOT NULL,
                    platform VARCHAR(32) NOT NULL,
                    series_id VARCHAR(64) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    url TEXT,
                    title VARCHAR(255),
                    source TEXT,
                    import_batch_id INTEGER,
                    deleted_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX ix_confirmed_vehicle_series_source ON confirmed_vehicle_series (source)"))
        conn.execute(
            text(
                """
                INSERT INTO confirmed_vehicle_series (
                    query_key,
                    query,
                    platform,
                    series_id,
                    status,
                    source,
                    created_at,
                    updated_at
                )
                VALUES ('风云t11', '风云T11', 'autohome', '7411', 'deleted', 'legacy', '2026-05-26 00:00:00', '2026-05-26 00:00:00')
                """
            )
        )

    reset_engine_cache()
    init_db(Settings(app_env="test", database_url=database_url))

    inspector = inspect(engine)
    confirmed_indexes = {index["name"] for index in inspector.get_indexes("confirmed_vehicle_series")}
    assert "uq_confirmed_vehicle_series_active_query_platform" in confirmed_indexes
    assert "ix_confirmed_vehicle_series_source" in confirmed_indexes

    with engine.connect() as conn:
        row = conn.execute(text("SELECT query_key, platform, series_id, status, source FROM confirmed_vehicle_series")).one()
    assert row == ("风云t11", "autohome", "7411", "deleted", "legacy")


def test_series_admin_create_update_delete_restore_with_audit(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        created = create_series_record(
            db,
            SeriesMutation(
                query="风云T11",
                platform="autohome",
                series_id="7411",
                url="https://k.autohome.com.cn/7411",
                title="风云T11",
                source="manual",
                operator="tester",
                reason="confirmed from old system",
            ),
        )
        assert created.status == "active"

        updated = update_series_record(
            db,
            created.id,
            SeriesMutation(
                query="风云T11",
                platform="autohome",
                series_id="7412",
                url="https://k.autohome.com.cn/7412",
                title="风云T11",
                source="manual",
                operator="tester",
                reason="corrected series id",
            ),
        )
        assert updated.series_id == "7412"

        soft_delete_series_record(db, created.id, operator="tester", reason="temporary removal")
        assert list_series_records(db, status="deleted").items[0].series_id == "7412"

        restore_series_record(db, created.id, operator="tester", reason="restore after review")
        assert list_series_records(db, status="active").items[0].series_id == "7412"

        actions = [row.action for row in db.query(SeriesAuditLog).order_by(SeriesAuditLog.id).all()]
        assert actions == ["create", "update", "delete", "restore"]


def test_series_admin_blocks_unresolved_active_conflict(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        first = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7411", operator="tester", reason="first"),
        )
        second = create_series_record(
            db,
            SeriesMutation(query="风云X3L", platform="autohome", series_id="8208", operator="tester", reason="second"),
        )

        result = update_series_record(
            db,
            second.id,
            SeriesMutation(query="风云T11", platform="autohome", series_id="8208", operator="tester", reason="rename"),
            allow_conflict=False,
        )

        assert result.id == second.id
        conflicts = db.query(SeriesConflict).all()
        assert len(conflicts) == 1
        assert conflicts[0].existing_value_json["id"] == first.id
        assert conflicts[0].incoming_value_json["id"] == second.id


def test_confirmed_vehicle_series_payload_ignores_deleted_rows(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        db.add_all(
            [
                ConfirmedVehicleSeries(
                    query_key="风云t11",
                    query="风云T11",
                    platform="autohome",
                    series_id="7411",
                    status="active",
                ),
                ConfirmedVehicleSeries(
                    query_key="风云t11",
                    query="风云T11",
                    platform="dongchedi",
                    series_id="5498",
                    status="deleted",
                ),
            ]
        )
        db.commit()

        assert confirmed_vehicle_series_payload(db, "风云T11") is None

        deleted = db.query(ConfirmedVehicleSeries).filter_by(platform="dongchedi").one()
        deleted.status = "active"
        db.commit()

        assert confirmed_vehicle_series_payload(db, "风云T11") is not None


def test_series_admin_create_replacement_after_soft_delete(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        deleted = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7411", operator="tester", reason="first"),
        )
        soft_delete_series_record(db, deleted.id, operator="tester", reason="replace")

        replacement = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7412", operator="tester", reason="replacement"),
        )

        assert replacement.id != deleted.id
        rows = db.query(ConfirmedVehicleSeries).filter_by(query_key="风云t11", platform="autohome").all()
        assert sorted(row.status for row in rows) == ["active", "deleted"]
        assert [row.series_id for row in rows if row.status == "active"] == ["7412"]


def test_series_admin_update_conflict_is_safe_by_default(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        first = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7411", operator="tester", reason="first"),
        )
        second = create_series_record(
            db,
            SeriesMutation(query="风云X3L", platform="autohome", series_id="8208", operator="tester", reason="second"),
        )

        result = update_series_record(
            db,
            second.id,
            SeriesMutation(query="风云T11", platform="autohome", series_id="8209", operator="tester", reason="rename"),
        )

        assert result.id == second.id
        assert result.query == "风云X3L"
        assert result.series_id == "8208"
        conflicts = db.query(SeriesConflict).filter_by(status="open").all()
        assert len(conflicts) == 1
        assert conflicts[0].existing_value_json["id"] == first.id
        assert conflicts[0].incoming_value_json["id"] == second.id


def test_series_admin_restore_conflict_keeps_deleted_record_unchanged(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        deleted = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7411", operator="tester", reason="first"),
        )
        soft_delete_series_record(db, deleted.id, operator="tester", reason="delete")
        active = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7412", operator="tester", reason="replacement"),
        )

        restored = restore_series_record(db, deleted.id, operator="tester", reason="restore original")

        assert restored.status == "deleted"
        assert restored.deleted_at is not None
        assert db.get(ConfirmedVehicleSeries, active.id).status == "active"
        conflicts = db.query(SeriesConflict).filter_by(status="open").all()
        assert len(conflicts) == 1
        assert conflicts[0].existing_value_json["id"] == active.id
        assert conflicts[0].incoming_value_json["id"] == deleted.id


def test_upsert_confirmed_vehicle_series_does_not_mutate_deleted_row(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        deleted = ConfirmedVehicleSeries(
            query_key="风云t11",
            query="风云T11",
            platform="autohome",
            series_id="7411",
            status="deleted",
        )
        db.add(deleted)
        db.commit()

        upsert_confirmed_vehicle_series(
            db,
            query="风云T11",
            selected_candidates={"autohome": {"series_id": "7412", "source": "manual"}},
        )
        db.commit()

        rows = db.query(ConfirmedVehicleSeries).filter_by(query_key="风云t11", platform="autohome").all()
        assert len(rows) == 2
        assert [row.series_id for row in rows if row.status == "deleted"] == ["7411"]
        assert [row.series_id for row in rows if row.status == "active"] == ["7412"]


def test_upsert_confirmed_vehicle_series_handles_concurrent_same_vehicle(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, future=True)
    start = Barrier(4)

    def worker(index: int) -> None:
        start.wait()
        with SessionLocal() as db:
            upsert_confirmed_vehicle_series(
                db,
                query="风云T11",
                selected_candidates={
                    "autohome": {"series_id": str(7411 + index), "source": "concurrent"},
                    "dongchedi": {"series_id": str(9436 + index), "source": "concurrent"},
                },
            )
            db.commit()

    with ThreadPoolExecutor(max_workers=4) as executor:
        for future in [executor.submit(worker, index) for index in range(4)]:
            future.result()

    with SessionLocal() as db:
        rows = (
            db.query(ConfirmedVehicleSeries)
            .filter(ConfirmedVehicleSeries.query_key == "风云t11", ConfirmedVehicleSeries.status == "active")
            .all()
        )
        assert {row.platform for row in rows} == {"autohome", "dongchedi"}
        assert len(rows) == 2
        assert all(row.source == "concurrent" for row in rows)


def test_series_admin_list_filters_paginates_and_counts(tmp_path: Path) -> None:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as db:
        t11_auto = create_series_record(
            db,
            SeriesMutation(
                query="风云T11",
                platform="autohome",
                series_id="7411",
                title="风云T11",
                source="manual",
                operator="tester",
                reason="one",
            ),
        )
        create_series_record(
            db,
            SeriesMutation(
                query="风云T11",
                platform="dongchedi",
                series_id="5498",
                title="风云T11",
                source="sync",
                operator="tester",
                reason="two",
            ),
        )
        create_series_record(
            db,
            SeriesMutation(
                query="风云X3L",
                platform="autohome",
                series_id="8208",
                title="风云X3L",
                source="manual",
                operator="tester",
                reason="three",
            ),
        )
        soft_delete_series_record(db, t11_auto.id, operator="tester", reason="delete")

        result = list_series_records(db, search="风云", platform="autohome", source="manual", limit=1, offset=1)
        assert result.total == 2
        assert result.limit == 1
        assert result.offset == 1
        assert len(result.items) == 1

        deleted = list_series_records(db, status="deleted")
        assert deleted.total == 1
        assert deleted.items[0].id == t11_auto.id


def test_series_admin_routes_crud_list_filters_and_audit(tmp_path: Path) -> None:
    client = make_admin_client(tmp_path)

    create_response = client.post(
        "/api/admin/series",
        json={
            "query": "风云T11",
            "platform": "autohome",
            "series_id": "7411",
            "url": "https://k.autohome.com.cn/7411",
            "title": "风云T11",
            "source": "manual",
            "operator": "tester",
            "reason": "confirmed from admin",
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()
    record_id = created["id"]
    assert created["status"] == "active"
    assert created["query_key"] == "风云t11"

    active_list = client.get("/api/admin/series?search=风云&platform=autohome&status=active")
    assert active_list.status_code == 200
    assert active_list.json()["total"] == 1
    assert active_list.json()["limit"] == 50
    assert active_list.json()["offset"] == 0
    assert active_list.json()["items"][0]["id"] == record_id

    capped_list = client.get("/api/admin/series?limit=999&offset=-10")
    assert capped_list.status_code == 200
    assert capped_list.json()["limit"] == 200
    assert capped_list.json()["offset"] == 0

    update_response = client.patch(
        f"/api/admin/series/{record_id}",
        json={
            "query": "风云T11",
            "platform": "autohome",
            "series_id": "7412",
            "url": "https://k.autohome.com.cn/7412",
            "title": "风云T11 2026",
            "source": "manual",
            "operator": "tester",
            "reason": "corrected id",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["series_id"] == "7412"

    delete_response = client.request(
        "DELETE",
        f"/api/admin/series/{record_id}",
        json={"operator": "tester", "reason": "temporary removal"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"

    deleted_list = client.get("/api/admin/series?search=7412&platform=autohome&status=deleted")
    assert deleted_list.status_code == 200
    assert deleted_list.json()["total"] == 1
    assert deleted_list.json()["items"][0]["id"] == record_id

    restore_response = client.post(
        f"/api/admin/series/{record_id}/restore",
        json={"operator": "tester", "reason": "restore after review"},
    )
    assert restore_response.status_code == 200
    assert restore_response.json()["status"] == "active"

    audit_response = client.get(f"/api/admin/series/audit?record_id={record_id}")
    assert audit_response.status_code == 200
    assert audit_response.json()["total"] == 4
    assert audit_response.json()["limit"] == 50
    assert audit_response.json()["offset"] == 0
    assert [item["action"] for item in audit_response.json()["items"]] == ["create", "update", "delete", "restore"]

    capped_audit = client.get(f"/api/admin/series/audit?record_id={record_id}&limit=999&offset=-1")
    assert capped_audit.status_code == 200
    assert capped_audit.json()["limit"] == 200
    assert capped_audit.json()["offset"] == 0


def test_series_admin_routes_manage_aliases(tmp_path: Path) -> None:
    client = make_admin_client(tmp_path)

    create_response = client.post(
        "/api/admin/series/aliases",
        json={"alias": "奇瑞风云T11", "canonical_query": "风云T11"},
    )
    assert create_response.status_code == 201
    created = create_response.json()
    alias_id = created["id"]
    assert created["alias_key"] == "奇瑞风云t11"

    blank_response = client.post(
        "/api/admin/series/aliases",
        json={"alias": "   ", "canonical_query": "风云T11"},
    )
    assert blank_response.status_code == 400

    list_response = client.get("/api/admin/series/aliases?search=奇瑞")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["id"] == alias_id

    capped_response = client.get("/api/admin/series/aliases?limit=999&offset=-3")
    assert capped_response.status_code == 200
    assert capped_response.json()["limit"] == 200
    assert capped_response.json()["offset"] == 0

    update_response = client.patch(
        f"/api/admin/series/aliases/{alias_id}",
        json={"alias": "风云T11 Pro", "canonical_query": "风云T11"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["alias"] == "风云T11 Pro"

    delete_response = client.delete(f"/api/admin/series/aliases/{alias_id}")
    assert delete_response.status_code == 204

    missing_response = client.patch(
        f"/api/admin/series/aliases/{alias_id}",
        json={"alias": "风云T11", "canonical_query": "风云T11"},
    )
    assert missing_response.status_code == 404


def test_series_admin_routes_require_passphrase_when_access_control_enabled(tmp_path: Path) -> None:
    client = make_admin_client(tmp_path, access_control_enabled=True)

    unauthorized = client.get("/api/admin/series")
    assert unauthorized.status_code == 401

    unauthorized_mutation = client.post("/api/admin/series", json={})
    assert unauthorized_mutation.status_code == 401

    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    authorized = client.get("/api/admin/series")
    assert authorized.status_code == 200


def test_series_admin_routes_reject_blank_and_invalid_series_payloads(tmp_path: Path) -> None:
    client = make_admin_client(tmp_path)

    blank_response = client.post(
        "/api/admin/series",
        json={
            "query": "   ",
            "platform": "autohome",
            "series_id": "7411",
            "operator": "tester",
            "reason": "blank query",
        },
    )
    assert blank_response.status_code == 422

    invalid_platform = client.post(
        "/api/admin/series",
        json={
            "query": "风云T11",
            "platform": "unknown",
            "series_id": "7411",
            "operator": "tester",
            "reason": "invalid platform",
        },
    )
    assert invalid_platform.status_code == 422


def test_series_admin_routes_report_active_conflicts(tmp_path: Path) -> None:
    client = make_admin_client(tmp_path)

    first = client.post(
        "/api/admin/series",
        json={
            "query": "风云T11",
            "platform": "autohome",
            "series_id": "7411",
            "operator": "tester",
            "reason": "seed first",
        },
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/admin/series",
        json={
            "query": "风云T11",
            "platform": "autohome",
            "series_id": "9999",
            "operator": "tester",
            "reason": "duplicate",
        },
    )
    assert duplicate.status_code == 409

    second = client.post(
        "/api/admin/series",
        json={
            "query": "风云X3L",
            "platform": "autohome",
            "series_id": "8208",
            "operator": "tester",
            "reason": "seed second",
        },
    )
    assert second.status_code == 201
    second_id = second.json()["id"]

    update_conflict = client.patch(
        f"/api/admin/series/{second_id}",
        json={
            "query": "风云T11",
            "platform": "autohome",
            "series_id": "8208",
            "operator": "tester",
            "reason": "conflicting rename",
        },
    )
    assert update_conflict.status_code == 409

    delete_first = client.request(
        "DELETE",
        f"/api/admin/series/{first.json()['id']}",
        json={"operator": "tester", "reason": "make deleted"},
    )
    assert delete_first.status_code == 200

    replacement = client.post(
        "/api/admin/series",
        json={
            "query": "风云T11",
            "platform": "autohome",
            "series_id": "7412",
            "operator": "tester",
            "reason": "replacement",
        },
    )
    assert replacement.status_code == 201

    restore_conflict = client.post(
        f"/api/admin/series/{first.json()['id']}/restore",
        json={"operator": "tester", "reason": "restore conflict"},
    )
    assert restore_conflict.status_code == 409
