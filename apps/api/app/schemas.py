from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


APPROVED_JOB_STATUSES = [
    "access_pending",
    "candidate_pending",
    "queued",
    "checking_incremental",
    "collecting_autohome",
    "collecting_dcd",
    "postprocessing",
    "generating_hermes_outputs",
    "summarizing",
    "rendering_wordcloud",
    "generating_ai_report",
    "building_qa_corpus",
    "completed",
    "completed_degraded",
    "failed",
    "cancelled",
    "expired",
]


class AccessVerifyRequest(BaseModel):
    passphrase: str = Field(min_length=1, max_length=256)


class AccessVerifyResponse(BaseModel):
    ok: bool
    passphrase_version: str


class PlatformCandidate(BaseModel):
    series_id: str | None = None
    url: str | None = None
    title: str | None = None
    source: str | None = None
    evidence_url: str | None = None
    kind: str | None = None
    note: str | None = None
    canonical_query: str | None = None
    canonical_query_key: str | None = None


class PlatformCandidateGroup(BaseModel):
    best: PlatformCandidate | None = None
    candidates: list[PlatformCandidate] = Field(default_factory=list)


class VehicleResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=255)


class VehicleResolveResponse(BaseModel):
    query: str
    autohome: PlatformCandidateGroup
    dongchedi: PlatformCandidateGroup


class SelectedCandidates(BaseModel):
    autohome: PlatformCandidate
    dongchedi: PlatformCandidate


class CreateJobRequest(BaseModel):
    query: str = Field(min_length=1, max_length=255)
    model_name: str | None = Field(default=None, max_length=255)
    collection_mode: Literal["incremental", "full_refresh"] = "incremental"
    selected_candidates: SelectedCandidates


class CreateJobResponse(BaseModel):
    job_id: str
    status: str
    current_stage: str
    result_url: str


class TaskCreateVehicle(BaseModel):
    query: str = Field(min_length=1, max_length=255)


class TaskCreateRequest(BaseModel):
    task_type: Literal["single", "comparison"]
    vehicles: list[TaskCreateVehicle] = Field(min_length=1, max_length=5)


class TaskCreateResponse(BaseModel):
    task_id: str
    status: str
    view_url: str
    manage_url: str


class TaskListItem(BaseModel):
    task_id: str
    task_type: str
    display_name: str
    status: str
    current_stage: str
    degraded: bool
    upgraded_to_full: bool
    eta_seconds: int | None
    eta_reason: str | None
    created_at: datetime
    completed_at: datetime | None


class TaskDetailResponse(TaskListItem):
    vehicles: list[dict]
    events: list[dict]
    artifacts: list[dict]


class JobOverviewResponse(BaseModel):
    job_id: str
    query: str
    model_name: str
    status: str
    current_stage: str
    collection_mode: str = "incremental"
    degraded: bool
    passphrase_version: str
    queue_job_id: str | None
    created_at: datetime
    enqueued_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None


class StageStatusItem(BaseModel):
    name: str
    status: str
    attempt_no: int = 1
    error_code: str | None = None
    error_message: str | None = None
    progress_percent: int | None = None
    progress_message: str | None = None


class JobProgressResponse(BaseModel):
    job_id: str
    status: str
    current_stage: str
    degraded: bool
    overall_percent: int
    stages: list[StageStatusItem]
    message: str
    estimated_remaining_seconds: int | None = None
    estimated_remaining_minutes: int | None = None
    eta_label: str = "预计剩余时间计算中"
    eta_confidence: str = "unknown"


class ArtifactItem(BaseModel):
    id: int
    type: str
    path: str
    url: str
    source_stage: str | None = None


class ComparisonVehicleOptionRequest(BaseModel):
    query: str = Field(min_length=1, max_length=255)


class ReusableJobOptionResponse(BaseModel):
    job_id: str
    model_name: str
    finished_at: datetime | None = None
    source: str


class ComparisonVehicleOptionResponse(BaseModel):
    query: str
    resolve: VehicleResolveResponse
    reuse_options: list[ReusableJobOptionResponse] = Field(default_factory=list)


class ComparisonOptionsRequest(BaseModel):
    vehicles: list[ComparisonVehicleOptionRequest] = Field(min_length=1, max_length=5)


class ComparisonOptionsResponse(BaseModel):
    vehicles: list[ComparisonVehicleOptionResponse] = Field(default_factory=list)


