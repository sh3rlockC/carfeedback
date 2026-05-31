from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON as SAJSON
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy import inspect as inspect_database
from sqlalchemy.exc import IntegrityError


ACTIVE_COLLECTION_STATUSES = ("queued", "waiting_agent", "running", "retry_wait")


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def new_collection_run_id() -> str:
    return f"run_{utc_now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"


def new_task_id() -> str:
    return f"task_{utc_now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        if not value:
            return fallback
        return json.loads(value)
    return value


def _table_exists(engine: Any, table_name: str) -> bool:
    try:
        return table_name in inspect_database(engine).get_table_names()
    except Exception:
        return False


def _candidate_series_id(vehicle: dict[str, Any], platform: str) -> str:
    selected = dict(vehicle.get("selected_candidates") or {})
    candidate = dict(selected.get(platform) or {})
    if candidate.get("series_id"):
        return str(candidate["series_id"])
    if platform == "autohome":
        return str(vehicle.get("autohome_series_id") or "")
    return str(vehicle.get("dcd_series_id") or vehicle.get("dongchedi_series_id") or "")


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _artifact_type(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".jsonl"):
        return "jsonl"
    if lowered.endswith(".json"):
        return "json"
    if lowered.endswith(".xlsx"):
        return "excel"
    if lowered.endswith(".png"):
        return "image_png"
    return "artifact"


def _first_path_with_suffix(paths: list[str], suffix: str) -> str | None:
    for path in paths:
        if path.endswith(suffix):
            return path
    return None


