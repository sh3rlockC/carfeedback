from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from threading import Barrier

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import _active_status_predicate_is_valid, _postgres_active_index_is_valid, init_db, reset_engine_cache
from app.models import ConfirmedVehicleSeries, SeriesAlias, SeriesAuditLog, SeriesConflict, SeriesImportBatch
from app.services.confirmed_vehicle_series import confirmed_vehicle_series_payload, upsert_confirmed_vehicle_series
from app.services.series_admin import (
    SeriesMutation,
    create_series_record,
    list_series_records,
    restore_series_record,
    soft_delete_series_record,
    update_series_record,
)


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
