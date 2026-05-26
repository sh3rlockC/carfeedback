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
    has_active_index = any(row["name"] == "uq_confirmed_vehicle_series_active_query_platform" for row in index_rows)
    has_full_unique_index = any(
        row["unique"] and row["name"] != "uq_confirmed_vehicle_series_active_query_platform"
        for row in index_rows
    )
    if has_active_index and not has_full_unique_index:
        return

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