def _payload_artifact_paths(payload: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("import_result", "export_result", "postprocess_result", "report_result"):
        result = payload.get(key)
        if not isinstance(result, dict):
            continue
        paths.extend(str(path) for path in result.get("artifact_paths") or [] if path)
    return paths


@dataclass(frozen=True)
class TaskVehicleRecord:
    id: int
    position: int
    query: str
    model_name: str
    autohome_series_id: str | None
    dcd_series_id: str | None
    enabled_platforms: list[str]
    status: str
    result_snapshot_json: dict[str, Any]


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    task_type: str
    display_name: str
    status: str
    current_stage: str
    collection_mode: str
    degraded: bool
    upgraded_to_full: bool
    vehicles: list[TaskVehicleRecord]


@dataclass(frozen=True)
class CollectionRunRecord:
    run_id: str
    platform: str
    query_key: str
    model_name: str
    series_id: str
    status: str
    mode: str
    shared_by_task_ids: list[str]
    agent_id: str | None = None
    failure_category: str | None = None
    output_path: str | None = None


class TaskStore:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, future=True, **_engine_kwargs(database_url))

    def load_task(self, task_id: str) -> TaskRecord:
        task_vehicle_columns = self._table_columns("task_vehicles")
        enabled_platforms_select = (
            "enabled_platforms"
            if "enabled_platforms" in task_vehicle_columns
            else "'[\"autohome\", \"dongchedi\"]' AS enabled_platforms"
        )
        with self.engine.begin() as conn:
            task_row = conn.execute(
                text(
                    """
                    SELECT task_id, task_type, display_name, status, current_stage, collection_mode,
                           degraded, upgraded_to_full
                    FROM tasks
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": task_id},
            ).mappings().first()
            if task_row is None:
                raise RuntimeError(f"task not found: {task_id}")

            vehicle_rows = conn.execute(
                text(
                    f"""
                    SELECT id, position, query, model_name, autohome_series_id, dcd_series_id,
                           {enabled_platforms_select}, status, result_snapshot_json
                    FROM task_vehicles
                    WHERE task_id = :task_id
                    ORDER BY position ASC, id ASC
                    """
                ),
                {"task_id": task_id},
            ).mappings().all()

        vehicles = [
            TaskVehicleRecord(
                id=int(row["id"]),
                position=int(row["position"]),
                query=str(row["query"]),
                model_name=str(row["model_name"]),
                autohome_series_id=str(row["autohome_series_id"]) if row["autohome_series_id"] else None,
                dcd_series_id=str(row["dcd_series_id"]) if row["dcd_series_id"] else None,
                enabled_platforms=[
                    str(platform)
                    for platform in _json_value(row["enabled_platforms"], ["autohome", "dongchedi"])
                    if str(platform) in {"autohome", "dongchedi"}
                ]
                or ["autohome", "dongchedi"],
                status=str(row["status"]),
                result_snapshot_json=dict(_json_value(row["result_snapshot_json"], {})),
            )
            for row in vehicle_rows
        ]
        return TaskRecord(
            task_id=str(task_row["task_id"]),
            task_type=str(task_row["task_type"]),
            display_name=str(task_row["display_name"]),
            status=str(task_row["status"]),
            current_stage=str(task_row["current_stage"]),
            collection_mode=str(task_row["collection_mode"]),
            degraded=bool(task_row["degraded"]),
            upgraded_to_full=bool(task_row["upgraded_to_full"]),
            vehicles=vehicles,
        )

    def ensure_comparison_child_task(
        self,
        vehicle: dict[str, Any],
        *,
        passphrase_version: str,
    ) -> str:
        query = str(vehicle.get("query") or vehicle.get("model_name") or "").strip()
        model_name = str(vehicle.get("model_name") or vehicle.get("query") or "").strip()
        autohome_series_id = _candidate_series_id(vehicle, "autohome")
        dcd_series_id = _candidate_series_id(vehicle, "dongchedi")
        enabled_platforms = [
            str(platform)
            for platform in vehicle.get("enabled_platforms", ["autohome", "dongchedi"])
            if str(platform) in {"autohome", "dongchedi"}
        ] or ["autohome", "dongchedi"]
        native_vehicle_id = _int_or_none(vehicle.get("native_task_vehicle_id"))
        legacy_vehicle_id = _int_or_none(vehicle.get("legacy_comparison_vehicle_id"))
        if legacy_vehicle_id is None and native_vehicle_id is None:
            legacy_vehicle_id = _int_or_none(vehicle.get("id"))
        with self.engine.begin() as conn:
            existing_task_id = str(vehicle.get("child_task_id") or vehicle.get("child_job_id") or "")
            if legacy_vehicle_id is not None and _table_exists(self.engine, "comparison_vehicles"):
                lock_clause = " FOR UPDATE" if conn.dialect.name == "postgresql" else ""
                row = conn.execute(
                    text(
                        f"""
                        SELECT child_job_id
                        FROM comparison_vehicles
                        WHERE id = :vehicle_id{lock_clause}
                        """
                    ),
                    {"vehicle_id": legacy_vehicle_id},
                ).mappings().first()
                db_child_task_id = str(row["child_job_id"]) if row is not None and row["child_job_id"] else ""
                if db_child_task_id:
                    existing_task_id = db_child_task_id
            if native_vehicle_id is not None:
                row = conn.execute(
                    text(
                        """
                        SELECT result_snapshot_json
                        FROM task_vehicles
                        WHERE id = :vehicle_id
                        """
                    ),
                    {"vehicle_id": native_vehicle_id},
                ).mappings().first()
                snapshot = _json_value(row["result_snapshot_json"], {}) if row is not None else {}
                if isinstance(snapshot, dict) and snapshot.get("child_task_id"):
                    existing_task_id = str(snapshot["child_task_id"])
            if existing_task_id and self._task_exists_conn(conn, existing_task_id):
                return existing_task_id

            now = utc_now_iso()
            task_id = new_task_id()
            conn.execute(
                text(
                    """
                    INSERT INTO tasks (
                        task_id, task_type, display_name, status, current_stage,
                        degraded, upgraded_to_full, view_token_hash, manage_token_hash,
                        manage_token_expires_at, eta_seconds, eta_reason, collection_mode,
                        created_at, updated_at
                    )
                    VALUES (
                        :task_id, 'single_vehicle', :display_name, 'queued', 'queued',
                        :degraded, :upgraded_to_full, :view_token_hash, :manage_token_hash,
                        :manage_token_expires_at, NULL, NULL, 'incremental',
                        :created_at, :updated_at
                    )
                    """
                ),
                {
                    "task_id": task_id,
                    "display_name": model_name or query or task_id,
                    "degraded": False,
                    "upgraded_to_full": False,
                    "view_token_hash": f"comparison-child-view:{task_id}",
                    "manage_token_hash": f"comparison-child-manage:{task_id}",
                    "manage_token_expires_at": "2030-01-01T00:00:00+00:00",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            insert_vehicle_statement = self._insert_task_vehicle_statement().bindparams(
                bindparam("result_snapshot_json", type_=SAJSON)
            )
            if "enabled_platforms" in self._table_columns("task_vehicles"):
                insert_vehicle_statement = insert_vehicle_statement.bindparams(bindparam("enabled_platforms", type_=SAJSON))
            conn.execute(
                insert_vehicle_statement,
                {
                    "task_id": task_id,
                    "position": int(vehicle.get("position") or 1),
                    "query": query,
                    "model_name": model_name or query,
                    "autohome_series_id": autohome_series_id or None,
                    "dcd_series_id": dcd_series_id or None,
                    "enabled_platforms": enabled_platforms,
                    "result_snapshot_json": {},
                    "created_at": now,
                    "updated_at": now,
                },
            )
            if legacy_vehicle_id is not None and _table_exists(self.engine, "comparison_vehicles"):
                conn.execute(
                    text(
                        """
                        UPDATE comparison_vehicles
                        SET child_job_id = :child_task_id,
                            status = 'running',
                            updated_at = :updated_at
                        WHERE id = :vehicle_id
                        """
                    ),
                    {"child_task_id": task_id, "vehicle_id": legacy_vehicle_id, "updated_at": now},
                )
            if native_vehicle_id is not None:
                self._update_task_vehicle_snapshot_conn(
                    conn,
                    vehicle_id=native_vehicle_id,
                    patch={"child_task_id": task_id, "source_job_id": task_id},
                    status="running",
                    updated_at=now,
                )
        return task_id

    def _task_exists(self, task_id: str) -> bool:
        with self.engine.begin() as conn:
            row = self._task_exists_conn(conn, task_id)
        return row is not None

    def _task_exists_conn(self, conn: Any, task_id: str) -> Any | None:
        return conn.execute(
            text("SELECT 1 FROM tasks WHERE task_id = :task_id LIMIT 1"),
            {"task_id": task_id},
        ).first()

    def _table_columns(self, table_name: str) -> set[str]:
        try:
            return {column["name"] for column in inspect_database(self.engine).get_columns(table_name)}
        except Exception:
            return set()

    def _insert_task_vehicle_statement(self):
        if "enabled_platforms" in self._table_columns("task_vehicles"):
            return text(
                """
                INSERT INTO task_vehicles (
                    task_id, position, query, model_name, autohome_series_id, dcd_series_id,
                    enabled_platforms, status, result_snapshot_json, created_at, updated_at
                )
                VALUES (
                    :task_id, :position, :query, :model_name, :autohome_series_id, :dcd_series_id,
                    :enabled_platforms, 'queued', :result_snapshot_json, :created_at, :updated_at
                )
                """
            )
        return text(
            """
            INSERT INTO task_vehicles (
                task_id, position, query, model_name, autohome_series_id, dcd_series_id,
                status, result_snapshot_json, created_at, updated_at
            )
            VALUES (
                :task_id, :position, :query, :model_name, :autohome_series_id, :dcd_series_id,
                'queued', :result_snapshot_json, :created_at, :updated_at
            )
                """
            )

    def create_task(
        self,
        *,
        task_type: str,
        display_name: str,
        vehicles: list[dict[str, Any]],
        collection_mode: str = "incremental",
    ) -> TaskRecord:
        if self.engine.dialect.name != "sqlite":
            raise RuntimeError("TaskStore.create_task is only available for SQLite test stores")
        self._ensure_sqlite_schema()
        now = utc_now_iso()
        task_id = new_task_id()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO tasks (
                        task_id, task_type, display_name, status, current_stage,
                        degraded, upgraded_to_full, view_token_hash, manage_token_hash,
                        manage_token_expires_at, collection_mode, created_at, updated_at
                    )
                    VALUES (
                        :task_id, :task_type, :display_name, 'queued', 'queued',
                        :degraded, :upgraded_to_full, :view_token_hash, :manage_token_hash,
                        :manage_token_expires_at, :collection_mode, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "display_name": display_name,
                    "degraded": False,
                    "upgraded_to_full": False,
                    "view_token_hash": f"task-view:{task_id}",
                    "manage_token_hash": f"task-manage:{task_id}",
                    "manage_token_expires_at": "2030-01-01T00:00:00+00:00",
                    "collection_mode": collection_mode,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            insert_vehicle_statement = self._insert_task_vehicle_statement().bindparams(
                bindparam("result_snapshot_json", type_=SAJSON)
            )
            if "enabled_platforms" in self._table_columns("task_vehicles"):
                insert_vehicle_statement = insert_vehicle_statement.bindparams(bindparam("enabled_platforms", type_=SAJSON))
            for index, vehicle in enumerate(vehicles, start=1):
                enabled_platforms = [
                    str(platform)
                    for platform in vehicle.get("enabled_platforms", ["autohome", "dongchedi"])
                    if str(platform) in {"autohome", "dongchedi"}
                ] or ["autohome", "dongchedi"]
                conn.execute(
                    insert_vehicle_statement,
                    {
                        "task_id": task_id,
                        "position": index,
                        "query": str(vehicle.get("query") or vehicle.get("model_name") or ""),
                        "model_name": str(vehicle.get("model_name") or vehicle.get("query") or ""),
                        "autohome_series_id": _candidate_series_id(vehicle, "autohome") or None,
                        "dcd_series_id": _candidate_series_id(vehicle, "dongchedi") or None,
                        "enabled_platforms": enabled_platforms,
                        "result_snapshot_json": {},
                        "created_at": now,
                        "updated_at": now,
                    },
                )
        return self.load_task(task_id)

    def _ensure_sqlite_schema(self) -> None:
        if self.engine.dialect.name != "sqlite" or _table_exists(self.engine, "tasks"):
            return
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        task_id TEXT PRIMARY KEY,
                        task_type TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        status TEXT NOT NULL,
                        current_stage TEXT NOT NULL,
                        degraded INTEGER NOT NULL DEFAULT 0,
                        upgraded_to_full INTEGER NOT NULL DEFAULT 0,
                        view_token_hash TEXT NOT NULL,
                        manage_token_hash TEXT NOT NULL,
                        manage_token_expires_at TEXT NOT NULL,
                        view_token_revoked_at TEXT,
                        manage_token_revoked_at TEXT,
                        eta_seconds INTEGER,
                        eta_reason TEXT,
                        collection_mode TEXT NOT NULL DEFAULT 'incremental',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS task_vehicles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        query TEXT NOT NULL,
                        model_name TEXT NOT NULL,
                        autohome_series_id TEXT,
                        dcd_series_id TEXT,
                        enabled_platforms JSON NOT NULL DEFAULT '["autohome", "dongchedi"]',
                        status TEXT NOT NULL DEFAULT 'queued',
                        result_snapshot_json JSON NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS collection_runs (
                        run_id TEXT PRIMARY KEY,
                        platform TEXT NOT NULL,
                        query_key TEXT NOT NULL,
                        model_name TEXT NOT NULL,
                        series_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        shared_by_task_ids JSON NOT NULL DEFAULT '[]',
                        agent_id TEXT,
                        failure_category TEXT,
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        resume_cursor JSON NOT NULL DEFAULT '{}',
                        output_path TEXT,
                        started_at TEXT,
                        finished_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_collection_run_active_identity
                    ON collection_runs (platform, query_key, series_id)
                    WHERE status IN ('queued', 'waiting_agent', 'running', 'retry_wait')
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_collection_run_running_agent
                    ON collection_runs (agent_id)
                    WHERE status = 'running' AND agent_id IS NOT NULL
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS collection_run_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        task_vehicle_id INTEGER,
                        created_at TEXT NOT NULL,
                        UNIQUE (run_id, task_id),
                        FOREIGN KEY (run_id) REFERENCES collection_runs(run_id) ON DELETE CASCADE,
                        FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                        FOREIGN KEY (task_vehicle_id) REFERENCES task_vehicles(id) ON DELETE CASCADE
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS task_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json JSON NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS task_artifacts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT NOT NULL,
                        artifact_type TEXT NOT NULL,
                        path TEXT NOT NULL,
                        downloadable INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS collector_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        platform TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json JSON NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES collection_runs(run_id) ON DELETE CASCADE
                    )
                    """
                )
            )

    def mark_task_stage(self, task_id: str, stage: str, status: str) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE tasks
                    SET current_stage = :stage,
                        status = :status,
                        updated_at = :updated_at
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": task_id, "stage": stage, "status": status, "updated_at": now},
            )
            self._sync_single_task_vehicle_status_conn(conn, task_id=task_id, status=status, updated_at=now)

    def append_task_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        statement = text(
            """
            INSERT INTO task_events (task_id, event_type, payload_json, created_at)
            VALUES (:task_id, :event_type, :payload_json, :created_at)
            """
        ).bindparams(bindparam("payload_json", type_=SAJSON))
        with self.engine.begin() as conn:
            conn.execute(
                statement,
                {
                    "task_id": task_id,
                    "event_type": event_type,
                    "payload_json": payload,
                    "created_at": utc_now_iso(),
                },
            )

    def _update_task_vehicle_snapshot_conn(
        self,
        conn: Any,
        *,
        vehicle_id: int,
        patch: dict[str, Any],
        status: str | None,
        updated_at: str,
    ) -> None:
        row = conn.execute(
            text(
                """
                SELECT result_snapshot_json
                FROM task_vehicles
                WHERE id = :vehicle_id
                """
            ),
            {"vehicle_id": vehicle_id},
        ).mappings().first()
        if row is None:
            return
        snapshot = _json_value(row["result_snapshot_json"], {})
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        snapshot.update({key: value for key, value in patch.items() if value is not None})
        status_sql = "status = :status," if status is not None else ""
        params = {
            "vehicle_id": vehicle_id,
            "result_snapshot_json": snapshot,
            "updated_at": updated_at,
        }
        if status is not None:
            params["status"] = status
        conn.execute(
            text(
                f"""
                UPDATE task_vehicles
                SET {status_sql}
                    result_snapshot_json = :result_snapshot_json,
                    updated_at = :updated_at
                WHERE id = :vehicle_id
                """
            ).bindparams(bindparam("result_snapshot_json", type_=SAJSON)),
            params,
        )

    def mark_task_vehicle_status(
        self,
        vehicle_id: int,
        *,
        status: str,
        source_job_id: str | None = None,
        child_task_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            self._update_task_vehicle_snapshot_conn(
                conn,
                vehicle_id=vehicle_id,
                patch={
                    "source_job_id": source_job_id,
                    "child_task_id": child_task_id,
                    "error_code": error_code,
                    "error_message": error_message,
                },
                status=status,
                updated_at=now,
            )

    def publish_comparison_result(self, task_id: str, payload: dict[str, Any], *, status: str) -> None:
        now = utc_now_iso()
        degraded = status == "completed_degraded" or bool(payload.get("degraded")) or bool(payload.get("excluded"))
        task_status = "completed_degraded" if degraded else "completed"
        if status == "failed":
            self.mark_task_failed(task_id, payload)
            return
        available_ids = {
            _int_or_none(vehicle.get("native_task_vehicle_id") or vehicle.get("vehicle_id"))
            for vehicle in payload.get("vehicles", [])
            if isinstance(vehicle, dict)
        }
        excluded_ids = {
            _int_or_none(vehicle.get("native_task_vehicle_id") or vehicle.get("vehicle_id"))
            for vehicle in payload.get("excluded", [])
            if isinstance(vehicle, dict)
        }
        available_ids.discard(None)
        excluded_ids.discard(None)
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        artifact_paths = [str(path) for path in report.get("artifact_paths") or [] if path]
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE tasks
                    SET current_stage = :current_stage,
                        status = :status,
                        degraded = :degraded,
                        completed_at = COALESCE(completed_at, :completed_at),
                        updated_at = :updated_at
                    WHERE task_id = :task_id
                    """
                ),
                {
                    "task_id": task_id,
                    "current_stage": task_status,
                    "status": task_status,
                    "degraded": degraded,
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            if available_ids:
                conn.execute(
                    text(
                        """
                        UPDATE task_vehicles
                        SET status = :status,
                            updated_at = :updated_at
                        WHERE id IN :vehicle_ids
                        """
                    ).bindparams(bindparam("vehicle_ids", expanding=True)),
                    {"status": task_status, "updated_at": now, "vehicle_ids": sorted(available_ids)},
                )
            if excluded_ids:
                conn.execute(
                    text(
                        """
                        UPDATE task_vehicles
                        SET status = 'excluded',
                            updated_at = :updated_at
                        WHERE id IN :vehicle_ids
                        """
                    ).bindparams(bindparam("vehicle_ids", expanding=True)),
                    {"updated_at": now, "vehicle_ids": sorted(excluded_ids)},
                )
            for path in artifact_paths:
                conn.execute(
                    text(
                        """
                        INSERT INTO task_artifacts (task_id, artifact_type, path, downloadable, created_at)
                        VALUES (:task_id, :artifact_type, :path, :downloadable, :created_at)
                        """
                    ),
                    {
                        "task_id": task_id,
                        "artifact_type": _artifact_type(path),
                        "path": path,
                        "downloadable": True,
                        "created_at": now,
                    },
                )
        self.append_task_event(task_id, "comparison_published", payload)

    def is_retry_paused(self, task_id: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT status, current_stage FROM tasks WHERE task_id = :task_id"),
                {"task_id": task_id},
            ).mappings().first()
        return bool(row and (row["status"] == "retry_paused" or row["current_stage"] == "retry_paused"))

    def publish_degraded_result(self, task_id: str, payload: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE tasks
                    SET current_stage = 'completed_degraded',
                        status = 'completed_degraded',
                        degraded = :degraded,
                        upgraded_to_full = :upgraded_to_full,
                        completed_at = COALESCE(completed_at, :completed_at),
                        updated_at = :updated_at
                    WHERE task_id = :task_id
                    """
                ),
                {
                    "task_id": task_id,
                    "degraded": True,
                    "upgraded_to_full": False,
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            self._sync_single_task_vehicle_status_conn(conn, task_id=task_id, status="completed_degraded", updated_at=now)
            self._persist_comparison_snapshot(conn, task_id=task_id, payload=payload, updated_at=now)
        self.append_task_event(task_id, "degraded_published", payload)

    def publish_full_result(self, task_id: str, payload: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT degraded, upgraded_to_full FROM tasks WHERE task_id = :task_id"),
                {"task_id": task_id},
            ).mappings().first()
            if row is None:
                raise RuntimeError(f"task not found: {task_id}")
            was_degraded = bool(row["degraded"])
            already_upgraded = bool(row["upgraded_to_full"])
            conn.execute(
                text(
                    """
                    UPDATE tasks
                    SET current_stage = 'completed',
                        status = 'completed',
                        degraded = :degraded,
                        upgraded_to_full = :upgraded_to_full,
                        completed_at = COALESCE(completed_at, :completed_at),
                        updated_at = :updated_at
                    WHERE task_id = :task_id
                    """
                ),
                {
                    "task_id": task_id,
                    "degraded": was_degraded,
                    "upgraded_to_full": was_degraded or already_upgraded,
                    "completed_at": now,
                    "updated_at": now,
                },
            )
            self._sync_single_task_vehicle_status_conn(conn, task_id=task_id, status="completed", updated_at=now)
            self._persist_comparison_snapshot(conn, task_id=task_id, payload=payload, updated_at=now)
        if was_degraded and not already_upgraded:
            self.append_task_event(task_id, "upgraded_to_full", payload)
        self.append_task_event(task_id, "full_result_published", payload)

    def _persist_comparison_snapshot(self, conn: Any, *, task_id: str, payload: dict[str, Any], updated_at: str) -> None:
        paths = _payload_artifact_paths(payload)
        final_report_path = _first_path_with_suffix(paths, "final_report.json")
        analysis_facts_path = _first_path_with_suffix(paths, "analysis_facts.jsonl")
        if not final_report_path or not analysis_facts_path:
            return

        if _table_exists(self.engine, "task_artifacts"):
            for path in paths:
                conn.execute(
                    text(
                        """
                        INSERT INTO task_artifacts (task_id, artifact_type, path, downloadable, created_at)
                        VALUES (:task_id, :artifact_type, :path, :downloadable, :created_at)
                        """
                    ),
                    {
                        "task_id": task_id,
                        "artifact_type": _artifact_type(path),
                        "path": path,
                        "downloadable": True,
                        "created_at": updated_at,
                    },
                )

        task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
        vehicles = task.get("vehicles") if isinstance(task, dict) else []
        vehicle = vehicles[0] if isinstance(vehicles, list) and vehicles and isinstance(vehicles[0], dict) else {}
        row = conn.execute(
            text(
                """
                SELECT id, result_snapshot_json, model_name, query
                FROM task_vehicles
                WHERE task_id = :task_id
                ORDER BY position ASC, id ASC
                LIMIT 1
                """
            ),
            {"task_id": task_id},
        ).mappings().first()
        if row is None:
            return
        existing = _json_value(row["result_snapshot_json"], {})
        existing = existing if isinstance(existing, dict) else {}
        snapshot = {
            "model_name": str(vehicle.get("model_name") or row["model_name"] or vehicle.get("query") or row["query"] or task_id),
            "source_job_id": task_id,
            "final_report_path": final_report_path,
            "analysis_facts_path": analysis_facts_path,
        }
        llm_metrics_path = _first_path_with_suffix(paths, "llm_metrics.json")
        if llm_metrics_path:
            snapshot["llm_metrics_path"] = llm_metrics_path
        existing["comparison_snapshot"] = snapshot
        conn.execute(
            text(
                """
                UPDATE task_vehicles
                SET result_snapshot_json = :result_snapshot_json,
                    updated_at = :updated_at
                WHERE id = :vehicle_id
                """
            ).bindparams(bindparam("result_snapshot_json", type_=SAJSON)),
            {
                "result_snapshot_json": existing,
                "updated_at": updated_at,
                "vehicle_id": int(row["id"]),
            },
        )

    def schedule_retry(self, task_id: str, payload: dict[str, Any]) -> None:
        self.append_task_event(task_id, "retry_scheduled", payload)

    def mark_task_failed(self, task_id: str, payload: dict[str, Any]) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE tasks
                    SET current_stage = 'failed',
                        status = 'failed',
                        completed_at = COALESCE(completed_at, :completed_at),
                        updated_at = :updated_at
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": task_id, "completed_at": now, "updated_at": now},
            )
            self._sync_single_task_vehicle_status_conn(conn, task_id=task_id, status="failed", updated_at=now)
        self.append_task_event(task_id, "task_failed", payload)

    def cancel_task_and_detach_runs(self, task_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        detached_runs: list[dict[str, Any]] = []
        cancelled_run_ids: list[str] = []
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE tasks
                    SET current_stage = 'cancelled',
                        status = 'cancelled',
                        completed_at = COALESCE(completed_at, :completed_at),
                        updated_at = :updated_at
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": task_id, "completed_at": now, "updated_at": now},
            )
            self._sync_single_task_vehicle_status_conn(conn, task_id=task_id, status="cancelled", updated_at=now)
            run_rows = conn.execute(
                text(
                    """
                    SELECT cr.run_id, cr.shared_by_task_ids
                    FROM collection_runs cr
                    JOIN collection_run_tasks crt ON crt.run_id = cr.run_id
                    WHERE crt.task_id = :task_id
                      AND cr.status IN ('queued', 'waiting_agent', 'running', 'retry_wait')
                    ORDER BY cr.created_at ASC, cr.run_id ASC
                    """
                ),
                {"task_id": task_id},
            ).mappings().all()

            for row in run_rows:
                run_id = str(row["run_id"])
                remaining_task_ids = [
                    existing_task_id
                    for existing_task_id in self._shared_task_ids(row)
                    if existing_task_id != task_id
                ]
                self._update_shared_task_ids(conn, run_id, remaining_task_ids)
                conn.execute(
                    text(
                        """
                        DELETE FROM collection_run_tasks
                        WHERE run_id = :run_id AND task_id = :task_id
                        """
                    ),
                    {"run_id": run_id, "task_id": task_id},
                )
                cancelled = not remaining_task_ids
                if cancelled:
                    conn.execute(
                        text(
                            """
                            UPDATE collection_runs
                            SET status = 'cancelled',
                                finished_at = COALESCE(finished_at, :finished_at),
                                updated_at = :updated_at
                            WHERE run_id = :run_id
                            """
                        ),
                        {"run_id": run_id, "finished_at": now, "updated_at": now},
                    )
                    cancelled_run_ids.append(run_id)
                detached_runs.append(
                    {
                        "run_id": run_id,
                        "remaining_task_ids": remaining_task_ids,
                        "cancelled": cancelled,
                    }
                )

        event_payload = {"detached_runs": detached_runs, "cancelled_run_ids": cancelled_run_ids}
        self.append_task_event(task_id, "task_cancelled", event_payload)
        return event_payload

    def _sync_single_task_vehicle_status_conn(self, conn: Any, *, task_id: str, status: str, updated_at: str) -> None:
        conn.execute(
            text(
                """
                UPDATE task_vehicles
                SET status = :status,
                    updated_at = :updated_at
                WHERE task_id = :task_id
                  AND EXISTS (
                      SELECT 1
                      FROM tasks
                      WHERE task_id = :task_id
                        AND task_type <> 'comparison'
                  )
                """
            ),
            {"task_id": task_id, "status": status, "updated_at": updated_at},
        )

    def create_or_join_collection_run(
        self,
        *,
        platform: str,
        query_key: str,
        model_name: str,
        series_id: str,
        mode: str,
        task_id: str,
    ) -> CollectionRunRecord:
        last_error: IntegrityError | None = None
        for _attempt in range(2):
            try:
                return self._create_or_join_collection_run_once(
                    platform=platform,
                    query_key=query_key,
                    model_name=model_name,
                    series_id=series_id,
                    mode=mode,
                    task_id=task_id,
                )
            except IntegrityError as exc:
                if not self._is_active_identity_integrity_error(exc):
                    raise
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("failed to create or join collection run")

    def _create_or_join_collection_run_once(
        self,
        *,
        platform: str,
        query_key: str,
        model_name: str,
        series_id: str,
        mode: str,
        task_id: str,
    ) -> CollectionRunRecord:
        with self.engine.begin() as conn:
            run_row = self._find_active_run_for_update(
                conn,
                platform=platform,
                query_key=query_key,
                series_id=series_id,
            )
            if run_row is None:
                run_id = self._insert_collection_run(
                    conn,
                    platform=platform,
                    query_key=query_key,
                    model_name=model_name,
                    series_id=series_id,
                    mode=mode,
                    task_id=task_id,
                )
            else:
                run_id = str(run_row["run_id"])
                shared_by_task_ids = self._shared_task_ids(run_row)
                if task_id not in shared_by_task_ids:
                    shared_by_task_ids.append(task_id)
                    self._update_shared_task_ids(conn, run_id, shared_by_task_ids)

            self._insert_run_task_link(conn, run_id=run_id, task_id=task_id)
            return self._load_collection_run(conn, run_id)

    def attach_task_to_run(self, task_id: str, run_id: str) -> CollectionRunRecord:
        with self.engine.begin() as conn:
            run_row = self._get_collection_run_row_for_update(conn, run_id)
            if run_row is None:
                raise RuntimeError(f"collection run not found: {run_id}")
            shared_by_task_ids = self._shared_task_ids(run_row)
            if task_id not in shared_by_task_ids:
                shared_by_task_ids.append(task_id)
                self._update_shared_task_ids(conn, run_id, shared_by_task_ids)
            self._insert_run_task_link(conn, run_id=run_id, task_id=task_id)
            return self._load_collection_run(conn, run_id)

    def load_collection_run(self, run_id: str) -> CollectionRunRecord:
        with self.engine.begin() as conn:
            return self._load_collection_run(conn, run_id)

    def load_collection_runs(self, run_ids: list[str]) -> list[CollectionRunRecord]:
        return [self.load_collection_run(run_id) for run_id in run_ids]

    def running_agent_ids_by_platform(self) -> dict[str, set[str]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT platform, agent_id
                    FROM collection_runs
                    WHERE status = 'running'
                      AND agent_id IS NOT NULL
                    """
                )
            ).mappings().all()
        busy: dict[str, set[str]] = {}
        for row in rows:
            busy.setdefault(str(row["platform"]), set()).add(str(row["agent_id"]))
        return busy

    def mark_collection_run_waiting_agent(self, run_id: str) -> CollectionRunRecord:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            row = self._get_collection_run_row_for_update(conn, run_id)
            if row is None:
                raise RuntimeError(f"collection run not found: {run_id}")
            if str(row["status"]) in {"queued", "retry_wait"}:
                conn.execute(
                    text(
                        """
                        UPDATE collection_runs
                        SET status = 'waiting_agent',
                            agent_id = NULL,
                            updated_at = :updated_at
                        WHERE run_id = :run_id
                        """
                    ),
                    {"run_id": run_id, "updated_at": now},
                )
            return self._load_collection_run(conn, run_id)

    def claim_collection_run_with_agent(self, run_id: str, agent_id: str) -> CollectionRunRecord | None:
        now = utc_now_iso()
        try:
            with self.engine.begin() as conn:
                row = self._get_collection_run_row_for_update(conn, run_id)
                if row is None:
                    raise RuntimeError(f"collection run not found: {run_id}")
                result = conn.execute(
                    text(
                        """
                        UPDATE collection_runs
                        SET status = 'running',
                            agent_id = :agent_id,
                            failure_category = NULL,
                            started_at = COALESCE(started_at, :started_at),
                            finished_at = NULL,
                            updated_at = :updated_at
                        WHERE run_id = :run_id
                          AND status IN ('queued', 'waiting_agent', 'retry_wait')
                        """
                    ),
                    {
                        "run_id": run_id,
                        "agent_id": agent_id,
                        "started_at": now,
                        "updated_at": now,
                    },
                )
                if result.rowcount != 1:
                    return None
                return self._load_collection_run(conn, run_id)
        except IntegrityError as exc:
            if self._is_running_agent_integrity_error(exc):
                return None
            raise

    def start_collection_run(self, run_id: str, *, agent_id: str | None = None) -> CollectionRunRecord:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            row = self._get_collection_run_row_for_update(conn, run_id)
            if row is None:
                raise RuntimeError(f"collection run not found: {run_id}")
            if str(row["status"]) in {"queued", "waiting_agent", "retry_wait"}:
                conn.execute(
                    text(
                        """
                        UPDATE collection_runs
                        SET status = 'running',
                            agent_id = COALESCE(:agent_id, agent_id),
                            failure_category = NULL,
                            started_at = COALESCE(started_at, :started_at),
                            finished_at = NULL,
                            updated_at = :updated_at
                        WHERE run_id = :run_id
                        """
                    ),
                    {
                        "run_id": run_id,
                        "agent_id": agent_id,
                        "started_at": now,
                        "updated_at": now,
                    },
                )
            return self._load_collection_run(conn, run_id)

    def complete_collection_run(
        self,
        run_id: str,
        *,
        output_path: str,
        resume_cursor: dict[str, Any] | None = None,
    ) -> CollectionRunRecord:
        now = utc_now_iso()
        statement = text(
            """
            UPDATE collection_runs
            SET status = 'succeeded',
                output_path = :output_path,
                failure_category = NULL,
                resume_cursor = :resume_cursor,
                finished_at = COALESCE(finished_at, :finished_at),
                updated_at = :updated_at
            WHERE run_id = :run_id
            """
        ).bindparams(bindparam("resume_cursor", type_=SAJSON))
        with self.engine.begin() as conn:
            if self._get_collection_run_row_for_update(conn, run_id) is None:
                raise RuntimeError(f"collection run not found: {run_id}")
            conn.execute(
                statement,
                {
                    "run_id": run_id,
                    "output_path": output_path,
                    "resume_cursor": resume_cursor or {},
                    "finished_at": now,
                    "updated_at": now,
                },
            )
            return self._load_collection_run(conn, run_id)

    def fail_collection_run(
        self,
        run_id: str,
        *,
        failure_category: str,
        resume_cursor: dict[str, Any] | None = None,
    ) -> CollectionRunRecord:
        now = utc_now_iso()
        statement = text(
            """
            UPDATE collection_runs
            SET status = 'failed',
                failure_category = :failure_category,
                resume_cursor = :resume_cursor,
                finished_at = COALESCE(finished_at, :finished_at),
                updated_at = :updated_at
            WHERE run_id = :run_id
            """
        ).bindparams(bindparam("resume_cursor", type_=SAJSON))
        with self.engine.begin() as conn:
            if self._get_collection_run_row_for_update(conn, run_id) is None:
                raise RuntimeError(f"collection run not found: {run_id}")
            conn.execute(
                statement,
                {
                    "run_id": run_id,
                    "failure_category": failure_category,
                    "resume_cursor": resume_cursor or {},
                    "finished_at": now,
                    "updated_at": now,
                },
            )
            return self._load_collection_run(conn, run_id)

    def record_collector_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        statement = text(
            """
            INSERT INTO collector_events (run_id, platform, event_type, payload_json, created_at)
            VALUES (:run_id, :platform, :event_type, :payload_json, :created_at)
            """
        ).bindparams(bindparam("payload_json", type_=SAJSON))
        with self.engine.begin() as conn:
            run_row = self._get_collection_run_row(conn, run_id)
            if run_row is None:
                raise RuntimeError(f"collection run not found: {run_id}")
            conn.execute(
                statement,
                {
                    "run_id": run_id,
                    "platform": str(run_row["platform"]),
                    "event_type": event_type,
                    "payload_json": payload,
                    "created_at": utc_now_iso(),
                },
            )

    def _find_active_run_for_update(self, conn: Any, *, platform: str, query_key: str, series_id: str) -> Any | None:
        lock_clause = " FOR UPDATE" if conn.dialect.name == "postgresql" else ""
        query = text(
            f"""
            SELECT run_id, platform, query_key, model_name, series_id, status, mode,
                   shared_by_task_ids, failure_category, output_path
            FROM collection_runs
            WHERE platform = :platform
              AND query_key = :query_key
              AND series_id = :series_id
              AND status IN ('queued', 'waiting_agent', 'running', 'retry_wait')
            ORDER BY created_at ASC
            LIMIT 1{lock_clause}
            """
        )
        return conn.execute(
            query,
            {"platform": platform, "query_key": query_key, "series_id": series_id},
        ).mappings().first()

    def _insert_collection_run(
        self,
        conn: Any,
        *,
        platform: str,
        query_key: str,
        model_name: str,
        series_id: str,
        mode: str,
        task_id: str,
    ) -> str:
        now = utc_now_iso()
        run_id = new_collection_run_id()
        insert_statement = text(
            """
            INSERT INTO collection_runs (
                run_id, platform, query_key, model_name, series_id, status, mode,
                shared_by_task_ids, retry_count, resume_cursor, created_at, updated_at
            )
            VALUES (
                :run_id, :platform, :query_key, :model_name, :series_id, 'queued', :mode,
                :shared_by_task_ids, 0, :resume_cursor, :created_at, :updated_at
            )
            """
        ).bindparams(
            bindparam("shared_by_task_ids", type_=SAJSON),
            bindparam("resume_cursor", type_=SAJSON),
        )
        conn.execute(
            insert_statement,
            {
                "run_id": run_id,
                "platform": platform,
                "query_key": query_key,
                "model_name": model_name,
                "series_id": series_id,
                "mode": mode,
                "shared_by_task_ids": [task_id],
                "resume_cursor": {},
                "created_at": now,
                "updated_at": now,
            },
        )
        return run_id

    def _get_collection_run_row(self, conn: Any, run_id: str) -> Any | None:
        return conn.execute(
            text(
                """
                SELECT run_id, platform, query_key, model_name, series_id, status, mode,
                       shared_by_task_ids, agent_id, failure_category, output_path
                FROM collection_runs
                WHERE run_id = :run_id
                """
            ),
            {"run_id": run_id},
        ).mappings().first()

    def _get_collection_run_row_for_update(self, conn: Any, run_id: str) -> Any | None:
        lock_clause = " FOR UPDATE" if conn.dialect.name == "postgresql" else ""
        return conn.execute(
            text(
                f"""
                SELECT run_id, platform, query_key, model_name, series_id, status, mode,
                       shared_by_task_ids, agent_id, failure_category, output_path
                FROM collection_runs
                WHERE run_id = :run_id{lock_clause}
                """
            ),
            {"run_id": run_id},
        ).mappings().first()

    def _load_collection_run(self, conn: Any, run_id: str) -> CollectionRunRecord:
        row = self._get_collection_run_row(conn, run_id)
        if row is None:
            raise RuntimeError(f"collection run not found: {run_id}")
        return CollectionRunRecord(
            run_id=str(row["run_id"]),
            platform=str(row["platform"]),
            query_key=str(row["query_key"]),
            model_name=str(row["model_name"]),
            series_id=str(row["series_id"]),
            status=str(row["status"]),
            mode=str(row["mode"]),
            shared_by_task_ids=self._shared_task_ids(row),
            agent_id=str(row["agent_id"]) if row["agent_id"] else None,
            failure_category=str(row["failure_category"]) if row["failure_category"] else None,
            output_path=str(row["output_path"]) if row["output_path"] else None,
        )

    def _shared_task_ids(self, row: Any) -> list[str]:
        values = _json_value(row["shared_by_task_ids"], [])
        return [str(value) for value in values]

    def _update_shared_task_ids(self, conn: Any, run_id: str, shared_by_task_ids: list[str]) -> None:
        statement = text(
            """
            UPDATE collection_runs
            SET shared_by_task_ids = :shared_by_task_ids,
                updated_at = :updated_at
            WHERE run_id = :run_id
            """
        ).bindparams(bindparam("shared_by_task_ids", type_=SAJSON))
        conn.execute(
            statement,
            {
                "run_id": run_id,
                "shared_by_task_ids": shared_by_task_ids,
                "updated_at": utc_now_iso(),
            },
        )

    def _insert_run_task_link(self, conn: Any, *, run_id: str, task_id: str) -> None:
        now = utc_now_iso()
        if conn.dialect.name == "postgresql":
            statement = text(
                """
                INSERT INTO collection_run_tasks (run_id, task_id, task_vehicle_id, created_at)
                VALUES (:run_id, :task_id, NULL, :created_at)
                ON CONFLICT (run_id, task_id) DO NOTHING
                """
            )
            conn.execute(statement, {"run_id": run_id, "task_id": task_id, "created_at": now})
            return
        statement = text(
            """
            INSERT INTO collection_run_tasks (run_id, task_id, task_vehicle_id, created_at)
            VALUES (:run_id, :task_id, NULL, :created_at)
            """
        )
        try:
            conn.execute(statement, {"run_id": run_id, "task_id": task_id, "created_at": now})
        except IntegrityError as exc:
            if self._is_duplicate_run_task_integrity_error(exc):
                return
            raise

    def _is_active_identity_integrity_error(self, exc: IntegrityError) -> bool:
        message = f"{exc}".lower()
        return (
            "uq_collection_run_active_identity" in message
            or "collection_runs.platform" in message
            or "active identity conflict" in message
        )

    def _is_duplicate_run_task_integrity_error(self, exc: IntegrityError) -> bool:
        message = f"{exc}".lower()
        return (
            "uq_collection_run_task" in message
            or "collection_run_tasks.run_id" in message
            or "collection_run_tasks_task_id" in message
            or "unique constraint failed: collection_run_tasks.run_id, collection_run_tasks.task_id" in message
            or "duplicate key value violates unique constraint" in message
        )

    def _is_running_agent_integrity_error(self, exc: IntegrityError) -> bool:
        message = f"{exc}".lower()
        return (
            "uq_collection_run_running_agent" in message
            or "collection_runs.agent_id" in message
            or "unique constraint failed: collection_runs.agent_id" in message
        )
