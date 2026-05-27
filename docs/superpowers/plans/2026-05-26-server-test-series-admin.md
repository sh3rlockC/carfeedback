# Server Test Series Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved same-server test rollout, `seriesId` admin, alias lookup, sync/audit tooling, and real-environment validation path.

**Architecture:** Extend the existing FastAPI/SQLAlchemy data model around `confirmed_vehicle_series`, add focused services for admin operations, import/export, alias resolution, and server rollout scripts. The Next.js UI gets a hidden `/series-admin` workbench that calls new admin APIs, while deployment stays isolated through a dedicated compose/env stack and explicit server scripts.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, openpyxl, csv, Next.js App Router, React, TypeScript, Docker Compose, shell scripts, pytest, npm typecheck/build.

---

## Scope And Boundaries

The spec covers four connected subsystems that must ship together but can be implemented as independent commits:

1. Data model and backend services for `seriesId`, conflict, audit, import batch, and alias.
2. Admin API and hidden `/series-admin` UI.
3. Sync/audit/deployment scripts for the isolated `koubei-test` server environment.
4. Verification workflow for real testing and cutover readiness.

Do not connect to production until all local tests and the written server runbook are complete. Production server discovery is a separate approved execution step and must list candidates if multiple SSH targets are found.

## File Map

- Modify `apps/api/app/models.py`: add status fields and new audit/conflict/import/alias models.
- Modify `apps/api/app/db.py`: schema sync for existing databases.
- Modify `apps/api/app/schemas.py`: admin request/response schemas.
- Create `apps/api/app/services/series_admin.py`: CRUD, filters, audit, soft delete/restore, conflict handling.
- Create `apps/api/app/services/series_import_export.py`: Excel/CSV import preview, commit, Excel export.
- Create `apps/api/app/services/vehicle_aliases.py`: alias CRUD and alias-to-canonical resolution.
- Modify `apps/api/app/services/vehicle_resolver.py`: resolve aliases before confirmed series lookup.
- Create `apps/api/app/routes/series_admin.py`: `/api/admin/series/*` routes.
- Modify `apps/api/app/main.py`: include the new router.
- Create `apps/api/tests/test_series_admin.py`: backend tests for all admin behavior.
- Create `apps/api/tests/test_series_import_export.py`: import/export tests.
- Modify `apps/api/tests/test_vehicle_resolver.py`: alias resolution tests.
- Modify `apps/web/lib/api-types.ts`: admin DTOs.
- Create `apps/web/app/series-admin/page.tsx`: hidden admin UI.
- Modify `apps/web/app/globals.css`: flat dense admin styles.
- Modify `apps/web/scripts/verify-ui-contracts.mjs`: hidden route and copy contracts.
- Create `scripts/series-admin/export_series_audit.py`: CLI audit/export helper.
- Create `scripts/series-admin/sync_confirmed_series.py`: production-to-test and merge helper.
- Create `scripts/series-admin/audit_cutover.py`: compare production and test databases for `seriesId`, raw comments, and task results before cutover.
- Create `scripts/server-test/backup-production.sh`: backup script.
- Create `scripts/server-test/setup-test-env.sh`: isolated test env bootstrap script.
- Create `scripts/server-test/resource-watch.sh`: terminal resource alert script for CPU, memory, and disk pressure.
- Create `ops/test/docker-compose.test.yml`: isolated compose stack.
- Create `ops/test/.env.test.example`: test env template.
- Create `docs/server-test-series-admin-runbook.md`: operator runbook.
- Create `docs/server-test-real-validation.md`: real-environment validation checklist and result log template.

---

### Task 1: Data Model And Schema Sync

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/db.py`
- Test: `apps/api/tests/test_series_admin.py`

- [ ] **Step 1: Write failing model tests**

Add `apps/api/tests/test_series_admin.py`:

```python
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

        assert db.query(ConfirmedVehicleSeries).one().status == "active"
        assert db.query(SeriesAlias).one().canonical_query == "风云T11"
        assert db.query(SeriesAuditLog).one().operator == "tester"
        assert db.query(SeriesConflict).one().status == "open"
```

- [ ] **Step 2: Run model tests and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_admin.py -q
```

Expected: FAIL because `SeriesAlias`, `SeriesAuditLog`, `SeriesConflict`, `SeriesImportBatch`, and new columns do not exist.

- [ ] **Step 3: Add models and columns**

Modify `apps/api/app/models.py`:

```python
class ConfirmedVehicleSeries(Base):
    __tablename__ = "confirmed_vehicle_series"
    __table_args__ = (UniqueConstraint("query_key", "platform", name="uq_confirmed_vehicle_series_query_platform"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_key: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    series_id: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    import_batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SeriesImportBatch(Base):
    __tablename__ = "series_import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SeriesAuditLog(Base):
    __tablename__ = "series_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    old_value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    new_value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SeriesConflict(Base):
    __tablename__ = "series_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conflict_type: Mapped[str] = mapped_column(String(64), nullable=False)
    query_key: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    existing_value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    incoming_value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    import_batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class SeriesAlias(Base):
    __tablename__ = "series_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alias_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_query: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
```

- [ ] **Step 4: Add schema sync for existing DBs**

Modify `_sync_existing_schema` in `apps/api/app/db.py`:

```python
    if "confirmed_vehicle_series" in table_names:
        series_columns = {column["name"] for column in inspector.get_columns("confirmed_vehicle_series")}
        with engine.begin() as conn:
            if "status" not in series_columns:
                conn.execute(text("ALTER TABLE confirmed_vehicle_series ADD COLUMN status VARCHAR(32) NOT NULL DEFAULT 'active'"))
            if "deleted_at" not in series_columns:
                conn.execute(text("ALTER TABLE confirmed_vehicle_series ADD COLUMN deleted_at TIMESTAMP NULL"))
            if "import_batch_id" not in series_columns:
                conn.execute(text("ALTER TABLE confirmed_vehicle_series ADD COLUMN import_batch_id INTEGER NULL"))
```

- [ ] **Step 5: Run model tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_admin.py -q
```

Expected: PASS.

Commit:

```bash
git add apps/api/app/models.py apps/api/app/db.py apps/api/tests/test_series_admin.py
git commit -m "feat: add series admin data model"
```

---

### Task 2: Series Admin Service

**Files:**
- Create: `apps/api/app/services/series_admin.py`
- Modify: `apps/api/app/services/confirmed_vehicle_series.py`
- Test: `apps/api/tests/test_series_admin.py`

- [ ] **Step 1: Add failing service tests**

Append to `apps/api/tests/test_series_admin.py`:

```python
from app.services.series_admin import (
    SeriesMutation,
    create_series_record,
    list_series_records,
    restore_series_record,
    soft_delete_series_record,
    update_series_record,
)


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
```

- [ ] **Step 2: Run service tests and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_admin.py -q
```

Expected: FAIL because `apps/api/app/services/series_admin.py` does not exist.

- [ ] **Step 3: Implement service**

Create `apps/api/app/services/series_admin.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import ConfirmedVehicleSeries, SeriesAuditLog, SeriesConflict
from app.services.confirmed_vehicle_series import query_key


def now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SeriesMutation:
    query: str
    platform: str
    series_id: str
    operator: str
    reason: str
    url: str | None = None
    title: str | None = None
    source: str | None = None
    status: str = "active"


@dataclass
class SeriesListResult:
    items: list[ConfirmedVehicleSeries]
    total: int


def _snapshot(record: ConfirmedVehicleSeries | None) -> dict[str, Any]:
    if record is None:
        return {}
    return {
        "id": record.id,
        "query": record.query,
        "query_key": record.query_key,
        "platform": record.platform,
        "series_id": record.series_id,
        "url": record.url,
        "title": record.title,
        "source": record.source,
        "status": record.status,
    }


def _audit(db: Session, *, record_id: int | None, action: str, operator: str, reason: str, old: dict[str, Any], new: dict[str, Any]) -> None:
    db.add(
        SeriesAuditLog(
            record_id=record_id,
            action=action,
            operator=operator.strip(),
            reason=reason.strip(),
            old_value_json=old,
            new_value_json=new,
        )
    )


def _active_conflict(db: Session, *, key: str, platform: str, exclude_id: int | None = None) -> ConfirmedVehicleSeries | None:
    query = db.query(ConfirmedVehicleSeries).filter(
        ConfirmedVehicleSeries.query_key == key,
        ConfirmedVehicleSeries.platform == platform,
        ConfirmedVehicleSeries.status == "active",
    )
    if exclude_id is not None:
        query = query.filter(ConfirmedVehicleSeries.id != exclude_id)
    return query.one_or_none()


def create_series_record(db: Session, mutation: SeriesMutation) -> ConfirmedVehicleSeries:
    key = query_key(mutation.query)
    existing = _active_conflict(db, key=key, platform=mutation.platform)
    if existing is not None:
        conflict = SeriesConflict(
            conflict_type="active_identity",
            query_key=key,
            query=mutation.query.strip(),
            platform=mutation.platform,
            existing_value_json=_snapshot(existing),
            incoming_value_json={
                "query": mutation.query,
                "platform": mutation.platform,
                "series_id": mutation.series_id,
            },
            status="open",
        )
        db.add(conflict)
        db.commit()
        return existing

    record = ConfirmedVehicleSeries(
        query_key=key,
        query=mutation.query.strip(),
        platform=mutation.platform,
        series_id=mutation.series_id.strip(),
        url=mutation.url,
        title=mutation.title,
        source=mutation.source,
        status=mutation.status,
        created_at=now(),
        updated_at=now(),
    )
    db.add(record)
    db.flush()
    _audit(db, record_id=record.id, action="create", operator=mutation.operator, reason=mutation.reason, old={}, new=_snapshot(record))
    db.commit()
    db.refresh(record)
    return record


def update_series_record(db: Session, record_id: int, mutation: SeriesMutation, *, allow_conflict: bool = True) -> ConfirmedVehicleSeries:
    record = db.get(ConfirmedVehicleSeries, record_id)
    if record is None:
        raise ValueError("series record not found")

    old = _snapshot(record)
    key = query_key(mutation.query)
    existing = _active_conflict(db, key=key, platform=mutation.platform, exclude_id=record.id)
    if existing is not None and not allow_conflict:
        db.add(
            SeriesConflict(
                conflict_type="active_identity",
                query_key=key,
                query=mutation.query.strip(),
                platform=mutation.platform,
                existing_value_json=_snapshot(existing),
                incoming_value_json={**_snapshot(record), "query": mutation.query, "series_id": mutation.series_id},
                status="open",
            )
        )
        db.commit()
        db.refresh(record)
        return record

    record.query = mutation.query.strip()
    record.query_key = key
    record.platform = mutation.platform
    record.series_id = mutation.series_id.strip()
    record.url = mutation.url
    record.title = mutation.title
    record.source = mutation.source
    record.status = mutation.status
    record.updated_at = now()
    _audit(db, record_id=record.id, action="update", operator=mutation.operator, reason=mutation.reason, old=old, new=_snapshot(record))
    db.commit()
    db.refresh(record)
    return record


def soft_delete_series_record(db: Session, record_id: int, *, operator: str, reason: str) -> ConfirmedVehicleSeries:
    record = db.get(ConfirmedVehicleSeries, record_id)
    if record is None:
        raise ValueError("series record not found")
    old = _snapshot(record)
    record.status = "deleted"
    record.deleted_at = now()
    record.updated_at = now()
    _audit(db, record_id=record.id, action="delete", operator=operator, reason=reason, old=old, new=_snapshot(record))
    db.commit()
    db.refresh(record)
    return record


def restore_series_record(db: Session, record_id: int, *, operator: str, reason: str) -> ConfirmedVehicleSeries:
    record = db.get(ConfirmedVehicleSeries, record_id)
    if record is None:
        raise ValueError("series record not found")
    old = _snapshot(record)
    record.status = "active"
    record.deleted_at = None
    record.updated_at = now()
    _audit(db, record_id=record.id, action="restore", operator=operator, reason=reason, old=old, new=_snapshot(record))
    db.commit()
    db.refresh(record)
    return record


def list_series_records(
    db: Session,
    *,
    search: str | None = None,
    platform: str | None = None,
    status: str | None = "active",
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> SeriesListResult:
    query = db.query(ConfirmedVehicleSeries)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(ConfirmedVehicleSeries.query.like(like))
    if platform:
        query = query.filter(ConfirmedVehicleSeries.platform == platform)
    if status:
        query = query.filter(ConfirmedVehicleSeries.status == status)
    if source:
        query = query.filter(ConfirmedVehicleSeries.source == source)
    total = query.count()
    items = query.order_by(ConfirmedVehicleSeries.updated_at.desc(), ConfirmedVehicleSeries.id.desc()).offset(offset).limit(limit).all()
    return SeriesListResult(items=items, total=total)
```

