from __future__ import annotations

from pathlib import Path
import sys

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import init_db, reset_engine_cache
from app.models import ConfirmedVehicleSeries, SeriesAlias, SeriesAuditLog, SeriesConflict, SeriesImportBatch


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
        db.add(alias)
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
            platform="autohome",
            existing_value_json={"series_id": "7411"},
            incoming_value_json={"series_id": "9999"},
            status="open",
            import_batch_id=batch.id,
        )
        db.add(conflict)
        db.commit()

        assert db.query(SeriesImportBatch).one().source == "legacy_sync"
        assert db.query(ConfirmedVehicleSeries).one().status == "active"
        assert db.query(SeriesAlias).one().canonical_query == "风云T11"
        assert db.query(SeriesAuditLog).one().operator == "tester"
        assert db.query(SeriesConflict).one().status == "open"
