from __future__ import annotations

from pathlib import Path
import shutil

from fastapi import APIRouter, Depends, Request
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.registry import FailedJobRegistry
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Job, JobAIReport, JobArtifact, JobQAChunk, JobTimeReport
from app.schemas import (
    AdminDbFailedJobItem,
    AdminFailedJobsDeleteResponse,
    AdminFailedJobsResponse,
    AdminRedisFailedJobItem,
)
from app.services.passphrase import require_passphrase_session

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _failed_registry(settings: Settings) -> tuple[FailedJobRegistry, Queue]:
    redis = Redis.from_url(settings.redis_url)
    queue = Queue(settings.worker_queue_name, connection=redis)
    return FailedJobRegistry(queue=queue), queue


def _redis_failed_jobs(settings: Settings) -> tuple[list[AdminRedisFailedJobItem], str | None]:
    try:
        registry, queue = _failed_registry(settings)
        items = []
        for job_id in registry.get_job_ids():
            job = queue.fetch_job(job_id)
            items.append(
                AdminRedisFailedJobItem(
                    job_id=job_id,
                    status=getattr(job, "get_status", lambda: None)() if job else None,
                    origin=getattr(job, "origin", None) if job else None,
                    description=getattr(job, "description", None) if job else None,
                )
            )
        return items, None
    except RedisError as exc:
        return [], str(exc)


def _safe_remove_job_dir(settings: Settings, job_id: str) -> str | None:
    artifact_root = Path(settings.artifact_root).expanduser().resolve()
    target = (artifact_root / job_id).resolve()
    if target == artifact_root or artifact_root not in target.parents:
        return None
    if not target.exists():
        return None
    shutil.rmtree(target)
    return str(target)


@router.get("/jobs/failed", response_model=AdminFailedJobsResponse)
def list_failed_jobs(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AdminFailedJobsResponse:
    require_passphrase_session(request, settings)
    redis_failed_jobs, redis_error = _redis_failed_jobs(settings)
    db_failed_jobs = [
        AdminDbFailedJobItem(
            job_id=job.job_id,
            query=job.query,
            model_name=job.model_name,
            current_stage=job.current_stage,
            created_at=job.created_at,
            finished_at=job.finished_at,
        )
        for job in db.query(Job).filter(Job.status == "failed").order_by(Job.created_at.desc()).all()
    ]
    return AdminFailedJobsResponse(
        redis_failed_jobs=redis_failed_jobs,
        db_failed_jobs=db_failed_jobs,
        redis_error=redis_error,
    )


@router.delete("/jobs/failed", response_model=AdminFailedJobsDeleteResponse)
def clear_failed_jobs(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AdminFailedJobsDeleteResponse:
    require_passphrase_session(request, settings)
    redis_removed_job_ids: list[str] = []
    redis_error = None
    try:
        registry, _queue = _failed_registry(settings)
        for job_id in registry.get_job_ids():
            registry.remove(job_id, delete_job=True)
            redis_removed_job_ids.append(job_id)
    except RedisError as exc:
        redis_error = str(exc)

    failed_jobs = db.query(Job).filter(Job.status == "failed").order_by(Job.created_at.desc()).all()
    db_expired_job_ids = [job.job_id for job in failed_jobs]
    deleted_artifact_dirs: list[str] = []
    for job in failed_jobs:
        removed = _safe_remove_job_dir(settings, job.job_id)
        if removed:
            deleted_artifact_dirs.append(removed)
        db.query(JobArtifact).filter(JobArtifact.job_id == job.job_id).delete(synchronize_session=False)
        db.query(JobAIReport).filter(JobAIReport.job_id == job.job_id).delete(synchronize_session=False)
        db.query(JobQAChunk).filter(JobQAChunk.job_id == job.job_id).delete(synchronize_session=False)
        db.query(JobTimeReport).filter(JobTimeReport.job_id == job.job_id).delete(synchronize_session=False)
        job.status = "expired"
        job.current_stage = "expired"
    db.commit()

    return AdminFailedJobsDeleteResponse(
        redis_removed_job_ids=redis_removed_job_ids,
        db_expired_job_ids=db_expired_job_ids,
        deleted_artifact_dirs=deleted_artifact_dirs,
        redis_error=redis_error,
    )