- [ ] **Step 4: Update confirmed lookup to ignore deleted rows**

Modify `confirmed_vehicle_series_payload` in `apps/api/app/services/confirmed_vehicle_series.py`:

```python
    records = (
        db.query(ConfirmedVehicleSeries)
        .filter(
            ConfirmedVehicleSeries.query_key == key,
            ConfirmedVehicleSeries.status == "active",
        )
        .all()
    )
```

- [ ] **Step 5: Run service tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_admin.py -q
```

Expected: PASS.

Commit:

```bash
git add apps/api/app/services/series_admin.py apps/api/app/services/confirmed_vehicle_series.py apps/api/tests/test_series_admin.py
git commit -m "feat: add series admin service"
```

---

### Task 3: Import, Preview, Conflict, And Export Service

**Files:**
- Create: `apps/api/app/services/series_import_export.py`
- Test: `apps/api/tests/test_series_import_export.py`

- [ ] **Step 1: Write failing import/export tests**

Create `apps/api/tests/test_series_import_export.py`:

```python
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys

from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import init_db, reset_engine_cache
from app.models import ConfirmedVehicleSeries, SeriesConflict
from app.services.series_import_export import commit_series_import, export_series_excel, preview_series_import


def db_session(tmp_path: Path):
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series-import.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    return sessionmaker(bind=engine, future=True)()


def csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def test_preview_counts_new_duplicate_and_conflict(tmp_path: Path) -> None:
    db = db_session(tmp_path)
    try:
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="autohome",
                series_id="7411",
                status="active",
            )
        )
        db.commit()
        payload = csv_bytes(
            "query,platform,series_id,url,title,source\n"
            "风云T11,autohome,7411,https://k.autohome.com.cn/7411,风云T11,legacy\n"
            "风云T11,autohome,9999,https://k.autohome.com.cn/9999,风云T11,legacy\n"
            "风云T11,dongchedi,9436,https://www.dongchedi.com/auto/series/9436,风云T11,legacy\n"
        )

        preview = preview_series_import(db, filename="series.csv", content=payload)

        assert preview.summary == {"new": 1, "duplicate": 1, "conflict": 1, "invalid": 0}
        assert preview.rows[0].status == "duplicate"
        assert preview.rows[1].status == "conflict"
        assert preview.rows[2].status == "new"
    finally:
        db.close()


def test_commit_import_writes_only_new_rows_and_conflicts(tmp_path: Path) -> None:
    db = db_session(tmp_path)
    try:
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="autohome",
                series_id="7411",
                status="active",
            )
        )
        db.commit()
        payload = csv_bytes(
            "query,platform,series_id,url,title,source\n"
            "风云T11,autohome,9999,https://k.autohome.com.cn/9999,风云T11,legacy\n"
            "风云T11,dongchedi,9436,https://www.dongchedi.com/auto/series/9436,风云T11,legacy\n"
        )

        result = commit_series_import(db, filename="series.csv", content=payload, operator="tester")

        assert result.summary["new"] == 1
        assert result.summary["conflict"] == 1
        assert db.query(ConfirmedVehicleSeries).filter(ConfirmedVehicleSeries.platform == "dongchedi").one().series_id == "9436"
        assert db.query(SeriesConflict).one().incoming_value_json["series_id"] == "9999"
    finally:
        db.close()


def test_export_series_excel_contains_active_rows(tmp_path: Path) -> None:
    db = db_session(tmp_path)
    try:
        db.add(ConfirmedVehicleSeries(query_key="风云t11", query="风云T11", platform="autohome", series_id="7411", status="active"))
        db.commit()

        data = export_series_excel(db)

        workbook = load_workbook(BytesIO(data))
        sheet = workbook.active
        assert sheet.cell(row=1, column=1).value == "query"
        assert sheet.cell(row=2, column=1).value == "风云T11"
        assert sheet.cell(row=2, column=3).value == "7411"
    finally:
        db.close()
```

- [ ] **Step 2: Run import/export tests and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_import_export.py -q
```

Expected: FAIL because `series_import_export` does not exist.

- [ ] **Step 3: Implement import/export service**

Create `apps/api/app/services/series_import_export.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO, StringIO
import csv

from openpyxl import Workbook, load_workbook
from sqlalchemy.orm import Session

from app.models import ConfirmedVehicleSeries, SeriesConflict, SeriesImportBatch
from app.services.confirmed_vehicle_series import query_key
from app.services.series_admin import SeriesMutation, create_series_record


@dataclass
class ImportRowPreview:
    row_number: int
    query: str
    platform: str
    series_id: str
    url: str | None
    title: str | None
    source: str | None
    status: str
    message: str


@dataclass
class ImportPreview:
    summary: dict[str, int]
    rows: list[ImportRowPreview]


REQUIRED_COLUMNS = ("query", "platform", "series_id")


def _rows_from_csv(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    return [dict(row) for row in csv.DictReader(StringIO(text))]


def _rows_from_excel(content: bytes) -> list[dict[str, str]]:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    headers = [str(cell.value or "").strip() for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    rows: list[dict[str, str]] = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        rows.append({headers[index]: "" if value is None else str(value) for index, value in enumerate(values)})
    return rows


def _parse_rows(filename: str, content: bytes) -> list[dict[str, str]]:
    if filename.lower().endswith(".csv"):
        return _rows_from_csv(content)
    if filename.lower().endswith((".xlsx", ".xlsm")):
        return _rows_from_excel(content)
    raise ValueError("unsupported import file type")


def _active_record(db: Session, query: str, platform: str) -> ConfirmedVehicleSeries | None:
    return (
        db.query(ConfirmedVehicleSeries)
        .filter(
            ConfirmedVehicleSeries.query_key == query_key(query),
            ConfirmedVehicleSeries.platform == platform,
            ConfirmedVehicleSeries.status == "active",
        )
        .one_or_none()
    )


def preview_series_import(db: Session, *, filename: str, content: bytes) -> ImportPreview:
    parsed = _parse_rows(filename, content)
    summary = {"new": 0, "duplicate": 0, "conflict": 0, "invalid": 0}
    previews: list[ImportRowPreview] = []
    for index, row in enumerate(parsed, start=2):
        query = str(row.get("query") or "").strip()
        platform = str(row.get("platform") or "").strip()
        series_id = str(row.get("series_id") or "").strip()
        url = str(row.get("url") or "").strip() or None
        title = str(row.get("title") or "").strip() or None
        source = str(row.get("source") or "").strip() or None
        if not query or platform not in {"autohome", "dongchedi"} or not series_id:
            status = "invalid"
            message = "query, platform, and series_id are required"
        else:
            existing = _active_record(db, query, platform)
            if existing is None:
                status = "new"
                message = "new active mapping"
            elif existing.series_id == series_id:
                status = "duplicate"
                message = "same active mapping already exists"
            else:
                status = "conflict"
                message = f"existing active series_id is {existing.series_id}"
        summary[status] += 1
        previews.append(ImportRowPreview(index, query, platform, series_id, url, title, source, status, message))
    return ImportPreview(summary=summary, rows=previews)


def commit_series_import(db: Session, *, filename: str, content: bytes, operator: str) -> ImportPreview:
    preview = preview_series_import(db, filename=filename, content=content)
    batch = SeriesImportBatch(source="import", filename=filename, operator=operator, summary_json=preview.summary)
    db.add(batch)
    db.flush()
    for row in preview.rows:
        if row.status == "new":
            created = create_series_record(
                db,
                SeriesMutation(
                    query=row.query,
                    platform=row.platform,
                    series_id=row.series_id,
                    url=row.url,
                    title=row.title,
                    source=row.source,
                    operator=operator,
                    reason=f"import batch {batch.id}",
                ),
            )
            created.import_batch_id = batch.id
            db.add(created)
        elif row.status == "conflict":
            existing = _active_record(db, row.query, row.platform)
            db.add(
                SeriesConflict(
                    conflict_type="series_import",
                    query_key=query_key(row.query),
                    query=row.query,
                    platform=row.platform,
                    existing_value_json={"id": existing.id, "series_id": existing.series_id} if existing else {},
                    incoming_value_json={"series_id": row.series_id, "url": row.url, "title": row.title, "source": row.source},
                    status="open",
                    import_batch_id=batch.id,
                )
            )
    db.commit()
    return preview


def export_series_excel(db: Session) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "confirmed_vehicle_series"
    headers = ["query", "platform", "series_id", "url", "title", "source", "status", "updated_at"]
    sheet.append(headers)
    rows = db.query(ConfirmedVehicleSeries).order_by(ConfirmedVehicleSeries.query.asc(), ConfirmedVehicleSeries.platform.asc()).all()
    for record in rows:
        sheet.append([record.query, record.platform, record.series_id, record.url, record.title, record.source, record.status, record.updated_at.isoformat()])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
```

