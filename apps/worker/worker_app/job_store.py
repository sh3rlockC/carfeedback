from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON as SAJSON
from sqlalchemy import bindparam, create_engine, text


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def new_worker_job_id() -> str:
    return f"job_{utc_now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"


def _as_datetime(value: datetime | str | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


def _db_bool(value: Any) -> bool:
    return bool(value)


@dataclass(frozen=True)
class WorkerJobInputs:
    job_id: str
    query: str
    model_name: str
    autohome_series_id: str
    dongchedi_series_id: str
    collection_mode: str = "incremental"


@dataclass(frozen=True)
class TimeReportInputs:
    report_id: str
    job_id: str
    model_name: str
    start_date: str
    end_date: str


@dataclass(frozen=True)
class ComparisonVehicleInputs:
    id: int
    position: int
    query: str
    model_name: str
    status: str
    source_job_id: str | None
    child_job_id: str | None
    selected_candidates: dict[str, Any]


@dataclass(frozen=True)
class ComparisonInputs:
    comparison_id: str
    start_date: str | None
    end_date: str | None
    passphrase_version: str
    vehicles: list[ComparisonVehicleInputs]


class DatabaseJobStore:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, future=True, **_engine_kwargs(database_url))

    def fetch_job_inputs(self, job_id: str) -> WorkerJobInputs:
        query = text(
            """
            SELECT j.job_id, j.query, j.model_name, j.collection_mode, c.platform, c.series_id
            FROM jobs j
            JOIN job_candidates c ON c.job_id = j.job_id
            WHERE j.job_id = :job_id AND c.selected = :selected
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query, {"job_id": job_id, "selected": True}).mappings().all()
        if not rows:
            raise RuntimeError(f"job not found: {job_id}")

        series_by_platform = {row["platform"]: row["series_id"] for row in rows}
        if not series_by_platform.get("autohome") or not series_by_platform.get("dongchedi"):
            raise RuntimeError(f"job missing selected candidates: {job_id}")

        return WorkerJobInputs(
            job_id=job_id,
            query=str(rows[0]["query"]),
            model_name=rows[0]["model_name"],
            autohome_series_id=str(series_by_platform["autohome"]),
            dongchedi_series_id=str(series_by_platform["dongchedi"]),
            collection_mode=str(rows[0]["collection_mode"] or "incremental"),
        )

    def update_collection_summary(self, job_id: str, summary: dict[str, Any]) -> None:
        statement = text(
            """
            UPDATE jobs
            SET collection_summary = :collection_summary
            WHERE job_id = :job_id
            """
        ).bindparams(bindparam("collection_summary", type_=SAJSON))
        with self.engine.begin() as conn:
            conn.execute(statement, {"job_id": job_id, "collection_summary": summary})

    def fetch_time_report_inputs(self, report_id: str) -> TimeReportInputs:
        query = text(
            """
            SELECT report_id, job_id, model_name, start_date, end_date
            FROM job_time_reports
            WHERE report_id = :report_id
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(query, {"report_id": report_id}).mappings().first()
        if row is None:
            raise RuntimeError(f"time report not found: {report_id}")
        return TimeReportInputs(
            report_id=str(row["report_id"]),
            job_id=str(row["job_id"]),
            model_name=str(row["model_name"]),
            start_date=str(row["start_date"]),
            end_date=str(row["end_date"]),
        )

    def fetch_comparison_inputs(self, comparison_id: str) -> ComparisonInputs:
        query = text(
            """
            SELECT c.comparison_id, c.start_date, c.end_date, c.passphrase_version,
                   v.id, v.position, v.query, v.model_name, v.status,
                   v.source_job_id, v.child_job_id, v.selected_candidates
            FROM comparison_jobs c
            JOIN comparison_vehicles v ON v.comparison_id = c.comparison_id
            WHERE c.comparison_id = :comparison_id
            ORDER BY v.position ASC, v.id ASC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query, {"comparison_id": comparison_id}).mappings().all()
        if not rows:
            raise RuntimeError(f"comparison not found: {comparison_id}")

        vehicles = []
        for row in rows:
            selected_candidates = row["selected_candidates"] or {}
            if isinstance(selected_candidates, str):
                selected_candidates = json.loads(selected_candidates)
            vehicles.append(
                ComparisonVehicleInputs(
                    id=int(row["id"]),
                    position=int(row["position"] or 0),
                    query=str(row["query"]),
                    model_name=str(row["model_name"]),
                    status=str(row["status"]),
                    source_job_id=str(row["source_job_id"]) if row["source_job_id"] else None,
                    child_job_id=str(row["child_job_id"]) if row["child_job_id"] else None,
                    selected_candidates=dict(selected_candidates),
                )
            )
        first = rows[0]
        return ComparisonInputs(
            comparison_id=str(first["comparison_id"]),
            start_date=str(first["start_date"]) if first["start_date"] else None,
            end_date=str(first["end_date"]) if first["end_date"] else None,
            passphrase_version=str(first["passphrase_version"]),
            vehicles=vehicles,
        )

    def mark_comparison_running(self, comparison_id: str) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE comparison_jobs
                    SET status = 'running',
                        current_stage = 'collecting_models',
                        started_at = COALESCE(started_at, :started_at),
                        error_code = NULL,
                        error_message = NULL
                    WHERE comparison_id = :comparison_id
                    """
                ),
                {"comparison_id": comparison_id, "started_at": now},
            )

    def mark_comparison_comparing(self, comparison_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE comparison_jobs
                    SET status = 'running',
                        current_stage = 'comparing'
                    WHERE comparison_id = :comparison_id
                    """
                ),
                {"comparison_id": comparison_id},
            )

    def mark_comparison_vehicle_status(
        self,
        vehicle_id: int,
        *,
        status: str,
        source_job_id: str | None = None,
        child_job_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE comparison_vehicles
                    SET status = :status,
                        source_job_id = COALESCE(:source_job_id, source_job_id),
                        child_job_id = COALESCE(:child_job_id, child_job_id),
                        error_code = :error_code,
                        error_message = :error_message,
                        updated_at = :updated_at
                    WHERE id = :vehicle_id
                    """
                ),
                {
                    "vehicle_id": vehicle_id,
                    "status": status,
                    "source_job_id": source_job_id,
                    "child_job_id": child_job_id,
                    "error_code": error_code,
                    "error_message": error_message,
                    "updated_at": now,
                },
            )

    def ensure_comparison_child_job(self, vehicle: ComparisonVehicleInputs, *, passphrase_version: str) -> str:
        if vehicle.child_job_id:
            return vehicle.child_job_id
        now = utc_now_iso()
        job_id = new_worker_job_id()
        autohome = dict(vehicle.selected_candidates.get("autohome") or {})
        dongchedi = dict(vehicle.selected_candidates.get("dongchedi") or {})
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (
                        job_id, query, model_name, status, current_stage, degraded,
                        passphrase_version, queue_job_id, created_at, enqueued_at, started_at, finished_at
                    ) VALUES (
                        :job_id, :query, :model_name, 'queued', 'queued', :degraded,
                        :passphrase_version, NULL, :created_at, :enqueued_at, NULL, NULL
                    )
                    """
                ),
                {
                    "job_id": job_id,
                    "query": vehicle.query,
                    "model_name": vehicle.model_name,
                    "degraded": _db_bool(False),
                    "passphrase_version": passphrase_version,
                    "created_at": now,
                    "enqueued_at": now,
                },
            )
            for platform, candidate in (("autohome", autohome), ("dongchedi", dongchedi)):
                conn.execute(
                    text(
                        """
                        INSERT INTO job_candidates (job_id, platform, series_id, url, title, source, selected)
                        VALUES (:job_id, :platform, :series_id, :url, :title, :source, :selected)
                        """
                    ),
                    {
                        "job_id": job_id,
                        "platform": platform,
                        "series_id": str(candidate.get("series_id") or ""),
                        "url": candidate.get("url"),
                        "title": candidate.get("title") or vehicle.model_name,
                        "source": candidate.get("source"),
                        "selected": _db_bool(True),
                    },
                )
            conn.execute(
                text(
                    """
                    INSERT INTO job_stage_runs (
                        job_id, stage_name, attempt_no, status, started_at, ended_at, duration_ms, error_code, error_message
                    ) VALUES (
                        :job_id, 'queued', 1, 'queued', :started_at, NULL, NULL, NULL, NULL
                    )
                    """
                ),
                {"job_id": job_id, "started_at": now},
            )
            conn.execute(
                text(
                    """
                    UPDATE comparison_vehicles
                    SET child_job_id = :child_job_id,
                        status = 'running',
                        updated_at = :updated_at
                    WHERE id = :vehicle_id
                    """
                ),
                {"vehicle_id": vehicle.id, "child_job_id": job_id, "updated_at": now},
            )
        return job_id

    def comparison_source_artifacts(self, job_id: str) -> dict[str, str]:
        query = text(
            """
            SELECT artifact_path
            FROM job_artifacts
            WHERE job_id = :job_id
            ORDER BY id ASC
            """
        )
        artifacts: dict[str, str] = {}
        with self.engine.begin() as conn:
            rows = conn.execute(query, {"job_id": job_id}).mappings().all()
        for row in rows:
            path = str(row["artifact_path"])
            for suffix in ("final_report.json", "analysis_facts.jsonl", "llm_metrics.json"):
                if path.endswith(suffix):
                    artifacts[suffix] = path
        return artifacts

    def comparison_downloadable_artifacts(self, job_id: str) -> list[str]:
        query = text(
            """
            SELECT artifact_path
            FROM job_artifacts
            WHERE job_id = :job_id
            ORDER BY id ASC
            """
        )
        with self.engine.begin() as conn:
            rows = conn.execute(query, {"job_id": job_id}).mappings().all()
        paths: list[str] = []
        for row in rows:
            path = str(row["artifact_path"])
            if path.lower().endswith((".xlsx", ".png")):
                paths.append(path)
        return paths

    def mark_comparison_completed(
        self,
        comparison_id: str,
        *,
        report_json: dict[str, Any],
        artifact_paths: list[str],
        degraded: bool,
    ) -> None:
        now = utc_now_iso()
        status = "completed_degraded" if degraded else "completed"
        statement = text(
            """
            UPDATE comparison_jobs
            SET status = :status,
                current_stage = :status,
                degraded = :degraded,
                report_json = :report_json,
                error_code = NULL,
                error_message = NULL,
                finished_at = :finished_at
            WHERE comparison_id = :comparison_id
            """
        ).bindparams(bindparam("report_json", type_=SAJSON))
        with self.engine.begin() as conn:
            conn.execute(
                statement,
                {
                    "comparison_id": comparison_id,
                    "status": status,
                    "degraded": _db_bool(degraded),
                    "report_json": report_json,
                    "finished_at": now,
                },
            )
            conn.execute(text("DELETE FROM comparison_artifacts WHERE comparison_id = :comparison_id"), {"comparison_id": comparison_id})
            for artifact_path in artifact_paths:
                artifact_name = artifact_path.rsplit("/", 1)[-1]
                conn.execute(
                    text(
                        """
                        INSERT INTO comparison_artifacts (comparison_id, artifact_type, artifact_path, artifact_url, source_stage, created_at)
                        VALUES (:comparison_id, :artifact_type, :artifact_path, NULL, :source_stage, :created_at)
                        """
                    ),
                    {
                        "comparison_id": comparison_id,
                        "artifact_type": self._infer_comparison_artifact_type(artifact_path),
                        "artifact_path": artifact_path,
                        "source_stage": "comparison"
                        if artifact_name in {"final_comparison.json", "comparison_summary.xlsx", "comparison_dimension_matrix.xlsx", "llm_metrics.json"}
                        else "snapshot",
                        "created_at": now,
                    },
                )

    def mark_comparison_failed(self, comparison_id: str, *, error_code: str, error_message: str) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE comparison_jobs
                    SET status = 'failed',
                        current_stage = 'failed',
                        error_code = :error_code,
                        error_message = :error_message,
                        finished_at = :finished_at
                    WHERE comparison_id = :comparison_id
                    """
                ),
                {
                    "comparison_id": comparison_id,
                    "error_code": error_code,
                    "error_message": error_message,
                    "finished_at": now,
                },
            )

    def mark_time_report_running(self, report_id: str) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE job_time_reports
                    SET status = 'running',
                        error_code = NULL,
                        error_message = NULL,
                        updated_at = :updated_at
                    WHERE report_id = :report_id
                    """
                ),
                {"report_id": report_id, "updated_at": now},
            )

    def mark_time_report_completed(self, report_id: str, result: dict[str, Any]) -> None:
        now = utc_now_iso()
        statement = text(
            """
            UPDATE job_time_reports
            SET status = 'completed',
                sample_count = :sample_count,
                platform_counts = :platform_counts,
                report_json = :report_json,
                artifact_paths = :artifact_paths,
                source = :source,
                error_code = NULL,
                error_message = NULL,
                updated_at = :updated_at,
                completed_at = :completed_at
            WHERE report_id = :report_id
            """
        ).bindparams(
            bindparam("platform_counts", type_=SAJSON),
            bindparam("report_json", type_=SAJSON),
            bindparam("artifact_paths", type_=SAJSON),
        )
        with self.engine.begin() as conn:
            conn.execute(
                statement,
                {
                    "report_id": report_id,
                    "sample_count": int(result.get("sample_count") or 0),
                    "platform_counts": result.get("platform_counts") or {},
                    "report_json": result.get("report_json") or {},
                    "artifact_paths": result.get("artifact_paths") or [],
                    "source": result.get("source") or "hermes",
                    "updated_at": now,
                    "completed_at": now,
                },
            )

    def mark_time_report_failed(self, report_id: str, *, error_code: str, error_message: str) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE job_time_reports
                    SET status = 'failed',
                        error_code = :error_code,
                        error_message = :error_message,
                        updated_at = :updated_at,
                        completed_at = :completed_at
                    WHERE report_id = :report_id
                    """
                ),
                {
                    "report_id": report_id,
                    "error_code": error_code,
                    "error_message": error_message,
                    "updated_at": now,
                    "completed_at": now,
                },
            )

    def mark_job_enqueued(self, *, job_id: str, queue_job_id: str | None) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'queued',
                        current_stage = 'queued',
                        started_at = NULL,
                        finished_at = NULL,
                        degraded = :degraded,
                        queue_job_id = :queue_job_id,
                        enqueued_at = :enqueued_at
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": job_id, "degraded": _db_bool(False), "queue_job_id": queue_job_id, "enqueued_at": now},
            )

    def handle_pipeline_event(self, job_id: str, event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type == "stage_running":
            self._stage_started(job_id=job_id, stage_name=event["stage"])
            return
        if event_type in {"stage_success", "stage_degraded", "stage_failed"}:
            self._stage_finished(job_id=job_id, event=event)
            return
        if event_type == "pipeline_completed":
            self._pipeline_completed(job_id=job_id, event=event)

    def mark_job_failed(self, job_id: str, *, error_code: str, error_message: str) -> None:
        now = utc_now()
        now_iso = now.isoformat()
        with self.engine.begin() as conn:
            job_row = conn.execute(
                text(
                    """
                    SELECT current_stage
                    FROM jobs
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": job_id},
            ).mappings().first()
            current_stage = str(job_row["current_stage"]) if job_row and job_row["current_stage"] else None

            stage_row = conn.execute(
                text(
                    """
                    SELECT id, started_at
                    FROM job_stage_runs
                    WHERE job_id = :job_id AND status = 'running'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"job_id": job_id},
            ).mappings().first()
            if stage_row is None and current_stage and current_stage not in {"completed", "failed", "completed_degraded"}:
                stage_row = conn.execute(
                    text(
                        """
                        SELECT id, started_at
                        FROM job_stage_runs
                        WHERE job_id = :job_id AND stage_name = :stage_name
                        ORDER BY id DESC
                        LIMIT 1
                        """
                    ),
                    {"job_id": job_id, "stage_name": current_stage},
                ).mappings().first()

            if stage_row is not None:
                started_at = _as_datetime(stage_row["started_at"]) or now
                duration_ms = int(max((now - started_at).total_seconds() * 1000, 0))
                conn.execute(
                    text(
                        """
                        UPDATE job_stage_runs
                        SET status = 'failed',
                            ended_at = :ended_at,
                            duration_ms = :duration_ms,
                            error_code = :error_code,
                            error_message = :error_message
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": stage_row["id"],
                        "ended_at": now_iso,
                        "duration_ms": duration_ms,
                        "error_code": error_code,
                        "error_message": error_message,
                    },
                )

            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = 'failed',
                        current_stage = 'failed',
                        finished_at = :finished_at
                    WHERE job_id = :job_id
                    """
                ),
                {"job_id": job_id, "finished_at": now_iso},
            )

    def _next_attempt_no(self, conn, job_id: str, stage_name: str) -> int:
        current = conn.execute(
            text(
                """
                SELECT COALESCE(MAX(attempt_no), 0)
                FROM job_stage_runs
                WHERE job_id = :job_id AND stage_name = :stage_name
                """
            ),
            {"job_id": job_id, "stage_name": stage_name},
        ).scalar_one()
        return int(current) + 1

    def _stage_started(self, *, job_id: str, stage_name: str) -> None:
        now = utc_now_iso()
        with self.engine.begin() as conn:
            attempt_no = self._next_attempt_no(conn, job_id, stage_name)
            conn.execute(
                text(
                    """
                    INSERT INTO job_stage_runs (
                        job_id, stage_name, attempt_no, status, started_at, ended_at, duration_ms, error_code, error_message
                    ) VALUES (
                        :job_id, :stage_name, :attempt_no, 'running', :started_at, NULL, NULL, NULL, NULL
                    )
                    """
                ),
                {
                    "job_id": job_id,
                    "stage_name": stage_name,
                    "attempt_no": attempt_no,
                    "started_at": now,
                },
            )
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = :status,
                        current_stage = :current_stage,
                        started_at = COALESCE(started_at, :started_at)
                    WHERE job_id = :job_id
                    """
                ),
                {
                    "job_id": job_id,
                    "status": stage_name,
                    "current_stage": stage_name,
                    "started_at": now,
                },
            )

    def _stage_finished(self, *, job_id: str, event: dict[str, Any]) -> None:
        stage_name = event["stage"]
        snapshot = event.get("snapshot") or {}
        now = utc_now()
        now_iso = now.isoformat()
        status = snapshot.get("status", "success")
        error_code = event.get("error_code")
        error_message = event.get("error_message")
        result = event.get("result")

        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, started_at
                    FROM job_stage_runs
                    WHERE job_id = :job_id AND stage_name = :stage_name
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"job_id": job_id, "stage_name": stage_name},
            ).mappings().first()

            if row is None or row["started_at"] is None or status == "degraded" and event.get("message") == "skipped in single-platform fallback":
                attempt_no = self._next_attempt_no(conn, job_id, stage_name)
                conn.execute(
                    text(
                        """
                        INSERT INTO job_stage_runs (
                            job_id, stage_name, attempt_no, status, started_at, ended_at, duration_ms, error_code, error_message
                        ) VALUES (
                            :job_id, :stage_name, :attempt_no, :status, :started_at, :ended_at, 0, :error_code, :error_message
                        )
                        """
                    ),
                    {
                        "job_id": job_id,
                        "stage_name": stage_name,
                        "attempt_no": attempt_no,
                        "status": status,
                        "started_at": now_iso,
                        "ended_at": now_iso,
                        "error_code": error_code,
                        "error_message": error_message,
                    },
                )
            else:
                started_at = _as_datetime(row["started_at"]) or now
                duration_ms = int(max((now - started_at).total_seconds() * 1000, 0))
                conn.execute(
                    text(
                        """
                        UPDATE job_stage_runs
                        SET status = :status,
                            ended_at = :ended_at,
                            duration_ms = :duration_ms,
                            error_code = :error_code,
                            error_message = :error_message
                        WHERE id = :id
                        """
                    ),
                    {
                        "id": row["id"],
                        "status": status,
                        "ended_at": now_iso,
                        "duration_ms": duration_ms,
                        "error_code": error_code,
                        "error_message": error_message,
                    },
                )

            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET current_stage = :current_stage,
                        degraded = :degraded
                    WHERE job_id = :job_id
                    """
                ),
                {
                    "job_id": job_id,
                    "current_stage": stage_name,
                    "degraded": _db_bool(snapshot.get("degraded")),
                },
            )

            if status in {"success", "degraded"} and result is not None:
                conn.execute(
                    text("DELETE FROM job_artifacts WHERE job_id = :job_id AND source_stage = :source_stage"),
                    {"job_id": job_id, "source_stage": stage_name},
                )
                for artifact_path in result.artifact_paths:
                    conn.execute(
                        text(
                            """
                            INSERT INTO job_artifacts (job_id, artifact_type, artifact_path, artifact_url, source_stage, created_at)
                            VALUES (:job_id, :artifact_type, :artifact_path, NULL, :source_stage, :created_at)
                            """
                        ),
                        {
                            "job_id": job_id,
                            "artifact_type": self._infer_artifact_type(artifact_path),
                            "artifact_path": artifact_path,
                            "source_stage": stage_name,
                            "created_at": now_iso,
                        },
                    )

    def _pipeline_completed(self, *, job_id: str, event: dict[str, Any]) -> None:
        result = event["result"]
        now = utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE jobs
                    SET status = :status,
                        current_stage = :current_stage,
                        degraded = :degraded,
                        finished_at = :finished_at
                    WHERE job_id = :job_id
                    """
                ),
                {
                    "job_id": job_id,
                    "status": result.status,
                    "current_stage": result.status,
                    "degraded": _db_bool(result.degraded),
                    "finished_at": now,
                },
            )

    def _infer_artifact_type(self, artifact_path: str) -> str:
        path = artifact_path.lower()
        if path.endswith(".validation.json"):
            return "validation_json"
        if path.endswith(".progress.json"):
            return "progress_json"
        if path.endswith(".failed-pages.json"):
            return "failed_pages_json"
        if path.endswith(".png"):
            return "image_png"
        if path.endswith(".xlsx"):
            return "excel"
        if path.endswith(".json"):
            return "json"
        return "file"

    def _infer_comparison_artifact_type(self, artifact_path: str) -> str:
        path = artifact_path.lower()
        name = path.rsplit("/", 1)[-1]
        if name == "final_comparison.json":
            return "comparison_json"
        if name == "comparison_summary.xlsx":
            return "comparison_excel"
        if name == "comparison_dimension_matrix.xlsx":
            return "comparison_dimension_excel"
        if name.endswith(".analysis_facts.jsonl"):
            return "source_analysis_facts_jsonl"
        if name.endswith(".final_report.json"):
            return "source_final_report_json"
        if name == "llm_metrics.json" or name.endswith(".llm_metrics.json"):
            return "llm_metrics_json"
        return self._infer_artifact_type(artifact_path)
