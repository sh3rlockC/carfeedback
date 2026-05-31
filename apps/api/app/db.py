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
    return {"pool_pre_ping": True}


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

        if "task_vehicles" in table_names:
            task_vehicle_columns = {column["name"] for column in inspector.get_columns("task_vehicles")}
            if "enabled_platforms" not in task_vehicle_columns:
                if dialect == "postgresql":
                    conn.execute(
                        text(
                            "ALTER TABLE task_vehicles "
                            "ADD COLUMN enabled_platforms JSONB NOT NULL DEFAULT '[\"autohome\", \"dongchedi\"]'::jsonb"
                        )
                    )
                else:
                    conn.execute(
                        text(
                            "ALTER TABLE task_vehicles "
                            "ADD COLUMN enabled_platforms JSON NOT NULL DEFAULT '[\"autohome\", \"dongchedi\"]'"
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