class ComparisonVehicleInput(BaseModel):
    query: str = Field(min_length=1, max_length=255)
    model_name: str | None = Field(default=None, max_length=255)
    selected_candidates: SelectedCandidates
    reuse_job_id: str | None = Field(default=None, max_length=64)


class ComparisonCreateRequest(BaseModel):
    vehicles: list[ComparisonVehicleInput] = Field(min_length=2, max_length=5)
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ComparisonCreateResponse(BaseModel):
    comparison_id: str
    status: str
    current_stage: str
    progress_url: str
    result_url: str


class ComparisonVehicleProgress(BaseModel):
    query: str
    model_name: str
    status: str
    source_job_id: str | None = None
    child_job_id: str | None = None
    estimated_remaining_seconds: int | None = None
    estimated_remaining_minutes: int | None = None
    eta_label: str = "预计剩余时间计算中"
    eta_confidence: str = "unknown"
    error_message: str | None = None


class ComparisonProgressResponse(BaseModel):
    comparison_id: str
    status: str
    current_stage: str
    degraded: bool
    overall_percent: int
    estimated_remaining_seconds: int | None = None
    estimated_remaining_minutes: int | None = None
    eta_label: str = "预计剩余时间计算中"
    eta_confidence: str = "unknown"
    vehicles: list[ComparisonVehicleProgress] = Field(default_factory=list)
    message: str


class ComparisonArtifactItem(BaseModel):
    id: int
    type: str
    path: str
    url: str
    source_stage: str | None = None


class ComparisonResultResponse(BaseModel):
    comparison_id: str
    status: str
    degraded: bool
    retention_days: int
    vehicle_count: int
    report_json: dict = Field(default_factory=dict)
    artifacts: list[ComparisonArtifactItem] = Field(default_factory=list)
    zip_url: str


class TemplateReportResponse(BaseModel):
    title: str
    highlights: list[str] = Field(default_factory=list)


class StructuredSectionsResponse(BaseModel):
    overview: list[dict[str, str]] = Field(default_factory=list)
    compare: list[dict[str, str]] = Field(default_factory=list)
    business: list[dict[str, str]] = Field(default_factory=list)
    opportunities: list[dict[str, str]] = Field(default_factory=list)


class KeywordRankItemResponse(BaseModel):
    term: str
    count: int


class KeywordRankingsResponse(BaseModel):
    positive: list[KeywordRankItemResponse] = Field(default_factory=list)
    negative: list[KeywordRankItemResponse] = Field(default_factory=list)
    combined: list[KeywordRankItemResponse] = Field(default_factory=list)


class WordcloudResponse(BaseModel):
    positive_image_url: str | None = None
    negative_image_url: str | None = None
    terms_excel_url: str | None = None
    keyword_rankings: KeywordRankingsResponse = Field(default_factory=KeywordRankingsResponse)


class SampleSummaryResponse(BaseModel):
    autohome_count: int
    dcd_count: int


class CollectionPlatformSummaryResponse(BaseModel):
    existing_count: int = 0
    new_count: int = 0
    total_count: int = 0
    pages_scanned: int = 0
    mode: str = "incremental"
    stop_reason: str | None = None


class CollectionSummaryResponse(BaseModel):
    autohome: CollectionPlatformSummaryResponse = Field(default_factory=CollectionPlatformSummaryResponse)
    dongchedi: CollectionPlatformSummaryResponse = Field(default_factory=CollectionPlatformSummaryResponse)


class JobResultResponse(BaseModel):
    job_id: str
    status: str
    degraded: bool
    model_name: str
    retention_days: int
    sample_summary: SampleSummaryResponse
    collection_summary: CollectionSummaryResponse = Field(default_factory=CollectionSummaryResponse)
    template_report: TemplateReportResponse
    structured_sections: StructuredSectionsResponse
    wordcloud: WordcloudResponse
    artifacts: list[ArtifactItem] = Field(default_factory=list)
    ai_report: dict | None = None
    ai_available: bool
    qa_available: bool


class CommentDailyCountResponse(BaseModel):
    date: str
    count: int


class JobCommentSummaryResponse(BaseModel):
    job_id: str
    total_count: int
    dated_count: int
    undated_count: int
    date_min: str | None = None
    date_max: str | None = None
    daily_counts: list[CommentDailyCountResponse] = Field(default_factory=list)
    platform_counts: dict[str, int] = Field(default_factory=dict)