- [ ] **Step 4: Run import/export tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_import_export.py apps/api/tests/test_series_admin.py -q
```

Expected: PASS.

Commit:

```bash
git add apps/api/app/services/series_import_export.py apps/api/tests/test_series_import_export.py
git commit -m "feat: add series import export service"
```

---

### Task 4: Alias Service And Resolver Integration

**Files:**
- Create: `apps/api/app/services/vehicle_aliases.py`
- Modify: `apps/api/app/services/vehicle_resolver.py`
- Test: `apps/api/tests/test_vehicle_resolver.py`

- [ ] **Step 1: Write failing alias tests**

Append to `apps/api/tests/test_vehicle_resolver.py`:

```python
from app.models import ConfirmedVehicleSeries, SeriesAlias


def test_vehicle_resolver_uses_active_alias_for_confirmed_series(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'alias.db'}", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        db.add(SeriesAlias(alias_key="奇瑞风云t11", alias="奇瑞风云T11", canonical_query="风云T11"))
        db.add(ConfirmedVehicleSeries(query_key="风云t11", query="风云T11", platform="autohome", series_id="7411", status="active"))
        db.add(ConfirmedVehicleSeries(query_key="风云t11", query="风云T11", platform="dongchedi", series_id="9436", status="active"))
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
        db.add(SeriesAlias(alias_key="风云", alias="风云", canonical_query="风云X3L"))
        db.add(ConfirmedVehicleSeries(query_key="风云t11", query="风云T11", platform="autohome", series_id="7411", status="active"))
        db.add(ConfirmedVehicleSeries(query_key="风云t11", query="风云T11", platform="dongchedi", series_id="9436", status="active"))
        db.add(ConfirmedVehicleSeries(query_key="风云x3l", query="风云X3L", platform="autohome", series_id="8208", status="active"))
        db.add(ConfirmedVehicleSeries(query_key="风云x3l", query="风云X3L", platform="dongchedi", series_id="25545", status="active"))
        db.commit()

        resolver = VehicleResolver(settings=make_service_settings(tmp_path), db=db)
        result = resolver.resolve("风云")

        assert result["query"] == "风云"
        assert {candidate["series_id"] for candidate in result["autohome"]["candidates"]} == {"7411", "8208"}
        assert result["autohome"]["best"] is None
    finally:
        db.close()
```

- [ ] **Step 2: Run alias tests and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_vehicle_resolver.py::test_vehicle_resolver_uses_active_alias_for_confirmed_series apps/api/tests/test_vehicle_resolver.py::test_vehicle_resolver_returns_candidates_for_duplicate_alias -q
```

Expected: FAIL because alias service is not used.

- [ ] **Step 3: Implement alias service**

Create `apps/api/app/services/vehicle_aliases.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ConfirmedVehicleSeries, SeriesAlias
from app.services.confirmed_vehicle_series import PLATFORMS, query_key


@dataclass
class AliasResolution:
    canonical_queries: list[str]


def alias_key(value: str) -> str:
    return query_key(value)


def create_alias(db: Session, *, alias: str, canonical_query: str) -> SeriesAlias:
    record = SeriesAlias(alias_key=alias_key(alias), alias=alias.strip(), canonical_query=canonical_query.strip())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def update_alias(db: Session, alias_id: int, *, alias: str, canonical_query: str) -> SeriesAlias:
    record = db.get(SeriesAlias, alias_id)
    if record is None:
        raise ValueError("alias not found")
    record.alias_key = alias_key(alias)
    record.alias = alias.strip()
    record.canonical_query = canonical_query.strip()
    db.commit()
    db.refresh(record)
    return record


def delete_alias(db: Session, alias_id: int) -> None:
    record = db.get(SeriesAlias, alias_id)
    if record is None:
        raise ValueError("alias not found")
    db.delete(record)
    db.commit()


def resolve_alias(db: Session | None, query: str) -> AliasResolution:
    if db is None:
        return AliasResolution([])
    rows = db.query(SeriesAlias).filter(SeriesAlias.alias_key == alias_key(query)).all()
    canonical = sorted({row.canonical_query for row in rows if row.canonical_query})
    return AliasResolution(canonical_queries=canonical)


def confirmed_payload_for_canonical_candidates(db: Session, original_query: str, canonical_queries: list[str]) -> dict | None:
    if not canonical_queries:
        return None
    if len(canonical_queries) == 1:
        from app.services.confirmed_vehicle_series import confirmed_vehicle_series_payload

        return confirmed_vehicle_series_payload(db, canonical_queries[0])

    payload: dict = {"query": original_query}
    for platform in PLATFORMS:
        candidates = []
        for canonical_query in canonical_queries:
            key = query_key(canonical_query)
            record = (
                db.query(ConfirmedVehicleSeries)
                .filter(
                    ConfirmedVehicleSeries.query_key == key,
                    ConfirmedVehicleSeries.platform == platform,
                    ConfirmedVehicleSeries.status == "active",
                )
                .one_or_none()
            )
            if record is not None:
                candidates.append(
                    {
                        "series_id": record.series_id,
                        "url": record.url,
                        "title": record.title or record.query,
                        "source": record.source or "series_alias",
                        "evidence_url": record.url,
                        "kind": "alias_candidate",
                        "note": f"别名命中 {record.query}",
                    }
                )
        payload[platform] = {"best": None, "candidates": candidates}
    return payload
```

- [ ] **Step 4: Integrate aliases into resolver**

Modify `VehicleResolver.resolve` in `apps/api/app/services/vehicle_resolver.py`:

```python
        from app.services.vehicle_aliases import confirmed_payload_for_canonical_candidates, resolve_alias

        alias_resolution = resolve_alias(self.db, normalized_query)
        alias_payload = confirmed_payload_for_canonical_candidates(self.db, normalized_query, alias_resolution.canonical_queries)
        if alias_payload is not None:
            return alias_payload

        confirmed = confirmed_vehicle_series_payload(self.db, normalized_query)
        if confirmed is not None:
            return confirmed
```

- [ ] **Step 5: Run alias tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_vehicle_resolver.py -q
```

Expected: PASS.

Commit:

```bash
git add apps/api/app/services/vehicle_aliases.py apps/api/app/services/vehicle_resolver.py apps/api/tests/test_vehicle_resolver.py
git commit -m "feat: resolve vehicle aliases"
```

---

### Task 5: Admin API Routes

**Files:**
- Modify: `apps/api/app/schemas.py`
- Create: `apps/api/app/routes/series_admin.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_series_admin.py`

- [ ] **Step 1: Add failing route tests**

Append to `apps/api/tests/test_series_admin.py`:

```python
from fastapi.testclient import TestClient

from app.main import create_app


def make_series_client(tmp_path: Path) -> TestClient:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series-api.db'}", access_control_enabled=False)
    return TestClient(create_app(settings))


