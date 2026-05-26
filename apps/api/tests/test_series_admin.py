from __future__ import annotations

from pathlib import Path
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import init_db, reset_engine_cache
from app.models import ConfirmedVehicleSeries, SeriesAlias, SeriesAuditLog, SeriesConflict, SeriesImportBatch
from app.services.confirmed_vehicle_series import confirmed_vehicle_series_payload
from app.services.series_admin import (
    SeriesMutation,
    create_series_record,
    list_series_records,
    restore_series_record,
    soft_delete_series_record,
    update_series_record,
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
