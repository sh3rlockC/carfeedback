from __future__ import annotations

from io import BytesIO
import logging
from pathlib import Path
from urllib.parse import quote
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from redis.exceptions import RedisError
from rq import Queue
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Job, JobCandidate, JobStageRun, JobTimeReport
from app.models import JobArtifact
from app.schemas import (
    CreateTimeReportRequest,
    CreateJobRequest,
    CreateJobResponse,
    JobCommentPageResponse,
    JobCommentSummaryResponse,
    JobQARequest,
    JobQAResponse,
    JobOverviewResponse,
    JobProgressResponse,
    JobResultResponse,
    SelectedCandidates,
    StageStatusItem,
    TimeReportListResponse,
    TimeReportResponse,
)
from app.services.comment_time_reports import (
    comment_summary,
    extract_job_comments,
    filter_comments_by_date,
    platform_counts,
    time_report_payload,
)
from app.services.confirmed_vehicle_series import (
    candidate_canonical_key,
    canonical_query_for_selected_candidates,
    upsert_confirmed_vehicle_series,
)
from app.services.eta import estimate_job_progress_eta
from app.services.job_queue import get_job_queue
from app.services.keyword_rank_images import build_keyword_rank_pngs
from app.services.passphrase import require_passphrase_session
from app.services.qa_service import answer_job_question, find_summary_artifact
from app.services.result_reader import read_wordcloud_terms_workbook_or_empty
from app.services.result_assembler import assemble_job_result
from app.services.stage_progress import read_stage_progress

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)
QUEUE_UNAVAILABLE_MESSAGE = "任务队列暂不可用，请确认 Redis 和 worker 已启动。"


def _ensure_session(request: Request, settings: Settings) -> None:
    require_passphrase_session(request, settings)


def _ensure_same_canonical_vehicle(selected_candidates: SelectedCandidates) -> None:
    autohome_key = candidate_canonical_key(selected_candidates.autohome)
    dongchedi_key = candidate_canonical_key(selected_candidates.dongchedi)
    if autohome_key and dongchedi_key and autohome_key != dongchedi_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="selected candidates must belong to the same canonical vehicle",
        )


def _is_result_bundle_artifact(path: str) -> bool:
    lower_path = path.lower()
    return lower_path.endswith((".xlsx", ".png"))


def _is_wordcloud_terms_artifact(path: str) -> bool:
    return path.lower().endswith("_词云词项清单.xlsx")


def _is_time_report_artifact(path: str) -> bool:
    lower_path = path.lower()
    return lower_path.endswith((".xlsx", ".png"))


def _safe_zip_name(path: Path, seen: set[str]) -> str:
    base_name = path.name or "artifact"
    if base_name not in seen:
        seen.add(base_name)
        return base_name

    stem = path.stem or "artifact"
    suffix = path.suffix
    index = 2
    while True:
        candidate = f"{stem}_{index}{suffix}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate
        index += 1


@router.post("", response_model=CreateJobResponse)
def create_job(
    payload: CreateJobRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    queue: Queue = Depends(get_job_queue),
) -> CreateJobResponse:
    _ensure_session(request, settings)

    model_name = payload.model_name or payload.query
    selected_autohome = payload.selected_candidates.autohome
    selected_dongchedi = payload.selected_candidates.dongchedi
    if not selected_autohome.series_id or not selected_dongchedi.series_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="confirmed candidates required for both platforms")
    _ensure_same_canonical_vehicle(payload.selected_candidates)

    job = Job(
        query=payload.query,
        model_name=model_name,
        status="queued",
        current_stage="queued",
        collection_mode=payload.collection_mode,
        collection_summary={},
        passphrase_version=settings.pass_phrase_version,
    )
    db.add(job)
    db.flush()

    db.add_all(
        [
            JobCandidate(
                job_id=job.job_id,
                platform="autohome",
                series_id=selected_autohome.series_id,
                url=selected_autohome.url,
                title=selected_autohome.title,
                source=selected_autohome.source,
                selected=True,
            ),
            JobCandidate(
                job_id=job.job_id,
                platform="dongchedi",
                series_id=selected_dongchedi.series_id,
                url=selected_dongchedi.url,
                title=selected_dongchedi.title,
                source=selected_dongchedi.source,
                selected=True,
            ),
        ]
    )
    selected_candidates = {
        "autohome": selected_autohome,
        "dongchedi": selected_dongchedi,
    }
    upsert_confirmed_vehicle_series(
        db,
        query=canonical_query_for_selected_candidates(query=payload.query, selected_candidates=selected_candidates),
        selected_candidates=selected_candidates,
    )

    queued_stage = JobStageRun(
        job_id=job.job_id,
        stage_name="queued",
        attempt_no=1,
        status="queued",
    )
    db.add(queued_stage)
    db.commit()
    db.refresh(job)

    try:
        queue_job = queue.enqueue(
            "worker_jobs.run_job",
            kwargs={
                "job_id": job.job_id,
                "database_url": settings.database_url,
                "artifact_root": settings.artifact_root,
            },
            job_timeout=settings.worker_job_timeout_seconds,
        )
    except RedisError as exc:
        job.status = "failed"
        job.current_stage = "failed"
        queued_stage.status = "failed"
        queued_stage.error_code = "queue_unavailable"
        queued_stage.error_message = QUEUE_UNAVAILABLE_MESSAGE
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=QUEUE_UNAVAILABLE_MESSAGE) from exc

    job.queue_job_id = queue_job.id
    job.enqueued_at = job.created_at
    db.commit()
    db.refresh(job)

    return CreateJobResponse(
        job_id=job.job_id,
        status=job.status,
        current_stage=job.current_stage,
        result_url=f"/jobs/{job.job_id}",
    )


