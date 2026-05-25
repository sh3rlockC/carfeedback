export type AccessVerifyResponse = {
  ok: boolean;
  passphrase_version: string;
};

export type PlatformCandidate = {
  series_id: string | null;
  url: string | null;
  title: string | null;
  source: string | null;
  evidence_url?: string | null;
  kind?: string | null;
  note?: string | null;
};

export type PlatformCandidateGroup = {
  best: PlatformCandidate | null;
  candidates: PlatformCandidate[];
};

export type VehicleResolveResponse = {
  query: string;
  autohome: PlatformCandidateGroup;
  dongchedi: PlatformCandidateGroup;
};

export type SelectedCandidates = {
  autohome: PlatformCandidate;
  dongchedi: PlatformCandidate;
};

export type CreateJobResponse = {
  job_id: string;
  status: string;
  current_stage: string;
  result_url: string;
};

export type StageStatusItem = {
  name: string;
  status: string;
  attempt_no: number;
  error_code: string | null;
  error_message: string | null;
  progress_percent: number | null;
  progress_message: string | null;
};

export type JobProgressResponse = {
  job_id: string;
  status: string;
  current_stage: string;
  degraded: boolean;
  overall_percent: number;
  stages: StageStatusItem[];
  message: string;
  estimated_remaining_seconds: number | null;
  estimated_remaining_minutes: number | null;
  eta_label: string;
  eta_confidence: string;
};

export type SampleSummary = {
  autohome_count: number;
  dcd_count: number;
};

export type CollectionPlatformSummary = {
  existing_count: number;
  new_count: number;
  total_count: number;
  pages_scanned: number;
  mode: string;
  stop_reason: string | null;
};

export type CollectionSummary = {
  autohome: CollectionPlatformSummary;
  dongchedi: CollectionPlatformSummary;
};

export type TemplateReport = {
  title: string;
  highlights: string[];
};

export type StructuredSections = {
  overview: Array<Record<string, string>>;
  compare: Array<Record<string, string>>;
  business: Array<Record<string, string>>;
  opportunities: Array<Record<string, string>>;
};

export type KeywordRankItem = {
  term: string;
  count: number;
};

export type KeywordRankings = {
  positive: KeywordRankItem[];
  negative: KeywordRankItem[];
  combined: KeywordRankItem[];
};

export type Wordcloud = {
  positive_image_url: string | null;
  negative_image_url: string | null;
  terms_excel_url: string | null;
  keyword_rankings: KeywordRankings;
};

export type ArtifactItem = {
  id: number;
  type: string;
  path: string;
  url: string;
  source_stage: string | null;
};

export type ReusableJobOption = {
  job_id: string;
  model_name: string;
  finished_at: string | null;
  source: string;
};

export type ComparisonVehicleOption = {
  query: string;
  resolve: VehicleResolveResponse;
  reuse_options: ReusableJobOption[];
};

export type ComparisonOptionsResponse = {
  vehicles: ComparisonVehicleOption[];
};

export type ComparisonVehicleInput = {
  query: string;
  model_name?: string | null;
  selected_candidates: SelectedCandidates;
  reuse_job_id?: string | null;
};

export type ComparisonCreateResponse = {
  comparison_id: string;
  status: string;
  current_stage: string;
  progress_url: string;
  result_url: string;
};

export type ComparisonVehicleProgress = {
  query: string;
  model_name: string;
  status: string;
  source_job_id: string | null;
  child_job_id: string | null;
  estimated_remaining_seconds: number | null;
  estimated_remaining_minutes: number | null;
  eta_label: string;
  eta_confidence: string;
  error_message: string | null;
};

export type ComparisonProgressResponse = {
  comparison_id: string;
  status: string;
  current_stage: string;
  degraded: boolean;
  overall_percent: number;
  estimated_remaining_seconds: number | null;
  estimated_remaining_minutes: number | null;
  eta_label: string;
  eta_confidence: string;
  vehicles: ComparisonVehicleProgress[];
  message: string;
};

