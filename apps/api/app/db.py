from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.models import Base

_ENGINE = None
_SESSION_LOCAL = None


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


def init_db(settings: Settings | None = None) -> None:
    global _ENGINE, _SESSION_LOCAL
    settings = settings or get_settings()
    _ENGINE = create_engine(settings.database_url, future=True, **_engine_kwargs(settings.database_url))
    _SESSION_LOCAL = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)
    Base.metadata.create_all(_ENGINE)
    _sync_existing_schema(_ENGINE)


def get_engine(settings: Settings | None = None):
    global _ENGINE
    if _ENGINE is None:
        init_db(settings)
    return _ENGINE


def reset_engine_cache() -> None:
    global _ENGINE, _SESSION_LOCAL
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SESSION_LOCAL = None


def _sync_existing_schema(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if "jobs" in table_names:
            job_columns = {column["name"] for column in inspector.get_columns("jobs")}
            if "collection_mode" not in job_columns:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN collection_mode VARCHAR(32) NOT NULL DEFAULT 'incremental'"))
            if "collection_summary" not in job_columns:
                if dialect == "postgresql":
                    conn.execute(text("ALTER TABLE jobs ADD COLUMN collection_summary JSONB NOT NULL DEFAULT '{}'::jsonb"))
                else:
                    conn.execute(text("ALTER TABLE jobs ADD COLUMN collection_summary JSON NOT NULL DEFAULT '{}'"))

        if "confirmed_vehicle_series" in table_names:
            confirmed_columns = {column["name"] for column in inspector.get_columns("confirmed_vehicle_series")}
            if "status" not in confirmed_columns:
                conn.execute(text("ALTER TABLE confirmed_vehicle_series ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'active'"))
            if "deleted_at" not in confirmed_columns:
                if dialect == "postgresql":
                    conn.execute(text("ALTER TABLE confirmed_vehicle_series ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE"))
                else:
                    conn.execute(text("ALTER TABLE confirmed_vehicle_series ADD COLUMN deleted_at DATETIME"))
            if "import_batch_id" not in confirmed_columns:
                conn.execute(text("ALTER TABLE confirmed_vehicle_series ADD COLUMN import_batch_id INTEGER"))
            if dialect == "postgresql":
                conn.execute(text("ALTER TABLE confirmed_vehicle_series DROP CONSTRAINT IF EXISTS uq_confirmed_vehicle_series_query_platform"))
                conn.execute(text("DROP INDEX IF EXISTS uq_confirmed_vehicle_series_query_platform"))
                conn.execute(
                    text(
                        """
                        DO $$
                        DECLARE
                            existing_definition text;
                        BEGIN
                            SELECT indexdef INTO existing_definition
                            FROM pg_indexes
                            WHERE schemaname = current_schema()
                              AND tablename = 'confirmed_vehicle_series'
                              AND indexname = 'uq_confirmed_vehicle_series_active_query_platform';

                            IF existing_definition IS NOT NULL
                               AND (
                                   lower(existing_definition) NOT LIKE 'create unique index%'
                                   OR lower(existing_definition) NOT LIKE '%query_key%'
                                   OR lower(existing_definition) NOT LIKE '%platform%'
                                   OR lower(existing_definition) NOT LIKE '%where%'
                                   OR lower(existing_definition) NOT LIKE '%status%'
                                   OR lower(existing_definition) NOT LIKE '%active%'
                               ) THEN
                                DROP INDEX uq_confirmed_vehicle_series_active_query_platform;
                            END IF;
                        END $$;
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_confirmed_vehicle_series_active_query_platform
                        ON confirmed_vehicle_series (query_key, platform)
                        WHERE status = 'active'
                        """
                    )
                )
            elif dialect == "sqlite":
                _sync_confirmed_vehicle_series_sqlite_indexes(conn)


def _sync_confirmed_vehicle_series_sqlite_indexes(conn) -> None:
    index_rows = conn.execute(text("PRAGMA index_list('confirmed_vehicle_series')")).mappings().all()
    active_index_name = "uq_confirmed_vehicle_series_active_query_platform"
    active_index = next((row for row in index_rows if row["name"] == active_index_name), None)
    if active_index is not None and not _sqlite_active_index_is_valid(conn, active_index):
        conn.execute(text(f"DROP INDEX {active_index_name}"))
        index_rows = [row for row in index_rows if row["name"] != active_index_name]
        active_index = None

    legacy_unique_indexes = [
        row
        for row in index_rows
        if _sqlite_index_is_legacy_confirmed_series_unique(conn, row)
    ]
    if not legacy_unique_indexes:
        if active_index is None:
            _create_confirmed_vehicle_series_sqlite_active_index(conn)
        return

    _assert_confirmed_vehicle_series_sqlite_rebuild_safe(conn, index_rows, legacy_unique_indexes)
    _rebuild_confirmed_vehicle_series_sqlite_table(conn)
    _create_confirmed_vehicle_series_sqlite_active_index(conn)


def _sqlite_index_columns(conn, index_name: str) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(text(f"PRAGMA index_info('{index_name}')")).mappings().all()
    ]