@router.get("/{job_id}", response_model=JobOverviewResponse)
def get_job(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobOverviewResponse:
    _ensure_session(request, settings)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobOverviewResponse.model_validate(job, from_attributes=True)


@router.get("/{job_id}/progress", response_model=JobProgressResponse)
def get_job_progress(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobProgressResponse:
    _ensure_session(request, settings)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    stage_runs = (
        db.query(JobStageRun)
        .filter(JobStageRun.job_id == job_id)
        .order_by(JobStageRun.id.asc())
        .all()
    )
    stages = []
    for stage in stage_runs:
        progress_percent, progress_message = read_stage_progress(settings, job_id, stage.stage_name, stage.status)
        stages.append(
            StageStatusItem(
                name=stage.stage_name,
                status=stage.status,
                attempt_no=stage.attempt_no,
                error_code=stage.error_code,
                error_message=stage.error_message,
                progress_percent=progress_percent,
                progress_message=progress_message,
            )
        )

    status_to_percent = {
        "queued": 5,
        "candidate_pending": 10,
        "checking_incremental": 12,
        "collecting_autohome": 25,
        "collecting_dcd": 40,
        "postprocessing": 55,
        "generating_hermes_outputs": 82,
        "summarizing": 70,
        "rendering_wordcloud": 82,
        "generating_ai_report": 92,
        "building_qa_corpus": 96,
        "completed": 100,
        "completed_degraded": 100,
        "failed": 100,
        "cancelled": 100,
        "expired": 100,
    }
    overall_percent = status_to_percent.get(job.current_stage, status_to_percent.get(job.status, 0))
    message = {
        "queued": "任务已创建，等待执行",
        "checking_incremental": "正在检查历史语料并准备增量采集",
        "completed": "任务已完成",
        "completed_degraded": "任务已完成，部分结果降级",
        "failed": "任务执行失败",
        "expired": "任务结果已过期，请重新创建任务",
    }.get(job.current_stage, f"当前阶段：{job.current_stage}")
    eta = estimate_job_progress_eta(db, job, stages)

    return JobProgressResponse(
        job_id=job.job_id,
        status=job.status,
        current_stage=job.current_stage,
        degraded=job.degraded,
        overall_percent=overall_percent,
        stages=stages,
        message=message,
        **eta.as_dict(),
    )


@router.get("/{job_id}/result", response_model=JobResultResponse)
def get_job_result(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobResultResponse:
    _ensure_session(request, settings)
    payload = assemble_job_result(db, settings, job_id)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return JobResultResponse.model_validate(payload)


@router.get("/{job_id}/comments/summary", response_model=JobCommentSummaryResponse)
def get_job_comment_summary(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobCommentSummaryResponse:
    _ensure_session(request, settings)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    comments = extract_job_comments(db, settings, job)
    return JobCommentSummaryResponse.model_validate(comment_summary(job_id, comments))


@router.get("/{job_id}/comments", response_model=JobCommentPageResponse)
def get_job_comments(
    job_id: str,
    request: Request,
    start_date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobCommentPageResponse:
    _ensure_session(request, settings)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    comments = filter_comments_by_date(extract_job_comments(db, settings, job), start_date=start_date, end_date=end_date)
    start = (page - 1) * page_size
    items = [comment.public_dict() for comment in comments[start : start + page_size]]
    return JobCommentPageResponse(
        job_id=job_id,
        start_date=start_date,
        end_date=end_date,
        total=len(comments),
        page=page,
        page_size=page_size,
        items=items,
    )


@router.post("/{job_id}/time-reports", response_model=TimeReportResponse)
def create_time_report(
    job_id: str,
    payload: CreateTimeReportRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    queue: Queue = Depends(get_job_queue),
) -> TimeReportResponse:
    _ensure_session(request, settings)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    comments = filter_comments_by_date(
        extract_job_comments(db, settings, job),
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    if not comments:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该时间范围内没有可分析评论")

    report = JobTimeReport(
        job_id=job.job_id,
        model_name=job.model_name,
        start_date=payload.start_date,
        end_date=payload.end_date,
        status="queued",
        sample_count=len(comments),
        platform_counts=platform_counts(comments),
    )
    db.add(report)
    db.flush()

    try:
        queue_job = queue.enqueue(
            "worker_jobs.run_time_report",
            kwargs={
                "report_id": report.report_id,
                "database_url": settings.database_url,
                "artifact_root": settings.artifact_root,
            },
            job_timeout=settings.worker_job_timeout_seconds,
        )
    except RedisError as exc:
        report.status = "failed"
        report.error_code = "queue_unavailable"
        report.error_message = QUEUE_UNAVAILABLE_MESSAGE
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=QUEUE_UNAVAILABLE_MESSAGE) from exc

    report.queue_job_id = queue_job.id
    db.commit()
    db.refresh(report)
    return TimeReportResponse.model_validate(time_report_payload(report))


@router.get("/{job_id}/time-reports", response_model=TimeReportListResponse)
def list_time_reports(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TimeReportListResponse:
    _ensure_session(request, settings)
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    reports = (
        db.query(JobTimeReport)
        .filter(JobTimeReport.job_id == job_id)
        .order_by(JobTimeReport.created_at.desc())
        .all()
    )
    return TimeReportListResponse(items=[TimeReportResponse.model_validate(time_report_payload(report)) for report in reports])


@router.get("/{job_id}/time-reports/{report_id}", response_model=TimeReportResponse)
def get_time_report(
    job_id: str,
    report_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TimeReportResponse:
    _ensure_session(request, settings)
    report = db.get(JobTimeReport, report_id)
    if report is None or report.job_id != job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="time report not found")
    return TimeReportResponse.model_validate(time_report_payload(report))


@router.post("/{job_id}/qa", response_model=JobQAResponse)
def answer_job_qa(
    job_id: str,
    payload: JobQARequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> JobQAResponse:
    _ensure_session(request, settings)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    if find_summary_artifact(db, job_id) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="qa is not available for this job")

    try:
        result = answer_job_question(
            db,
            job_id=job_id,
            question=payload.question,
            model_name=job.model_name,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return JobQAResponse.model_validate(result)


@router.get("/{job_id}/artifacts/{artifact_id}")
def download_artifact(
    job_id: str,
    artifact_id: int,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _ensure_session(request, settings)
    artifact = db.get(JobArtifact, artifact_id)
    if artifact is None or artifact.job_id != job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact not found")
    path = Path(artifact.artifact_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="artifact file missing")
    return FileResponse(path=path, filename=path.name)


@router.get("/{job_id}/artifacts.zip")
def download_result_bundle(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _ensure_session(request, settings)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    artifacts = (
        db.query(JobArtifact)
        .filter(JobArtifact.job_id == job_id)
        .order_by(JobArtifact.id.asc())
        .all()
    )
    paths = [
        Path(artifact.artifact_path)
        for artifact in artifacts
        if _is_result_bundle_artifact(artifact.artifact_path) and Path(artifact.artifact_path).exists()
    ]
    if not paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no downloadable result artifacts")

    buffer = BytesIO()
    seen_names: set[str] = set()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, arcname=_safe_zip_name(path, seen_names))
        term_path = next(
            (
                Path(artifact.artifact_path)
                for artifact in artifacts
                if _is_wordcloud_terms_artifact(artifact.artifact_path) and Path(artifact.artifact_path).exists()
            ),
            None,
        )
        if term_path:
            rankings = read_wordcloud_terms_workbook_or_empty(term_path)
            if any(rankings.values()):
                try:
                    for filename, content in build_keyword_rank_pngs(rankings):
                        if filename not in seen_names:
                            seen_names.add(filename)
                            archive.writestr(filename, content)
                except Exception as exc:
                    logger.warning("failed to render keyword rank pngs for job %s: %s", job_id, exc)

    filename = f"{job.model_name}_全部结果.zip"
    encoded_filename = quote(filename)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=result_bundle.zip; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/{job_id}/time-reports/{report_id}/artifacts.zip")
def download_time_report_bundle(
    job_id: str,
    report_id: str,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    _ensure_session(request, settings)
    report = db.get(JobTimeReport, report_id)
    if report is None or report.job_id != job_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="time report not found")

    paths = [
        Path(path)
        for path in (report.artifact_paths or [])
        if _is_time_report_artifact(str(path)) and Path(path).exists()
    ]
    if not paths:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no downloadable time report artifacts")

    buffer = BytesIO()
    seen_names: set[str] = set()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, arcname=_safe_zip_name(path, seen_names))

    filename = f"{report.model_name}_{report.start_date}_{report.end_date}_时间版一页纸.zip"
    encoded_filename = quote(filename)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=time_report.zip; filename*=UTF-8''{encoded_filename}"},
    )