def test_series_admin_api_crud_and_audit(tmp_path: Path) -> None:
    client = make_series_client(tmp_path)

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
            "reason": "manual confirmation",
        },
    )
    assert create_response.status_code == 200
    record_id = create_response.json()["id"]

    list_response = client.get("/api/admin/series?search=风云&platform=autohome&status=active")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["series_id"] == "7411"

    update_response = client.put(
        f"/api/admin/series/{record_id}",
        json={
            "query": "风云T11",
            "platform": "autohome",
            "series_id": "7412",
            "operator": "tester",
            "reason": "corrected",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["series_id"] == "7412"

    delete_response = client.post(f"/api/admin/series/{record_id}/delete", json={"operator": "tester", "reason": "cleanup"})
    assert delete_response.status_code == 200
    assert delete_response.json()["status"] == "deleted"

    restore_response = client.post(f"/api/admin/series/{record_id}/restore", json={"operator": "tester", "reason": "restore"})
    assert restore_response.status_code == 200
    assert restore_response.json()["status"] == "active"

    audit_response = client.get(f"/api/admin/series/{record_id}/audit")
    assert audit_response.status_code == 200
    assert [item["action"] for item in audit_response.json()["items"]] == ["create", "update", "delete", "restore"]


def test_series_admin_api_alias_crud(tmp_path: Path) -> None:
    client = make_series_client(tmp_path)

    created = client.post("/api/admin/series/aliases", json={"alias": "奇瑞风云T11", "canonical_query": "风云T11"})
    assert created.status_code == 200
    alias_id = created.json()["id"]

    listed = client.get("/api/admin/series/aliases?search=奇瑞")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["canonical_query"] == "风云T11"

    updated = client.put(f"/api/admin/series/aliases/{alias_id}", json={"alias": "风云十一", "canonical_query": "风云T11"})
    assert updated.status_code == 200
    assert updated.json()["alias"] == "风云十一"

    deleted = client.delete(f"/api/admin/series/aliases/{alias_id}")
    assert deleted.status_code == 204
```

- [ ] **Step 2: Run route tests and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_admin.py::test_series_admin_api_crud_and_audit apps/api/tests/test_series_admin.py::test_series_admin_api_alias_crud -q
```

Expected: FAIL with 404 for `/api/admin/series`.

- [ ] **Step 3: Add schemas**

Append to `apps/api/app/schemas.py`:

```python
class SeriesMutationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=255)
    platform: Literal["autohome", "dongchedi"]
    series_id: str = Field(min_length=1, max_length=64)
    url: str | None = None
    title: str | None = None
    source: str | None = None
    status: Literal["active", "archived", "deleted"] = "active"
    operator: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=1000)


class SeriesActionRequest(BaseModel):
    operator: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=1000)


class SeriesRecordResponse(BaseModel):
    id: int
    query_key: str
    query: str
    platform: str
    series_id: str
    url: str | None = None
    title: str | None = None
    source: str | None = None
    status: str
    import_batch_id: int | None = None
    created_at: datetime
    updated_at: datetime


class SeriesListResponse(BaseModel):
    items: list[SeriesRecordResponse]
    total: int


class SeriesAuditResponse(BaseModel):
    items: list[dict]


class SeriesAliasRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=255)
    canonical_query: str = Field(min_length=1, max_length=255)


class SeriesAliasResponse(BaseModel):
    id: int
    alias_key: str
    alias: str
    canonical_query: str
    created_at: datetime
    updated_at: datetime


class SeriesAliasListResponse(BaseModel):
    items: list[SeriesAliasResponse]
    total: int
```

- [ ] **Step 4: Add route**

Create `apps/api/app/routes/series_admin.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import SeriesAlias, SeriesAuditLog
from app.schemas import (
    SeriesActionRequest,
    SeriesAliasListResponse,
    SeriesAliasRequest,
    SeriesAliasResponse,
    SeriesAuditResponse,
    SeriesListResponse,
    SeriesMutationRequest,
    SeriesRecordResponse,
)
from app.services.series_admin import (
    SeriesMutation,
    create_series_record,
    list_series_records,
    restore_series_record,
    soft_delete_series_record,
    update_series_record,
)
from app.services.vehicle_aliases import create_alias, delete_alias, update_alias

router = APIRouter(prefix="/api/admin/series", tags=["series-admin"])


def _mutation(payload: SeriesMutationRequest) -> SeriesMutation:
    return SeriesMutation(**payload.model_dump())


@router.get("", response_model=SeriesListResponse)
def list_series(
    search: str | None = None,
    platform: str | None = None,
    status_filter: str | None = "active",
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> SeriesListResponse:
    result = list_series_records(db, search=search, platform=platform, status=status_filter, source=source, limit=limit, offset=offset)
    return SeriesListResponse(items=[SeriesRecordResponse.model_validate(item, from_attributes=True) for item in result.items], total=result.total)


@router.post("", response_model=SeriesRecordResponse)
def create_series(payload: SeriesMutationRequest, db: Session = Depends(get_db)) -> SeriesRecordResponse:
    return SeriesRecordResponse.model_validate(create_series_record(db, _mutation(payload)), from_attributes=True)


@router.put("/{record_id}", response_model=SeriesRecordResponse)
def update_series(record_id: int, payload: SeriesMutationRequest, db: Session = Depends(get_db)) -> SeriesRecordResponse:
    try:
        record = update_series_record(db, record_id, _mutation(payload), allow_conflict=False)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SeriesRecordResponse.model_validate(record, from_attributes=True)


@router.post("/{record_id}/delete", response_model=SeriesRecordResponse)
def delete_series(record_id: int, payload: SeriesActionRequest, db: Session = Depends(get_db)) -> SeriesRecordResponse:
    try:
        record = soft_delete_series_record(db, record_id, operator=payload.operator, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SeriesRecordResponse.model_validate(record, from_attributes=True)


@router.post("/{record_id}/restore", response_model=SeriesRecordResponse)
def restore_series(record_id: int, payload: SeriesActionRequest, db: Session = Depends(get_db)) -> SeriesRecordResponse:
    try:
        record = restore_series_record(db, record_id, operator=payload.operator, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SeriesRecordResponse.model_validate(record, from_attributes=True)


@router.get("/{record_id}/audit", response_model=SeriesAuditResponse)
def list_audit(record_id: int, db: Session = Depends(get_db)) -> SeriesAuditResponse:
    rows = db.query(SeriesAuditLog).filter(SeriesAuditLog.record_id == record_id).order_by(SeriesAuditLog.id.asc()).all()
    return SeriesAuditResponse(
        items=[
            {
                "id": row.id,
                "action": row.action,
                "operator": row.operator,
                "reason": row.reason,
                "old_value_json": row.old_value_json,
                "new_value_json": row.new_value_json,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.get("/aliases", response_model=SeriesAliasListResponse)
def list_aliases(search: str | None = None, db: Session = Depends(get_db)) -> SeriesAliasListResponse:
    query = db.query(SeriesAlias)
    if search:
        query = query.filter(SeriesAlias.alias.like(f"%{search.strip()}%"))
    total = query.count()
    rows = query.order_by(SeriesAlias.updated_at.desc(), SeriesAlias.id.desc()).limit(200).all()
    return SeriesAliasListResponse(items=[SeriesAliasResponse.model_validate(row, from_attributes=True) for row in rows], total=total)


@router.post("/aliases", response_model=SeriesAliasResponse)
def add_alias(payload: SeriesAliasRequest, db: Session = Depends(get_db)) -> SeriesAliasResponse:
    return SeriesAliasResponse.model_validate(create_alias(db, alias=payload.alias, canonical_query=payload.canonical_query), from_attributes=True)


@router.put("/aliases/{alias_id}", response_model=SeriesAliasResponse)
def edit_alias(alias_id: int, payload: SeriesAliasRequest, db: Session = Depends(get_db)) -> SeriesAliasResponse:
    try:
        record = update_alias(db, alias_id, alias=payload.alias, canonical_query=payload.canonical_query)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SeriesAliasResponse.model_validate(record, from_attributes=True)


@router.delete("/aliases/{alias_id}", status_code=204)
def remove_alias(alias_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        delete_alias(db, alias_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=204)
```

- [ ] **Step 5: Register route**

Modify `apps/api/app/main.py`:

```python
from app.routes.series_admin import router as series_admin_router
```

and include:

```python
    app.include_router(series_admin_router)
```

- [ ] **Step 6: Run route tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_admin.py -q
```

Expected: PASS.

Commit:

```bash
git add apps/api/app/schemas.py apps/api/app/routes/series_admin.py apps/api/app/main.py apps/api/tests/test_series_admin.py
git commit -m "feat: add series admin api"
```

---

### Task 6: Import/Export API Endpoints

**Files:**
- Modify: `apps/api/app/routes/series_admin.py`
- Modify: `apps/api/app/schemas.py`
- Test: `apps/api/tests/test_series_import_export.py`

- [ ] **Step 1: Add failing API import/export tests**

Append to `apps/api/tests/test_series_import_export.py`:

```python
from fastapi.testclient import TestClient
from app.main import create_app


def make_import_client(tmp_path: Path) -> TestClient:
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series-import-api.db'}", access_control_enabled=False)
    return TestClient(create_app(settings))


def test_series_import_preview_and_commit_api(tmp_path: Path) -> None:
    client = make_import_client(tmp_path)
    content = (
        "query,platform,series_id,url,title,source\n"
        "风云T11,autohome,7411,https://k.autohome.com.cn/7411,风云T11,legacy\n"
    ).encode("utf-8")

    preview = client.post("/api/admin/series/import/preview", files={"file": ("series.csv", content, "text/csv")})
    assert preview.status_code == 200
    assert preview.json()["summary"]["new"] == 1

    commit = client.post("/api/admin/series/import/commit?operator=tester", files={"file": ("series.csv", content, "text/csv")})
    assert commit.status_code == 200
    assert commit.json()["summary"]["new"] == 1


def test_series_export_api_returns_xlsx(tmp_path: Path) -> None:
    client = make_import_client(tmp_path)
    client.post(
        "/api/admin/series",
        json={"query": "风云T11", "platform": "autohome", "series_id": "7411", "operator": "tester", "reason": "seed"},
    )

    response = client.get("/api/admin/series/export.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert len(response.content) > 100
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_import_export.py::test_series_import_preview_and_commit_api apps/api/tests/test_series_import_export.py::test_series_export_api_returns_xlsx -q
```

Expected: FAIL with 404.

- [ ] **Step 3: Add route schemas**

Append to `apps/api/app/schemas.py`:

```python
class SeriesImportRowResponse(BaseModel):
    row_number: int
    query: str
    platform: str
    series_id: str
    url: str | None = None
    title: str | None = None
    source: str | None = None
    status: str
    message: str


class SeriesImportPreviewResponse(BaseModel):
    summary: dict[str, int]
    rows: list[SeriesImportRowResponse]
```

- [ ] **Step 4: Add import/export routes**

Modify `apps/api/app/routes/series_admin.py` imports:

```python
from fastapi import File, UploadFile
from fastapi.responses import Response
from app.schemas import SeriesImportPreviewResponse, SeriesImportRowResponse
from app.services.series_import_export import commit_series_import, export_series_excel, preview_series_import
```

Add endpoints before alias routes:

```python
def _import_response(preview) -> SeriesImportPreviewResponse:
    return SeriesImportPreviewResponse(
        summary=preview.summary,
        rows=[SeriesImportRowResponse(**row.__dict__) for row in preview.rows],
    )


@router.post("/import/preview", response_model=SeriesImportPreviewResponse)
async def preview_import(file: UploadFile = File(...), db: Session = Depends(get_db)) -> SeriesImportPreviewResponse:
    content = await file.read()
    return _import_response(preview_series_import(db, filename=file.filename or "series.csv", content=content))


@router.post("/import/commit", response_model=SeriesImportPreviewResponse)
async def commit_import(operator: str, file: UploadFile = File(...), db: Session = Depends(get_db)) -> SeriesImportPreviewResponse:
    content = await file.read()
    return _import_response(commit_series_import(db, filename=file.filename or "series.csv", content=content, operator=operator))


@router.get("/export.xlsx")
def export_series(db: Session = Depends(get_db)) -> Response:
    data = export_series_excel(db)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="confirmed_vehicle_series.xlsx"'},
    )
```

- [ ] **Step 5: Run import/export API tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_import_export.py -q
```

Expected: PASS.

Commit:

```bash
git add apps/api/app/routes/series_admin.py apps/api/app/schemas.py apps/api/tests/test_series_import_export.py
git commit -m "feat: add series import export api"
```

---

### Task 7: Hidden `/series-admin` Frontend

**Files:**
- Modify: `apps/web/lib/api-types.ts`
- Create: `apps/web/app/series-admin/page.tsx`
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/scripts/verify-ui-contracts.mjs`

- [ ] **Step 1: Add UI contract checks first**

Modify `apps/web/scripts/verify-ui-contracts.mjs`:

```js
assertIncludes("apps/web/app/series-admin/page.tsx", "SeriesAdminPage");
assertIncludes("apps/web/app/series-admin/page.tsx", "series-admin");
assertIncludes("apps/web/app/series-admin/page.tsx", "别名");
assertIncludes("apps/web/app/series-admin/page.tsx", "导入预览");
assertNotIncludes("apps/web/app/components/app-chrome.tsx", "/series-admin");
```

- [ ] **Step 2: Run UI contract and verify failure**

Run:

```bash
npm --prefix apps/web run verify:ui
```

Expected: FAIL because `apps/web/app/series-admin/page.tsx` does not exist.

- [ ] **Step 3: Add TypeScript DTOs**

Append to `apps/web/lib/api-types.ts`:

```ts
export type SeriesRecord = {
  id: number;
  query_key: string;
  query: string;
  platform: "autohome" | "dongchedi";
  series_id: string;
  url: string | null;
  title: string | null;
  source: string | null;
  status: "active" | "archived" | "deleted";
  import_batch_id: number | null;
  created_at: string;
  updated_at: string;
};

export type SeriesListResponse = {
  items: SeriesRecord[];
  total: number;
};

export type SeriesMutationRequest = {
  query: string;
  platform: "autohome" | "dongchedi";
  series_id: string;
  url?: string;
  title?: string;
  source?: string;
  status?: "active" | "archived" | "deleted";
  operator: string;
  reason: string;
};

export type SeriesAlias = {
  id: number;
  alias_key: string;
  alias: string;
  canonical_query: string;
  created_at: string;
  updated_at: string;
};

export type SeriesAliasListResponse = {
  items: SeriesAlias[];
  total: number;
};

export type SeriesImportPreviewResponse = {
  summary: Record<string, number>;
  rows: Array<{
    row_number: number;
    query: string;
    platform: string;
    series_id: string;
    status: string;
    message: string;
  }>;
};
```

- [ ] **Step 4: Create admin page**

Create `apps/web/app/series-admin/page.tsx`:

```tsx
"use client";

import type { ChangeEvent, FormEvent } from "react";
import { useEffect, useState } from "react";
import { Download, FileUp, RefreshCw, RotateCcw, Save, Trash2 } from "lucide-react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, toJsonBody } from "@/lib/api";
import type { SeriesAlias, SeriesAliasListResponse, SeriesImportPreviewResponse, SeriesListResponse, SeriesMutationRequest, SeriesRecord } from "@/lib/api-types";
import { withBasePath } from "@/lib/paths";

type Tab = "series" | "aliases" | "import";

const emptyDraft: SeriesMutationRequest = {
  query: "",
  platform: "autohome",
  series_id: "",
  url: "",
  title: "",
  source: "",
  status: "active",
  operator: "",
  reason: "",
};

export default function SeriesAdminPage() {
  const [tab, setTab] = useState<Tab>("series");
  const [records, setRecords] = useState<SeriesRecord[]>([]);
  const [aliases, setAliases] = useState<SeriesAlias[]>([]);
  const [draft, setDraft] = useState<SeriesMutationRequest>(emptyDraft);
  const [aliasDraft, setAliasDraft] = useState({ alias: "", canonical_query: "" });
  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("active");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<SeriesImportPreviewResponse | null>(null);
  const [message, setMessage] = useState("");

  async function loadSeries() {
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (platform) params.set("platform", platform);
    if (status) params.set("status_filter", status);
    const payload = await apiRequest<SeriesListResponse>(`/api/admin/series?${params.toString()}`);
    setRecords(payload.items);
  }

  async function loadAliases() {
    const payload = await apiRequest<SeriesAliasListResponse>("/api/admin/series/aliases");
    setAliases(payload.items);
  }

  useEffect(() => {
    void loadSeries();
    void loadAliases();
  }, []);

  async function saveSeries(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = await apiRequest<SeriesRecord>("/api/admin/series", { method: "POST", body: toJsonBody(draft) });
    setMessage(`已保存 ${payload.query} ${payload.platform}`);
    setDraft(emptyDraft);
    await loadSeries();
  }

  async function removeRecord(record: SeriesRecord) {
    const payload = { operator: draft.operator || "series-admin", reason: draft.reason || "admin action" };
    await apiRequest<SeriesRecord>(`/api/admin/series/${record.id}/delete`, { method: "POST", body: toJsonBody(payload) });
    await loadSeries();
  }

  async function restoreRecord(record: SeriesRecord) {
    const payload = { operator: draft.operator || "series-admin", reason: draft.reason || "admin action" };
    await apiRequest<SeriesRecord>(`/api/admin/series/${record.id}/restore`, { method: "POST", body: toJsonBody(payload) });
    await loadSeries();
  }

  async function saveAlias(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = await apiRequest<SeriesAlias>("/api/admin/series/aliases", { method: "POST", body: toJsonBody(aliasDraft) });
    setMessage(`已保存别名 ${payload.alias}`);
    setAliasDraft({ alias: "", canonical_query: "" });
    await loadAliases();
  }

  async function deleteAlias(alias: SeriesAlias) {
    await apiRequest<null>(`/api/admin/series/aliases/${alias.id}`, { method: "DELETE" });
    await loadAliases();
  }

  async function previewImport() {
    if (!selectedFile) return;
    const form = new FormData();
    form.append("file", selectedFile);
    const response = await fetch(withBasePath("/api/admin/series/import/preview"), { method: "POST", body: form, credentials: "include" });
    setPreview((await response.json()) as SeriesImportPreviewResponse);
  }

  async function commitImport() {
    if (!selectedFile) return;
    const form = new FormData();
    form.append("file", selectedFile);
    const response = await fetch(withBasePath(`/api/admin/series/import/commit?operator=${encodeURIComponent(draft.operator || "series-admin")}`), {
      method: "POST",
      body: form,
      credentials: "include",
    });
    setPreview((await response.json()) as SeriesImportPreviewResponse);
    await loadSeries();
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setSelectedFile(event.target.files?.[0] ?? null);
    setPreview(null);
  }

  return (
    <main className="series-admin stack-lg">
      <SignalPanel tone="accent" className="stack">
        <SectionHeader eyebrow="SERIES ADMIN" title="车型 seriesId 管理" copy="隐藏管理入口，用于确认车型、导入导出、冲突预览和别名维护。" />
        <div className="segmented-control" role="tablist" aria-label="series-admin tabs">
          <button type="button" className={tab === "series" ? "selected" : ""} onClick={() => setTab("series")}>seriesId</button>
          <button type="button" className={tab === "aliases" ? "selected" : ""} onClick={() => setTab("aliases")}>别名</button>
          <button type="button" className={tab === "import" ? "selected" : ""} onClick={() => setTab("import")}>导入预览</button>
        </div>
        {message ? <p className="status-copy">{message}</p> : null}
      </SignalPanel>

      {tab === "series" ? (
        <section className="series-admin-grid">
          <SignalPanel className="stack">
            <div className="admin-filters">
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索车型" />
              <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
                <option value="">全部平台</option>
                <option value="autohome">汽车之家</option>
                <option value="dongchedi">懂车帝</option>
              </select>
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                <option value="active">active</option>
                <option value="archived">archived</option>
                <option value="deleted">deleted</option>
              </select>
              <button className="button secondary" type="button" onClick={loadSeries}><RefreshCw size={16} />刷新</button>
              <a className="button secondary" href={withBasePath("/api/admin/series/export.xlsx")}><Download size={16} />导出 Excel</a>
            </div>
            <div className="table-scroll">
              <table className="dense-table">
                <thead><tr><th>车型</th><th>平台</th><th>seriesId</th><th>状态</th><th>来源</th><th>操作</th></tr></thead>
                <tbody>
                  {records.map((record) => (
                    <tr key={record.id}>
                      <td>{record.query}</td>
                      <td>{record.platform}</td>
                      <td>{record.series_id}</td>
                      <td><StatusPill>{record.status}</StatusPill></td>
                      <td>{record.source ?? "-"}</td>
                      <td>
                        {record.status === "deleted" ? (
                          <button className="table-action" type="button" onClick={() => void restoreRecord(record)}><RotateCcw size={14} />恢复</button>
                        ) : (
                          <button className="table-action" type="button" onClick={() => void removeRecord(record)}><Trash2 size={14} />删除</button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </SignalPanel>
          <SignalPanel className="stack">
            <h2>新增 / 修正</h2>
            <form className="stack" onSubmit={saveSeries}>
              <input value={draft.query} onChange={(event) => setDraft({ ...draft, query: event.target.value })} placeholder="车型名" required />
              <select value={draft.platform} onChange={(event) => setDraft({ ...draft, platform: event.target.value as "autohome" | "dongchedi" })}>
                <option value="autohome">汽车之家</option>
                <option value="dongchedi">懂车帝</option>
              </select>
              <input value={draft.series_id} onChange={(event) => setDraft({ ...draft, series_id: event.target.value })} placeholder="seriesId" required />
              <input value={draft.url} onChange={(event) => setDraft({ ...draft, url: event.target.value })} placeholder="URL" />
              <input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="标题" />
              <input value={draft.source} onChange={(event) => setDraft({ ...draft, source: event.target.value })} placeholder="来源" />
              <input value={draft.operator} onChange={(event) => setDraft({ ...draft, operator: event.target.value })} placeholder="操作者" required />
              <textarea value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} placeholder="修改原因" required />
              <button className="button" type="submit"><Save size={16} />保存</button>
            </form>
          </SignalPanel>
        </section>
      ) : null}

      {tab === "aliases" ? (
        <SignalPanel className="stack">
          <form className="admin-filters" onSubmit={saveAlias}>
            <input value={aliasDraft.alias} onChange={(event) => setAliasDraft({ ...aliasDraft, alias: event.target.value })} placeholder="别名" required />
            <input value={aliasDraft.canonical_query} onChange={(event) => setAliasDraft({ ...aliasDraft, canonical_query: event.target.value })} placeholder="canonical model" required />
            <button className="button" type="submit"><Save size={16} />保存别名</button>
          </form>
          <div className="table-scroll">
            <table className="dense-table">
              <thead><tr><th>别名</th><th>canonical model</th><th>操作</th></tr></thead>
              <tbody>{aliases.map((item) => <tr key={item.id}><td>{item.alias}</td><td>{item.canonical_query}</td><td><button className="table-action" type="button" onClick={() => void deleteAlias(item)}><Trash2 size={14} />硬删除</button></td></tr>)}</tbody>
            </table>
          </div>
        </SignalPanel>
      ) : null}

      {tab === "import" ? (
        <SignalPanel className="stack">
          <div className="admin-filters">
            <input type="file" accept=".csv,.xlsx,.xlsm" onChange={chooseFile} />
            <button className="button secondary" type="button" onClick={previewImport} disabled={!selectedFile}><FileUp size={16} />预览</button>
            <button className="button" type="button" onClick={commitImport} disabled={!selectedFile || !preview}>导入无冲突项</button>
          </div>
          {preview ? <pre className="admin-preview">{JSON.stringify(preview.summary, null, 2)}</pre> : null}
        </SignalPanel>
      ) : null}
    </main>
  );
}
```

- [ ] **Step 5: Add minimal CSS**

Append to `apps/web/app/globals.css`:

```css
.series-admin-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(280px, 0.8fr);
  gap: 16px;
}

.admin-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.admin-filters input,
.admin-filters select,
.series-admin textarea {
  min-height: 38px;
  border: 1px solid var(--line-strong);
  background: var(--surface);
  color: var(--ink);
  padding: 8px 10px;
  border-radius: 6px;
}

.admin-preview {
  padding: 14px;
  border: 1px solid var(--line);
  background: var(--surface-muted);
  overflow: auto;
}

@media (max-width: 900px) {
  .series-admin-grid {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 6: Run frontend checks and commit**

Run:

```bash
npm --prefix apps/web run verify:ui
npm --prefix apps/web run typecheck
```

Expected: both PASS.

Commit:

```bash
git add apps/web/app/series-admin/page.tsx apps/web/lib/api-types.ts apps/web/app/globals.css apps/web/scripts/verify-ui-contracts.mjs
git commit -m "feat: add hidden series admin page"
```

---

### Task 8: Sync And Audit CLI Scripts

**Files:**
- Create: `scripts/series-admin/sync_confirmed_series.py`
- Create: `scripts/series-admin/export_series_audit.py`
- Test: `apps/api/tests/test_series_import_export.py`

- [ ] **Step 1: Add failing script tests**

Append to `apps/api/tests/test_series_import_export.py`:

```python
def test_sync_confirmed_series_script_imports_non_conflicting_rows(tmp_path: Path) -> None:
    import subprocess

    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    for db_path, series_id in [(source, "7411")]:
        settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{db_path}")
        reset_engine_cache()
        init_db(settings)
        engine = create_engine(settings.database_url, future=True)
        SessionLocal = sessionmaker(bind=engine, future=True)
        with SessionLocal() as db:
            db.add(ConfirmedVehicleSeries(query_key="风云t11", query="风云T11", platform="autohome", series_id=series_id, status="active"))
            db.commit()

    result = subprocess.run(
        [
            "python",
            "scripts/series-admin/sync_confirmed_series.py",
            "--source-url",
            f"sqlite+pysqlite:///{source}",
            "--target-url",
            f"sqlite+pysqlite:///{target}",
            "--operator",
            "tester",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "new=1" in result.stdout
```

- [ ] **Step 2: Run script test and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_import_export.py::test_sync_confirmed_series_script_imports_non_conflicting_rows -q
```

Expected: FAIL because script does not exist.

- [ ] **Step 3: Implement sync script**

Create `scripts/series-admin/sync_confirmed_series.py`:

```python
#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, ConfirmedVehicleSeries
from app.services.series_admin import SeriesMutation, create_series_record


def rows(database_url: str) -> list[ConfirmedVehicleSeries]:
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        return db.query(ConfirmedVehicleSeries).filter(ConfirmedVehicleSeries.status == "active").all()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--operator", required=True)
    args = parser.parse_args()

    target_engine = create_engine(args.target_url, future=True)
    Base.metadata.create_all(target_engine)
    TargetSession = sessionmaker(bind=target_engine, future=True)
    new_count = 0
    conflict_count = 0
    duplicate_count = 0
    with TargetSession() as target:
        for row in rows(args.source_url):
            existing = (
                target.query(ConfirmedVehicleSeries)
                .filter(
                    ConfirmedVehicleSeries.query_key == row.query_key,
                    ConfirmedVehicleSeries.platform == row.platform,
                    ConfirmedVehicleSeries.status == "active",
                )
                .one_or_none()
            )
            if existing is None:
                create_series_record(
                    target,
                    SeriesMutation(
                        query=row.query,
                        platform=row.platform,
                        series_id=row.series_id,
                        url=row.url,
                        title=row.title,
                        source=row.source,
                        operator=args.operator,
                        reason="legacy sync",
                    ),
                )
                new_count += 1
            elif existing.series_id == row.series_id:
                duplicate_count += 1
            else:
                create_series_record(
                    target,
                    SeriesMutation(query=row.query, platform=row.platform, series_id=row.series_id, operator=args.operator, reason="legacy sync conflict"),
                )
                conflict_count += 1
    print(f"new={new_count} duplicate={duplicate_count} conflict={conflict_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Implement audit export script**

Create `scripts/series-admin/export_series_audit.py`:

```python
#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from app.models import Base, SeriesAuditLog, SeriesConflict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    engine = create_engine(args.database_url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    workbook = Workbook()
    audit_sheet = workbook.active
    audit_sheet.title = "series_audit"
    audit_sheet.append(["id", "record_id", "action", "operator", "reason", "created_at"])
    conflicts_sheet = workbook.create_sheet("series_conflicts")
    conflicts_sheet.append(["id", "conflict_type", "query", "platform", "status", "created_at"])
    with SessionLocal() as db:
        for row in db.query(SeriesAuditLog).order_by(SeriesAuditLog.id.asc()).all():
            audit_sheet.append([row.id, row.record_id, row.action, row.operator, row.reason, row.created_at.isoformat()])
        for row in db.query(SeriesConflict).order_by(SeriesConflict.id.asc()).all():
            conflicts_sheet.append([row.id, row.conflict_type, row.query, row.platform, row.status, row.created_at.isoformat()])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_series_import_export.py -q
```

Expected: PASS.

Commit:

```bash
git add scripts/series-admin/sync_confirmed_series.py scripts/series-admin/export_series_audit.py apps/api/tests/test_series_import_export.py
git commit -m "feat: add series sync audit scripts"
```

---

### Task 9: Isolated Server Test Compose And Scripts

**Files:**
- Create: `ops/test/docker-compose.test.yml`
- Create: `ops/test/.env.test.example`
- Create: `scripts/server-test/backup-production.sh`
- Create: `scripts/server-test/setup-test-env.sh`
- Create: `docs/server-test-series-admin-runbook.md`

- [ ] **Step 1: Add test compose file**

Create `ops/test/docker-compose.test.yml`:

```yaml
services:
  nginx:
    image: nginx:1.27-alpine
    depends_on:
      web:
        condition: service_healthy
      api:
        condition: service_healthy
    ports:
      - "${TEST_HTTP_PORT:-18080}:80"
    volumes:
      - ../nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ${TEST_JOB_ARTIFACTS_HOST_PATH:-./storage/jobs}:/srv/koubei/jobs:ro
    restart: unless-stopped

  web:
    build:
      context: ../..
      dockerfile: ./ops/docker/web.Dockerfile
    environment:
      PORT: 3000
      NODE_ENV: production
      BACKEND_ORIGIN: http://api:8000
      NEXT_PUBLIC_BASE_PATH: ""
    healthcheck:
      test: ["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:3000/').then(r => { if (!r.ok) process.exit(1) }).catch(() => process.exit(1))\""]
      interval: 30s
      timeout: 10s
      retries: 5
    restart: unless-stopped

  api:
    build:
      context: ../..
      dockerfile: ./ops/docker/api.Dockerfile
    environment:
      APP_ENV: test
      BASE_URL: ${TEST_BASE_URL:-http://localhost:18080}
      DATABASE_URL: postgresql+psycopg://koubei_test:${TEST_POSTGRES_PASSWORD:-koubei_test}@postgres:5432/koubei_test
      REDIS_URL: redis://redis:6379/0
      ARTIFACT_ROOT: /srv/koubei/jobs
      CORPUS_ROOT: /srv/koubei/corpus
      WORKSPACE_ROOT: /workspace
      ACCESS_CONTROL_ENABLED: "false"
      TAVILY_API_KEY: ${TAVILY_API_KEY:-}
      LLM_PROVIDER: ${LLM_PROVIDER:-}
      LLM_API_KEY: ${LLM_API_KEY:-}
      LLM_BASE_URL: ${LLM_BASE_URL:-}
      LLM_MODEL_REPORT: ${LLM_MODEL_REPORT:-}
      LLM_MODEL_QA: ${LLM_MODEL_QA:-}
    volumes:
      - ${TEST_JOB_ARTIFACTS_HOST_PATH:-./storage/jobs}:/srv/koubei/jobs
      - ${TEST_CORPUS_HOST_PATH:-./storage/corpus}:/srv/koubei/corpus
      - /opt/codexwork-test:/workspace:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')\""]
      interval: 30s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: koubei_test
      POSTGRES_USER: koubei_test
      POSTGRES_PASSWORD: ${TEST_POSTGRES_PASSWORD:-koubei_test}
    volumes:
      - postgres_test_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U koubei_test -d koubei_test"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:8-alpine
    volumes:
      - redis_test_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  postgres_test_data:
  redis_test_data:
```

- [ ] **Step 2: Add env template**

Create `ops/test/.env.test.example`:

```env
COMPOSE_PROJECT_NAME=koubei-test
TEST_HTTP_PORT=18080
TEST_BASE_URL=http://localhost:18080
TEST_POSTGRES_PASSWORD=change-me-test
TEST_JOB_ARTIFACTS_HOST_PATH=/opt/codexwork-test/vehicle-koubei-web-demo/storage/jobs
TEST_CORPUS_HOST_PATH=/opt/codexwork-test/vehicle-koubei-web-demo/storage/corpus
TAVILY_API_KEY=
LLM_PROVIDER=
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL_REPORT=
LLM_MODEL_QA=
OPENCLAW_AUTOHOME_AGENT_IDS=autohome-1,autohome-2,autohome-3,autohome-4
OPENCLAW_DCD_AGENT_IDS=dongchedi-1,dongchedi-2,dongchedi-3,dongchedi-4
```

- [ ] **Step 3: Add backup script**

Create `scripts/server-test/backup-production.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

PROD_ROOT="${PROD_ROOT:-/opt/codexwork/vehicle-koubei-web-demo}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/backups/koubei}"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="${BACKUP_ROOT}/${STAMP}"

mkdir -p "${TARGET}"
cp "${PROD_ROOT}/.env" "${TARGET}/.env"
cp "${PROD_ROOT}/docker-compose.yml" "${TARGET}/docker-compose.yml"
tar -C "${PROD_ROOT}" -czf "${TARGET}/storage.tar.gz" storage || true
docker compose --project-directory "${PROD_ROOT}" exec -T postgres pg_dump -U "${POSTGRES_USER:-koubei}" "${POSTGRES_DB:-koubei}" > "${TARGET}/postgres.sql"
tar -C "${BACKUP_ROOT}" -czf "${TARGET}.tar.gz" "${STAMP}"
echo "${TARGET}.tar.gz"
```

- [ ] **Step 4: Add setup script**

Create `scripts/server-test/setup-test-env.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

TEST_ROOT="${TEST_ROOT:-/opt/codexwork-test/vehicle-koubei-web-demo}"
SOURCE_ROOT="${SOURCE_ROOT:-$(pwd)}"

mkdir -p "${TEST_ROOT}"
rsync -a --delete \
  --exclude '.git' \
  --exclude 'apps/web/node_modules' \
  --exclude 'apps/web/.next' \
  --exclude 'storage/jobs/*' \
  --exclude 'storage/corpus/*' \
  "${SOURCE_ROOT}/" "${TEST_ROOT}/"

if [[ ! -f "${TEST_ROOT}/ops/test/.env.test" ]]; then
  cp "${TEST_ROOT}/ops/test/.env.test.example" "${TEST_ROOT}/ops/test/.env.test"
fi

docker compose \
  --project-name koubei-test \
  --env-file "${TEST_ROOT}/ops/test/.env.test" \
  -f "${TEST_ROOT}/ops/test/docker-compose.test.yml" \
  up -d --build

docker compose \
  --project-name koubei-test \
  --env-file "${TEST_ROOT}/ops/test/.env.test" \
  -f "${TEST_ROOT}/ops/test/docker-compose.test.yml" \
  ps
```

- [ ] **Step 5: Add runbook**

Create `docs/server-test-series-admin-runbook.md`:

````markdown
# Server Test Series Admin Runbook

## Preconditions

- User has approved the implementation plan.
- `koubei-prod` SSH alias has been confirmed.
- Old production candidate was chosen by the user if multiple SSH hosts matched.
- Test OpenClaw agents exist: `autohome-1..4` and `dongchedi-1..4`.

## Backup

Run on the production host:

```bash
cd /opt/codexwork/vehicle-koubei-web-demo
PROD_ROOT=/opt/codexwork/vehicle-koubei-web-demo scripts/server-test/backup-production.sh
```

## Deploy Test Environment

```bash
cd /opt/codexwork-test/vehicle-koubei-web-demo
cp ops/test/.env.test.example ops/test/.env.test
scripts/server-test/setup-test-env.sh
```

## Validate

```bash
curl -f http://127.0.0.1:18080/
docker compose --project-name koubei-test --env-file ops/test/.env.test -f ops/test/docker-compose.test.yml ps
```

## Initial Series Sync

```bash
python scripts/series-admin/sync_confirmed_series.py \
  --source-url "$PROD_DATABASE_URL" \
  --target-url "$TEST_DATABASE_URL" \
  --operator "initial-sync"
```

## Audit Export

```bash
python scripts/series-admin/export_series_audit.py \
  --database-url "$TEST_DATABASE_URL" \
  --output /opt/codexwork-test/audit/series-audit.xlsx
```
````

- [ ] **Step 6: Validate compose and commit**

Run:

```bash
docker compose --env-file ops/test/.env.test.example -f ops/test/docker-compose.test.yml config --quiet
```

Expected: exit code 0.

Commit:

```bash
git add ops/test/docker-compose.test.yml ops/test/.env.test.example scripts/server-test/backup-production.sh scripts/server-test/setup-test-env.sh docs/server-test-series-admin-runbook.md
git commit -m "feat: add isolated server test deployment"
```

---

### Task 10: Cutover Audit For Series, Raw Comments, And Task Results

**Files:**
- Create: `scripts/series-admin/audit_cutover.py`
- Test: `apps/api/tests/test_cutover_audit.py`

- [ ] **Step 1: Write failing cutover audit tests**

Create `apps/api/tests/test_cutover_audit.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path
import sys

from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import init_db, reset_engine_cache
from app.models import Base, ComparisonJob, ConfirmedVehicleSeries, Job, KoubeiRawComment


def _seed_db(database_path: Path, *, include_test_only: bool) -> str:
    database_url = f"sqlite+pysqlite:///{database_path}"
    reset_engine_cache()
    init_db(Settings(app_env="test", database_url=database_url))
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    with SessionLocal() as db:
        db.add(
            ConfirmedVehicleSeries(
                query_key="风云t11",
                query="风云T11",
                platform="autohome",
                series_id="7411",
                status="active",
            )
        )
        if include_test_only:
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
                KoubeiRawComment(
                    query_key="风云t11",
                    query="风云T11",
                    model_name="风云T11",
                    platform="autohome",
                    series_id="7411",
                    source_link="https://example.test/comment/1",
                    dedupe_key="link:https://example.test/comment/1",
                    row_json={"content": "新增评论"},
                )
            )
            db.add(Job(job_id="job_test_001", query="风云T11", model_name="风云T11", status="completed", passphrase_version="dev"))
            db.add(ComparisonJob(comparison_id="cmp_test_001", status="completed", vehicle_count=2, passphrase_version="dev"))
        db.commit()
    return database_url


def test_cutover_audit_exports_series_raw_comment_and_task_sheets(tmp_path: Path) -> None:
    prod_url = _seed_db(tmp_path / "prod.db", include_test_only=False)
    test_url = _seed_db(tmp_path / "test.db", include_test_only=True)
    output = tmp_path / "cutover-audit.xlsx"

    result = subprocess.run(
        [
            "python",
            "scripts/series-admin/audit_cutover.py",
            "--prod-url",
            prod_url,
            "--test-url",
            test_url,
            "--output",
            str(output),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "series_new=1" in result.stdout
    assert "raw_comments_new=1" in result.stdout
    assert "task_results_new=2" in result.stdout
    workbook = load_workbook(output)
    assert {"summary", "series_diff", "raw_comment_diff", "task_result_diff"}.issubset(set(workbook.sheetnames))
```

- [ ] **Step 2: Run cutover audit test and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_cutover_audit.py -q
```

Expected: FAIL because `scripts/series-admin/audit_cutover.py` does not exist.

- [ ] **Step 3: Implement cutover audit script**

Create `scripts/series-admin/audit_cutover.py`:

```python
#!/usr/bin/env python
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from app.models import Base, ComparisonJob, ConfirmedVehicleSeries, Job, KoubeiRawComment


@dataclass(frozen=True)
class DiffRow:
    category: str
    key: str
    prod_value: str
    test_value: str
    action: str


def _session(database_url: str) -> Session:
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)()


def _series_rows(db: Session) -> dict[tuple[str, str], ConfirmedVehicleSeries]:
    return {
        (row.query_key, row.platform): row
        for row in db.query(ConfirmedVehicleSeries).filter(ConfirmedVehicleSeries.status == "active").all()
    }


def _raw_comment_keys(db: Session) -> set[tuple[str, str, str, str]]:
    return {
        (row.query_key, row.platform, row.series_id, row.dedupe_key)
        for row in db.query(KoubeiRawComment).all()
    }


def _task_result_keys(db: Session) -> set[tuple[str, str]]:
    jobs = {("job", row.job_id) for row in db.query(Job).filter(Job.status == "completed").all()}
    comparisons = {
        ("comparison", row.comparison_id)
        for row in db.query(ComparisonJob).filter(ComparisonJob.status == "completed").all()
    }
    return jobs | comparisons


def compare_series(prod: Session, test: Session) -> list[DiffRow]:
    prod_rows = _series_rows(prod)
    test_rows = _series_rows(test)
    rows: list[DiffRow] = []
    for key, test_row in sorted(test_rows.items()):
        prod_row = prod_rows.get(key)
        label = f"{key[0]}|{key[1]}"
        test_value = f"{test_row.series_id}|{test_row.status}"
        if prod_row is None:
            rows.append(DiffRow("series", label, "", test_value, "new"))
        elif prod_row.series_id != test_row.series_id:
            rows.append(DiffRow("series", label, f"{prod_row.series_id}|{prod_row.status}", test_value, "conflict"))
        else:
            rows.append(DiffRow("series", label, f"{prod_row.series_id}|{prod_row.status}", test_value, "duplicate"))
    for key, prod_row in sorted(prod_rows.items()):
        if key not in test_rows:
            rows.append(DiffRow("series", f"{key[0]}|{key[1]}", f"{prod_row.series_id}|{prod_row.status}", "", "missing_in_test"))
    return rows


def compare_key_sets(category: str, prod_keys: set[tuple[str, ...]], test_keys: set[tuple[str, ...]]) -> list[DiffRow]:
    rows: list[DiffRow] = []
    for key in sorted(test_keys - prod_keys):
        rows.append(DiffRow(category, "|".join(key), "", "present", "new"))
    for key in sorted(prod_keys & test_keys):
        rows.append(DiffRow(category, "|".join(key), "present", "present", "duplicate"))
    for key in sorted(prod_keys - test_keys):
        rows.append(DiffRow(category, "|".join(key), "present", "", "missing_in_test"))
    return rows


def write_workbook(output: Path, series: list[DiffRow], raw_comments: list[DiffRow], task_results: list[DiffRow]) -> None:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "summary"
    summary.append(["metric", "value"])
    all_rows = series + raw_comments + task_results
    for category in ("series", "raw_comments", "task_results"):
        for action in ("new", "duplicate", "conflict", "missing_in_test"):
            count = sum(1 for row in all_rows if row.category == category and row.action == action)
            summary.append([f"{category}_{action}", count])
    for title, rows in [
        ("series_diff", series),
        ("raw_comment_diff", raw_comments),
        ("task_result_diff", task_results),
    ]:
        sheet = workbook.create_sheet(title)
        sheet.append(["category", "key", "prod_value", "test_value", "action"])
        for row in rows:
            sheet.append([row.category, row.key, row.prod_value, row.test_value, row.action])
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prod-url", required=True)
    parser.add_argument("--test-url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with _session(args.prod_url) as prod, _session(args.test_url) as test:
        series = compare_series(prod, test)
        raw_comments = compare_key_sets("raw_comments", _raw_comment_keys(prod), _raw_comment_keys(test))
        task_results = compare_key_sets("task_results", _task_result_keys(prod), _task_result_keys(test))

    output = Path(args.output)
    write_workbook(output, series, raw_comments, task_results)
    print(
        " ".join(
            [
                f"series_new={sum(1 for row in series if row.action == 'new')}",
                f"series_conflict={sum(1 for row in series if row.action == 'conflict')}",
                f"raw_comments_new={sum(1 for row in raw_comments if row.action == 'new')}",
                f"task_results_new={sum(1 for row in task_results if row.action == 'new')}",
                f"output={output}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run cutover audit tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_cutover_audit.py -q
```

Expected: PASS.

Commit:

```bash
git add scripts/series-admin/audit_cutover.py apps/api/tests/test_cutover_audit.py
git commit -m "feat: add cutover audit export"
```

---

### Task 11: Resource Watch And Real Validation Checklist

**Files:**
- Create: `scripts/server-test/resource-watch.sh`
- Create: `apps/api/tests/test_server_test_scripts.py`
- Create: `docs/server-test-real-validation.md`
- Modify: `docs/server-test-series-admin-runbook.md`

- [ ] **Step 1: Write failing resource script test**

Create `apps/api/tests/test_server_test_scripts.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path


def test_resource_watch_emits_alerts_from_sample_files(tmp_path: Path) -> None:
    stats = tmp_path / "docker-stats.tsv"
    stats.write_text("koubei-test-api\\t95.3%\\t91.1%\\nkoubei-test-worker\\t12.0%\\t44.0%\\n", encoding="utf-8")
    disk = tmp_path / "df.txt"
    disk.write_text("Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/disk 100 88 12 88% /\\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "scripts/server-test/resource-watch.sh",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "RESOURCE_WATCH_ONCE": "1",
            "RESOURCE_WATCH_SAMPLE_FILE": str(stats),
            "RESOURCE_WATCH_DISK_SAMPLE_FILE": str(disk),
            "RESOURCE_WATCH_REQUIRED_CPU_HITS": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "[resource-alert] cpu" in result.stdout
    assert "[resource-alert] memory" in result.stdout
    assert "[resource-alert] disk" in result.stdout
```

- [ ] **Step 2: Run resource script test and verify failure**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_server_test_scripts.py -q
```

Expected: FAIL because `scripts/server-test/resource-watch.sh` does not exist.

- [ ] **Step 3: Implement resource watch script**

Create `scripts/server-test/resource-watch.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

CPU_THRESHOLD="${RESOURCE_WATCH_CPU_THRESHOLD:-90}"
MEMORY_THRESHOLD="${RESOURCE_WATCH_MEMORY_THRESHOLD:-90}"
DISK_THRESHOLD="${RESOURCE_WATCH_DISK_THRESHOLD:-85}"
INTERVAL_SECONDS="${RESOURCE_WATCH_INTERVAL_SECONDS:-60}"
REQUIRED_CPU_HITS="${RESOURCE_WATCH_REQUIRED_CPU_HITS:-10}"
CPU_HITS=0

percent_number() {
  printf '%s' "$1" | tr -d '%' | awk '{ printf "%.0f", $1 }'
}

stats_snapshot() {
  if [[ -n "${RESOURCE_WATCH_SAMPLE_FILE:-}" ]]; then
    cat "${RESOURCE_WATCH_SAMPLE_FILE}"
  else
    docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemPerc}}'
  fi
}

disk_snapshot() {
  if [[ -n "${RESOURCE_WATCH_DISK_SAMPLE_FILE:-}" ]]; then
    cat "${RESOURCE_WATCH_DISK_SAMPLE_FILE}"
  else
    df -P /
  fi
}

check_once() {
  local cpu_alert=0
  while IFS=$'\t' read -r name cpu memory; do
    [[ -z "${name}" ]] && continue
    local cpu_value memory_value
    cpu_value="$(percent_number "${cpu}")"
    memory_value="$(percent_number "${memory}")"
    if (( cpu_value >= CPU_THRESHOLD )); then
      cpu_alert=1
    fi
    if (( memory_value >= MEMORY_THRESHOLD )); then
      printf '[resource-alert] memory service=%s usage=%s threshold=%s%%\n' "${name}" "${memory}" "${MEMORY_THRESHOLD}"
    fi
  done < <(stats_snapshot)

  if (( cpu_alert == 1 )); then
    CPU_HITS=$((CPU_HITS + 1))
  else
    CPU_HITS=0
  fi
  if (( CPU_HITS >= REQUIRED_CPU_HITS )); then
    printf '[resource-alert] cpu sustained_hits=%s threshold=%s%% interval_seconds=%s\n' "${CPU_HITS}" "${CPU_THRESHOLD}" "${INTERVAL_SECONDS}"
  fi

  local disk_percent
  disk_percent="$(disk_snapshot | awk 'NR==2 { gsub("%", "", $5); print $5 }')"
  if [[ -n "${disk_percent}" ]] && (( disk_percent >= DISK_THRESHOLD )); then
    printf '[resource-alert] disk usage=%s%% threshold=%s%%\n' "${disk_percent}" "${DISK_THRESHOLD}"
  fi
}

while true; do
  check_once
  if [[ "${RESOURCE_WATCH_ONCE:-0}" == "1" ]]; then
    exit 0
  fi
  sleep "${INTERVAL_SECONDS}"
done
```

- [ ] **Step 4: Add real validation checklist**

Create `docs/server-test-real-validation.md`:

```markdown
# Server Test Real Validation

## Environment

- Host alias: `koubei-prod`
- Test root: `/opt/codexwork-test/vehicle-koubei-web-demo`
- Test port: `18080`
- Test compose project: `koubei-test`
- Test agents: `autohome-1..4`, `dongchedi-1..4`

## Required Pass Checks

- `curl -f http://127.0.0.1:18080/` succeeds.
- API health endpoint succeeds through the test stack.
- Production `confirmed_vehicle_series` imports into test.
- `/series-admin` can list, filter, add, edit, soft delete, restore, preview import, commit import, resolve conflicts, and export Excel.
- Alias lookup changes business lookup behavior.
- One single-vehicle task completes with real collectors and real LLM output.
- One comparison task with 2-3 vehicles completes with real collectors and real LLM output.
- Four concurrent users finish the pressure scenario.
- Resource watch logs terminal alerts for CPU, memory, or disk when thresholds are crossed.
- `audit_cutover.py` exports `summary`, `series_diff`, `raw_comment_diff`, and `task_result_diff` sheets.

## Pressure Scenario

- User 1: one single-vehicle query.
- Users 2-4: each runs a 2-3 vehicle comparison.
- Vehicles are selected from imported `confirmed_vehicle_series`.
- Record actual total duration, queue time, agent wait time, failed stages, retries, and bottleneck stages.

## Result Log

| Check | Started At | Finished At | Duration Minutes | Result | Notes |
| --- | --- | --- | ---: | --- | --- |
| Health | | | | | |
| Series sync | | | | | |
| Admin UI | | | | | |
| Alias lookup | | | | | |
| Single task | | | | | |
| Comparison task | | | | | |
| 4-user pressure | | | | | |
| Cutover audit export | | | | | |
```

- [ ] **Step 5: Link scripts from the runbook**

Append to `docs/server-test-series-admin-runbook.md`:

````markdown
## Resource Watch

Run during real testing:

```bash
scripts/server-test/resource-watch.sh
```

Alerts are printed to the terminal only. The script does not stop or downscale services.

## Cutover Audit

Before production cutover:

```bash
python scripts/series-admin/audit_cutover.py \
  --prod-url "$PROD_DATABASE_URL" \
  --test-url "$TEST_DATABASE_URL" \
  --output /opt/codexwork-test/audit/cutover-audit.xlsx
```

Use `docs/server-test-real-validation.md` as the real-environment result log.
````

- [ ] **Step 6: Run resource script tests and commit**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests/test_server_test_scripts.py -q
bash -n scripts/server-test/resource-watch.sh
```

Expected: both commands exit 0.

Commit:

```bash
git add scripts/server-test/resource-watch.sh apps/api/tests/test_server_test_scripts.py docs/server-test-real-validation.md docs/server-test-series-admin-runbook.md
git commit -m "feat: add server resource validation"
```

---

### Task 12: Final Verification

**Files:**
- All files changed above.

- [ ] **Step 1: Run backend tests**

Run:

```bash
/Users/xyc/Documents/codexwork/vehicle-koubei-web-demo/.venv/bin/pytest apps/api/tests -q
```

Expected: all API tests pass.

- [ ] **Step 2: Run frontend checks**

Run separately, not in parallel with build:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run verify:ui
npm --prefix apps/web run build
```

Expected: all commands exit 0.

- [ ] **Step 3: Run compose checks**

Run:

```bash
docker compose config --quiet
docker compose --env-file ops/test/.env.test.example -f ops/test/docker-compose.test.yml config --quiet
```

Expected: both commands exit 0.

- [ ] **Step 4: Run local browser smoke test**

Start local API and web if not already running, then verify:

```bash
curl -f http://127.0.0.1:3000/
curl -f http://127.0.0.1:3000/series-admin
curl -f http://127.0.0.1:3000/api/admin/series
```

Expected:

- `/` renders the workbench.
- `/series-admin` renders the hidden admin page.
- `/api/admin/series` returns JSON with `items` and `total`.

- [ ] **Step 5: Commit final fixes**

If any verification fixes were needed:

```bash
git add <changed-files>
git commit -m "fix: stabilize series admin rollout"
```

If no fixes were needed, do not create an empty commit.