export type ComparisonResultResponse = {
  comparison_id: string;
  status: string;
  degraded: boolean;
  retention_days: number;
  vehicle_count: number;
  report_json: Record<string, unknown>;
  artifacts: ArtifactItem[];
  zip_url: string;
};

export type JobResultResponse = {
  job_id: string;
  status: string;
  degraded: boolean;
  model_name: string;
  retention_days: number;
  sample_summary: SampleSummary;
  collection_summary: CollectionSummary;
  template_report: TemplateReport;
  structured_sections: StructuredSections;
  wordcloud: Wordcloud;
  artifacts: ArtifactItem[];
  ai_report: Record<string, unknown> | null;
  ai_available: boolean;
  qa_available: boolean;
};

export type CommentDailyCount = {
  date: string;
  count: number;
};

export type JobCommentSummaryResponse = {
  job_id: string;
  total_count: number;
  dated_count: number;
  undated_count: number;
  date_min: string | null;
  date_max: string | null;
  daily_counts: CommentDailyCount[];
  platform_counts: Record<string, number>;
};

export type JobCommentItem = {
  comment_id: string;
  platform: string;
  date: string;
  model_name: string;
  positive_text: string;
  negative_text: string;
  full_text: string;
};

export type JobCommentPageResponse = {
  job_id: string;
  start_date: string;
  end_date: string;
  total: number;
  page: number;
  page_size: number;
  items: JobCommentItem[];
};

export type CreateTimeReportRequest = {
  start_date: string;
  end_date: string;
};

export type TimeReportArtifact = {
  name: string;
  path: string;
  type: string;
};

export type TimeReportResponse = {
  report_id: string;
  job_id: string;
  model_name: string;
  date_range: {
    start_date: string;
    end_date: string;
  };
  status: string;
  sample_count: number;
  platform_counts: Record<string, number>;
  source: string | null;
  report_json: Record<string, unknown> | null;
  artifacts: TimeReportArtifact[];
  zip_url: string;
  error_code: string | null;
  error_message: string | null;
  queue_job_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type TimeReportListResponse = {
  items: TimeReportResponse[];
};

export type QaCitation = {
  chunk_id: string;
  source_type: string;
  text: string;
  metadata: Record<string, unknown>;
};

export type QaResponse = {
  answer: string;
  citations: QaCitation[];
  confidence: string;
  insufficient_evidence: boolean;
  answer_source: string;
  model_used: string | null;
  llm_error: string | null;
  follow_up_suggestions: string[];
};

export type QaRequest = {
  question: string;
};

export type TaskVehicle = {
  task_vehicle_id: number;
  position: number;
  query: string;
  model_name: string | null;
  autohome_series_id: string | null;
  dcd_series_id: string | null;
  status: string;
};

export type TaskEvent = {
  event_id: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string | null;
};

export type TaskArtifact = {
  artifact_id: number;
  artifact_type: string;
  path: string;
  downloadable: boolean;
  created_at: string | null;
};

export type TaskListItem = {
  task_id: string;
  task_type: "single" | "comparison";
  display_name: string;
  status: string;
  current_stage: string;
  degraded: boolean;
  upgraded_to_full: boolean;
  eta_seconds: number | null;
  eta_reason: string | null;
  created_at: string;
  completed_at: string | null;
};

export type TaskDetailResponse = TaskListItem & {
  vehicles: TaskVehicle[];
  events: TaskEvent[];
  artifacts: TaskArtifact[];
};

export type TaskCreateVehicle = {
  query: string;
};

export type TaskCreateRequest = {
  task_type: "single" | "comparison";
  vehicles: TaskCreateVehicle[];
};

export type TaskCreateResponse = {
  task_id: string;
  status: string;
  view_url: string;
  manage_url: string;
};

export type TaskLoadResponse = {
  running_task_count: number;
  queued_task_count: number;
  platforms: Record<string, { available: number; total: number }>;
};
