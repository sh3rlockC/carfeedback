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
    return {}


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


@dataclass(frozen=True)
class TaskVehicleRecord:
    id: int
    position: int
    query: str
    model_name: str
    autohome_series_id: str | None
    dcd_series_id: str | None
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
    failure_category: str | None = None
    output_path: str | None = None


class TaskStore:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, future=True, **_engine_kwargs(database_url))

    def load_task(self, task_id: str) -> TaskRecord:
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
                    """
                    SELECT id, position, query, model_name, autohome_series_id, dcd_series_id,
                           status, result_snapshot_json
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
        existing_task_id = str(vehicle.get("child_task_id") or vehicle.get("child_job_id") or "")
        if existing_task_id and self._task_exists(existing_task_id):
            return existing_task_id

        now = utc_now_iso()
        task_id = new_task_id()
        query = str(vehicle.get("query") or vehicle.get("model_name") or "").strip()
        model_name = str(vehicle.get("model_name") or vehicle.get("query") or "").strip()
        autohome_series_id = _candidate_series_id(vehicle, "autohome")
        dcd_series_id = _candidate_series_id(vehicle, "dongchedi")
        with self.engine.begin() as conn:
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
            conn.execute(
                text(
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
                ).bindparams(bindparam("result_snapshot_json", type_=SAJSON)),
                {
                    "task_id": task_id,
                    "position": int(vehicle.get("position") or 1),
                    "query": query,
                    "model_name": model_name or query,
                    "autohome_series_id": autohome_series_id or None,
                    "dcd_series_id": dcd_series_id or None,
                    "result_snapshot_json": {},
                    "created_at": now,
                    "updated_at": now,
                },
            )
            if vehicle.get("id") and _table_exists(self.engine, "comparison_vehicles"):
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
                    {"child_task_id": task_id, "vehicle_id": int(vehicle["id"]), "updated_at": now},
                )
        return task_id

    def _task_exists(self, task_id: str) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT 1 FROM tasks WHERE task_id = :task_id LIMIT 1"),
                {"task_id": task_id},
            ).first()
        return row is not None

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
        if was_degraded and not already_upgraded:
            self.append_task_event(task_id, "upgraded_to_full", payload)
        self.append_task_event(task_id, "full_result_published", payload)

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
                       shared_by_task_ids, failure_category, output_path
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
                       shared_by_task_ids, failure_category, output_path
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