def _sqlite_index_sql(conn, index_name: str) -> str | None:
    return conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": index_name},
    ).scalar_one_or_none()


def _sqlite_active_index_is_valid(conn, index_row) -> bool:
    index_sql = (_sqlite_index_sql(conn, index_row["name"]) or "").lower()
    return (
        bool(index_row["unique"])
        and bool(index_row["partial"])
        and _sqlite_index_columns(conn, index_row["name"]) == ["query_key", "platform"]
        and "where" in index_sql
        and "status" in index_sql
        and "active" in index_sql
    )


def _sqlite_index_is_legacy_confirmed_series_unique(conn, index_row) -> bool:
    if not index_row["unique"] or index_row["partial"]:
        return False
    return _sqlite_index_columns(conn, index_row["name"]) == ["query_key", "platform"]


def _assert_confirmed_vehicle_series_sqlite_rebuild_safe(conn, index_rows, legacy_unique_indexes) -> None:
    expected_columns = {
        "id",
        "query_key",
        "query",
        "platform",
        "series_id",
        "status",
        "url",
        "title",
        "source",
        "import_batch_id",
        "deleted_at",
        "created_at",
        "updated_at",
    }
    actual_columns = {
        row["name"]
        for row in conn.execute(text("PRAGMA table_info('confirmed_vehicle_series')")).mappings().all()
    }
    if actual_columns != expected_columns:
        raise RuntimeError(
            "Cannot rebuild confirmed_vehicle_series because it has unexpected columns; "
            f"expected {sorted(expected_columns)}, found {sorted(actual_columns)}"
        )

    legacy_names = {row["name"] for row in legacy_unique_indexes}
    unknown_indexes = [
        row["name"]
        for row in index_rows
        if row["name"] not in legacy_names
        and row["name"] != "uq_confirmed_vehicle_series_active_query_platform"
    ]
    if unknown_indexes:
        raise RuntimeError(
            "Cannot rebuild confirmed_vehicle_series because it has indexes that would need manual migration: "
            f"{sorted(unknown_indexes)}"
        )

    triggers = [
        row["name"]
        for row in conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'confirmed_vehicle_series'")
        ).mappings().all()
    ]
    if triggers:
        raise RuntimeError(
            "Cannot rebuild confirmed_vehicle_series because it has triggers that would need manual migration: "
            f"{sorted(triggers)}"
        )


def _rebuild_confirmed_vehicle_series_sqlite_table(conn) -> None:
    conn.execute(text("DROP TABLE IF EXISTS confirmed_vehicle_series_new"))
    conn.execute(
        text(
            """
            CREATE TABLE confirmed_vehicle_series_new (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
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
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(import_batch_id) REFERENCES series_import_batches (id)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO confirmed_vehicle_series_new (
                id,
                query_key,
                query,
                platform,
                series_id,
                status,
                url,
                title,
                source,
                import_batch_id,
                deleted_at,
                created_at,
                updated_at
            )
            SELECT
                id,
                query_key,
                query,
                platform,
                series_id,
                COALESCE(status, 'active'),
                url,
                title,
                source,
                import_batch_id,
                deleted_at,
                created_at,
                updated_at
            FROM confirmed_vehicle_series
            """
        )
    )
    conn.execute(text("DROP TABLE confirmed_vehicle_series"))
    conn.execute(text("ALTER TABLE confirmed_vehicle_series_new RENAME TO confirmed_vehicle_series"))


def _create_confirmed_vehicle_series_sqlite_active_index(conn) -> None:
    conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_confirmed_vehicle_series_active_query_platform
            ON confirmed_vehicle_series (query_key, platform)
            WHERE status = 'active'
            """
        )
    )


def get_session_local():
    global _SESSION_LOCAL
    if _SESSION_LOCAL is None:
        init_db()
    return _SESSION_LOCAL


def get_db() -> Generator[Session, None, None]:
    session_local = get_session_local()
    db = session_local()
    try:
        yield db
    finally:
        db.close()