class JobCommentItemResponse(BaseModel):
    comment_id: str
    platform: str
    date: str
    model_name: str
    positive_text: str
    negative_text: str
    full_text: str


class JobCommentPageResponse(BaseModel):
    job_id: str
    start_date: str
    end_date: str
    total: int
    page: int
    page_size: int
    items: list[JobCommentItemResponse] = Field(default_factory=list)


class CreateTimeReportRequest(BaseModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class TimeReportDateRangeResponse(BaseModel):
    start_date: str
    end_date: str


class TimeReportArtifactResponse(BaseModel):
    name: str
    path: str
    type: str


class TimeReportResponse(BaseModel):
    report_id: str
    job_id: str
    model_name: str
    date_range: TimeReportDateRangeResponse
    status: str
    sample_count: int
    platform_counts: dict[str, int] = Field(default_factory=dict)
    source: str | None = None
    report_json: dict | None = None
    artifacts: list[TimeReportArtifactResponse] = Field(default_factory=list)
    zip_url: str
    error_code: str | None = None
    error_message: str | None = None
    queue_job_id: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class AdminRedisFailedJobItem(BaseModel):
    job_id: str
    status: str | None = None
    origin: str | None = None
    description: str | None = None


class AdminDbFailedJobItem(BaseModel):
    job_id: str
    query: str
    model_name: str
    current_stage: str
    created_at: datetime | None = None
    finished_at: datetime | None = None


class AdminFailedJobsResponse(BaseModel):
    redis_failed_jobs: list[AdminRedisFailedJobItem] = Field(default_factory=list)
    db_failed_jobs: list[AdminDbFailedJobItem] = Field(default_factory=list)
    redis_error: str | None = None


class AdminFailedJobsDeleteResponse(BaseModel):
    redis_removed_job_ids: list[str] = Field(default_factory=list)
    db_expired_job_ids: list[str] = Field(default_factory=list)
    deleted_artifact_dirs: list[str] = Field(default_factory=list)
    redis_error: str | None = None


class SeriesMutationRequest(BaseModel):
    query: str = Field(min_length=1, max_length=255)
    platform: Literal["autohome", "dongchedi"]
    series_id: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1)
    url: str | None = None
    title: str | None = Field(default=None, max_length=255)
    source: str | None = None

    @field_validator("query", "series_id", "operator", "reason", mode="before")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("url", "title", "source", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped or None


class SeriesActionRequest(BaseModel):
    operator: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1)

    @field_validator("operator", "reason", mode="before")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = str(value or "").strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class SeriesRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    query_key: str
    query: str
    platform: str
    series_id: str
    status: str
    url: str | None = None
    title: str | None = None
    source: str | None = None
    import_batch_id: int | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SeriesListResponse(BaseModel):
    items: list[SeriesRecordResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class SeriesImportRowResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    query: str | None = None
    platform: str | None = None
    series_id: str | None = None
    url: str | None = None
    title: str | None = None
    source: str | None = None
    status: str
    query_key: str | None = None
    error: str | None = None
    existing_value_json: dict | None = None
    incoming_value_json: dict | None = None


class SeriesImportPreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    summary: dict[str, int]
    rows: list[SeriesImportRowResponse] = Field(default_factory=list)


class SeriesAuditItemResponse(BaseModel):
    id: int
    record_id: int | None = None
    action: str
    operator: str
    reason: str
    old_value: dict
    new_value: dict
    created_at: datetime


class SeriesAuditResponse(BaseModel):
    items: list[SeriesAuditItemResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class SeriesAliasRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=255)
    canonical_query: str = Field(min_length=1, max_length=255)


class SeriesAliasResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alias_key: str
    alias: str
    canonical_query: str
    created_at: datetime
    updated_at: datetime


class SeriesAliasListResponse(BaseModel):
    items: list[SeriesAliasResponse] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class TimeReportListResponse(BaseModel):
    items: list[TimeReportResponse] = Field(default_factory=list)


class JobQARequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class JobQACitationResponse(BaseModel):
    chunk_id: str
    source_type: str
    text: str
    metadata: dict = Field(default_factory=dict)


class JobQAResponse(BaseModel):
    answer: str
    citations: list[JobQACitationResponse] = Field(default_factory=list)
    confidence: str
    insufficient_evidence: bool
    answer_source: str = "fallback"
    model_used: str | None = None
    llm_error: str | None = None
    follow_up_suggestions: list[str] = Field(default_factory=list)
