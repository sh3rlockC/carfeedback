"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { Fragment, useEffect, useState } from "react";
import { SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError, toJsonBody } from "@/lib/api";
import type {
  ArtifactItem,
  ComparisonResultResponse,
  CreateTimeReportRequest,
  JobCommentPageResponse,
  JobCommentSummaryResponse,
  JobResultResponse,
  KeywordRankItem,
  QaResponse,
  TimeReportListResponse,
  TimeReportResponse,
} from "@/lib/api-types";
import { clearFlowState, getFlowState, setFlowState } from "@/lib/flow-state";
import { withBasePath } from "@/lib/paths";

const statusLabels: Record<string, string> = {
  completed: "已完成",
  completed_degraded: "降级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

const confidenceLabels: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const answerSourceLabels: Record<string, string> = {
  llm: "LLM 生成",
  fallback: "规则兜底",
};

const timeReportStatusLabels: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  completed: "已完成",
  failed: "失败",
};

function labelFor(value: string, labels: Record<string, string>) {
  return labels[value] ?? value;
}

function asText(value: unknown, fallback = "") {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "number") {
    return String(value);
  }
  return fallback;
}

type ComparisonDimensionVehicle = {
  model_name: string;
  positive_mentions: number;
  negative_mentions: number;
};

type ComparisonDimensionRow = {
  dimension: string;
  winner_model_names: string[];
  winner_score: number | null;
  winner_label: string;
  vehicles: ComparisonDimensionVehicle[];
};

type ComparisonConclusion = {
  summary: string;
  rationale_bullets: string[];
  source: string;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function asOptionalNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function comparisonConclusion(report: Record<string, unknown> | undefined): ComparisonConclusion {
  const conclusion = asRecord(report?.conclusion);
  const bullets = Array.isArray(conclusion.rationale_bullets)
    ? conclusion.rationale_bullets.map((item) => asText(item)).filter(Boolean)
    : [];
  return {
    summary: asText(conclusion.summary, "对比结果生成后会显示在这里。"),
    rationale_bullets: bullets,
    source: asText(conclusion.source, "fallback"),
  };
}

function dimensionWinner(vehicles: ComparisonDimensionVehicle[]) {
  const scored = vehicles
    .map((vehicle) => {
      const total = vehicle.positive_mentions + vehicle.negative_mentions;
      return total > 0 ? { vehicle, score: vehicle.positive_mentions / total } : null;
    })
    .filter((item): item is { vehicle: ComparisonDimensionVehicle; score: number } => item !== null);
  if (!scored.length) {
    return { winner_model_names: [], winner_score: null, winner_label: "无数据" };
  }
  const bestScore = Math.max(...scored.map((item) => item.score));
  const scoreWinners = scored.filter((item) => Math.abs(item.score - bestScore) < 1e-12);
  const bestPositive = Math.max(...scoreWinners.map((item) => item.vehicle.positive_mentions));
  const winners = scoreWinners
    .filter((item) => item.vehicle.positive_mentions === bestPositive)
    .map((item) => item.vehicle.model_name || "车型");
  return {
    winner_model_names: winners,
    winner_score: bestScore,
    winner_label: winners.join("、"),
  };
}

function comparisonDimensions(report: Record<string, unknown> | undefined): ComparisonDimensionRow[] {
  const rows = Array.isArray(report?.dimensions) ? report.dimensions : [];
  return rows
    .map((row) => {
      const record = asRecord(row);
      const vehicles = Array.isArray(record.vehicles) ? record.vehicles : [];
      const parsedVehicles = vehicles.map((vehicle) => {
        const item = asRecord(vehicle);
        return {
          model_name: asText(item.model_name),
          positive_mentions: asNumber(item.positive_mentions),
          negative_mentions: asNumber(item.negative_mentions),
        };
      });
      const fallbackWinner = dimensionWinner(parsedVehicles);
      const winnerNames = Array.isArray(record.winner_model_names)
        ? record.winner_model_names.map((item) => asText(item)).filter(Boolean)
        : fallbackWinner.winner_model_names;
      return {
        dimension: asText(record.dimension),
        winner_model_names: winnerNames,
        winner_score: asOptionalNumber(record.winner_score) ?? fallbackWinner.winner_score,
        winner_label: asText(record.winner_label, fallbackWinner.winner_label),
        vehicles: parsedVehicles,
      };
    })
    .filter((row) => row.dimension && row.vehicles.length);
}

function isBusinessDownloadArtifact(artifact: ArtifactItem) {
  const lowerPath = artifact.path.toLowerCase();
  return lowerPath.endsWith(".xlsx") || lowerPath.endsWith(".png");
}

function artifactFileName(artifact: ArtifactItem) {
  return artifact.path.split("/").pop() || artifact.path;
}

function statusTone(status: string): "default" | "success" | "warning" | "danger" | "accent" {
  if (status === "completed") {
    return "success";
  }
  if (status === "failed") {
    return "danger";
  }
  if (status === "queued" || status === "running") {
    return "warning";
  }
  return "default";
}

function formatPlatformCounts(counts: Record<string, number>) {
  const entries = Object.entries(counts);
  if (!entries.length) {
    return "暂无平台样本";
  }
  return entries.map(([platform, count]) => `${platform} ${count}`).join(" / ");
}

function reportText(report: Record<string, unknown> | null, keys: string[], fallback = "") {
  if (!report) {
    return fallback;
  }

  for (const key of keys) {
    const text = asText(report[key]);
    if (text) {
      return text;
    }
  }

  return fallback;
}

function reportList(report: Record<string, unknown> | null, keys: string[], fallback: string[]) {
  if (!report) {
    return fallback;
  }

  for (const key of keys) {
    const value = report[key];
    if (Array.isArray(value)) {
      const items = value.map((item) => asText(item)).filter(Boolean);
      if (items.length) {
        return items;
      }
    }
  }

  return fallback;
}

function KeywordRankList({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "positive" | "negative" | "combined";
  items: KeywordRankItem[];
}) {
  const maxCount = Math.max(...items.map((item) => item.count), 0);

  return (
    <div className={`keyword-rank-card keyword-rank-${tone}`}>
      <div className="keyword-rank-head">
        <h4>{title}</h4>
        <span>{items.length ? `Top ${items.length}` : "暂无数据"}</span>
      </div>

      {items.length ? (
        <div className="keyword-rank-list">
          {items.map((item, index) => {
            const width = maxCount > 0 ? Math.max(6, Math.round((item.count / maxCount) * 100)) : 0;

            return (
              <div className="keyword-rank-row" key={`${tone}-${item.term}-${index}`}>
                <span className="keyword-rank-index">{String(index + 1).padStart(2, "0")}</span>
                <div className="keyword-rank-main">
                  <div className="keyword-rank-label">
                    <span>{item.term}</span>
                    <strong>{item.count}</strong>
                  </div>
                  <div className="keyword-rank-track" aria-hidden="true">
                    <span style={{ width: `${width}%` }} />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="status-copy">当前结果未返回该榜单词项。</p>
      )}
    </div>
  );
}

export default function ResultPage() {
  const router = useRouter();
  const flowState = getFlowState();
  const [ready, setReady] = useState(false);
  const [result, setResult] = useState<JobResultResponse | null>(null);
  const [comparisonResult, setComparisonResult] = useState<ComparisonResultResponse | null>(null);
  const [error, setError] = useState("");
  const [qaQuestion, setQaQuestion] = useState("");
  const [qaResult, setQaResult] = useState<QaResponse | null>(null);
  const [qaLoading, setQaLoading] = useState(false);
  const [qaError, setQaError] = useState("");
  const [commentSummary, setCommentSummary] = useState<JobCommentSummaryResponse | null>(null);
  const [commentPreview, setCommentPreview] = useState<JobCommentPageResponse | null>(null);
  const [timeReports, setTimeReports] = useState<TimeReportResponse[]>([]);
  const [timeStart, setTimeStart] = useState("");
  const [timeEnd, setTimeEnd] = useState("");
  const [timeContextLoading, setTimeContextLoading] = useState(false);
  const [timePreviewLoading, setTimePreviewLoading] = useState(false);
  const [creatingTimeReport, setCreatingTimeReport] = useState(false);
  const [timeError, setTimeError] = useState("");
  const activeTimeReportCount = timeReports.filter((report) => report.status === "queued" || report.status === "running").length;

  useEffect(() => {
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) {
      return;
    }

    if (flowState.mode === "comparison") {
      if (!flowState.accessVersion || !flowState.comparisonId) {
        return;
      }

      let cancelled = false;
      const loadComparisonResult = async () => {
        try {
          const payload = await apiRequest<ComparisonResultResponse>(`/api/comparisons/${flowState.comparisonId}`);
          if (cancelled) {
            return;
          }
          setComparisonResult(payload);
          setFlowState({
            comparisonId: payload.comparison_id,
            comparisonProgress: {
              comparison_id: payload.comparison_id,
              status: payload.status,
              current_stage: payload.status,
              degraded: payload.degraded,
              overall_percent: 100,
              estimated_remaining_seconds: 0,
              estimated_remaining_minutes: 0,
              eta_label: "预计剩余 0 分钟",
              eta_confidence: "done",
              vehicles: [],
              message: labelFor(payload.status, statusLabels),
            },
          });
        } catch (err) {
          if (!cancelled) {
            setError(err instanceof ApiError ? err.message : "无法读取竞品对比结果。");
          }
        }
      };

      void loadComparisonResult();
      return () => {
        cancelled = true;
      };
    }

    if (!flowState.accessVersion || !flowState.jobId) {
      return;
    }

    let cancelled = false;

    const loadResult = async () => {
      try {
        const payload = await apiRequest<JobResultResponse>(`/api/jobs/${flowState.jobId}/result`);
        if (cancelled) {
          return;
        }

        setResult(payload);
        setFlowState({
          jobId: payload.job_id,
          jobProgress: {
            job_id: payload.job_id,
            status: payload.status,
            current_stage: payload.status,
            degraded: payload.degraded,
            overall_percent: 100,
            stages: [],
            message: labelFor(payload.status, statusLabels),
            estimated_remaining_seconds: 0,
            estimated_remaining_minutes: 0,
            eta_label: "预计剩余 0 分钟",
            eta_confidence: "done",
          },
        });
      } catch (err) {
        if (cancelled) {
          return;
        }

        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("无法读取任务结果。");
        }
      }
    };

    void loadResult();

    return () => {
      cancelled = true;
    };
  }, [flowState.accessVersion, flowState.comparisonId, flowState.jobId, flowState.mode, ready]);

  useEffect(() => {
    if (!ready || !flowState.accessVersion || !flowState.jobId || !result || result.status === "expired") {
      return;
    }

    let cancelled = false;

    const loadTimeContext = async () => {
      setTimeContextLoading(true);
      setTimeError("");

      try {
        const [summaryPayload, reportsPayload] = await Promise.all([
          apiRequest<JobCommentSummaryResponse>(`/api/jobs/${flowState.jobId}/comments/summary`),
          apiRequest<TimeReportListResponse>(`/api/jobs/${flowState.jobId}/time-reports`),
        ]);
        if (cancelled) {
          return;
        }

        setCommentSummary(summaryPayload);
        setTimeReports(reportsPayload.items);
        setTimeStart((current) => current || summaryPayload.date_min || "");
        setTimeEnd((current) => current || summaryPayload.date_max || "");
      } catch (err) {
        if (cancelled) {
          return;
        }
        setTimeError(err instanceof ApiError ? err.message : "无法读取时间范围评论。");
      } finally {
        if (!cancelled) {
          setTimeContextLoading(false);
        }
      }
    };

    void loadTimeContext();

    return () => {
      cancelled = true;
    };
  }, [ready, flowState.accessVersion, flowState.jobId, result]);

  useEffect(() => {
    if (!ready || !flowState.accessVersion || !flowState.jobId || !result || result.status === "expired") {
      return;
    }

    if (!timeStart || !timeEnd) {
      setCommentPreview(null);
      return;
    }

    let cancelled = false;

    const loadPreview = async () => {
      setTimePreviewLoading(true);
      try {
        const payload = await apiRequest<JobCommentPageResponse>(
          `/api/jobs/${flowState.jobId}/comments?start_date=${encodeURIComponent(timeStart)}&end_date=${encodeURIComponent(timeEnd)}&page=1&page_size=20`
        );
        if (!cancelled) {
          setCommentPreview(payload);
        }
      } catch (err) {
        if (!cancelled) {
          setCommentPreview(null);
          setTimeError(err instanceof ApiError ? err.message : "无法读取脱敏评论预览。");
        }
      } finally {
        if (!cancelled) {
          setTimePreviewLoading(false);
        }
      }
    };

    void loadPreview();

    return () => {
      cancelled = true;
    };
  }, [ready, flowState.accessVersion, flowState.jobId, result, timeStart, timeEnd]);

  useEffect(() => {
    if (!ready || !flowState.accessVersion || !flowState.jobId || activeTimeReportCount === 0) {
      return;
    }

    const jobId = flowState.jobId;
    const timer = window.setInterval(() => {
      void loadTimeReports(jobId);
    }, 3000);

    return () => {
      window.clearInterval(timer);
    };
  }, [ready, flowState.accessVersion, flowState.jobId, activeTimeReportCount]);

  async function loadTimeReports(jobId: string) {
    const payload = await apiRequest<TimeReportListResponse>(`/api/jobs/${jobId}/time-reports`);
    setTimeReports(payload.items);
  }

  async function createTimeReport() {
    if (!flowState.jobId || !timeStart || !timeEnd) {
      return;
    }

    setCreatingTimeReport(true);
    setTimeError("");

    const payload: CreateTimeReportRequest = {
      start_date: timeStart,
      end_date: timeEnd,
    };

    try {
      const report = await apiRequest<TimeReportResponse>(`/api/jobs/${flowState.jobId}/time-reports`, {
        method: "POST",
        body: toJsonBody(payload),
      });
      setTimeReports((current) => [report, ...current.filter((item) => item.report_id !== report.report_id)]);
      await loadTimeReports(flowState.jobId);
    } catch (err) {
      setTimeError(err instanceof ApiError ? err.message : "无法创建时间范围一页纸任务。");
    } finally {
      setCreatingTimeReport(false);
    }
  }

  async function askQuestion(question: string) {
    const trimmed = question.trim();
    if (!trimmed || !flowState.jobId) {
      return;
    }

    if (!result?.qa_available) {
      setQaError("当前结果还不能使用问答。");
      return;
    }

    setQaLoading(true);
    setQaError("");

    try {
      const payload = await apiRequest<QaResponse>(`/api/jobs/${flowState.jobId}/qa`, {
        method: "POST",
        body: toJsonBody({ question: trimmed }),
      });

      setQaResult(payload);
    } catch (err) {
      if (err instanceof ApiError) {
        setQaError(err.message);
      } else {
        setQaError("无法获取问答结果。");
      }
    } finally {
      setQaLoading(false);
    }
  }

  function handleQaSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void askQuestion(qaQuestion);
  }

  if (!ready) {
    return <main className="panel guard">正在加载...</main>;
  }

  if (flowState.mode === "comparison") {
    if (!flowState.accessVersion || !flowState.comparisonId) {
      return (
        <main className="panel guard">
          <p className="eyebrow">第 5 步 / 共 5 步</p>
          <h2>需要先创建竞品对比任务</h2>
          <p className="helper">请先创建并完成竞品对比任务，再查看结果页。</p>
          <div className="actions">
            <Link className="button" href="/tasks/new">
              前往新建任务
            </Link>
          </div>
        </main>
      );
    }

    const comparison = comparisonResult;
    const reportJson = comparison?.report_json;
    const vehicles = Array.isArray(reportJson?.vehicles) ? reportJson.vehicles : [];
    const dimensions = comparisonDimensions(reportJson);
    const conclusion = comparisonConclusion(reportJson);
    const comparisonArtifacts = (comparison?.artifacts ?? []).filter(isBusinessDownloadArtifact);
    const dimensionExcel = comparisonArtifacts.find((artifact) => artifactFileName(artifact) === "comparison_dimension_matrix.xlsx");
    const headline = asText(comparison?.report_json?.headline, "竞品口碑对比");
    const summary = conclusion.summary;

    return (
      <main className="stack-lg">
        <SignalPanel tone="accent" className="stack-lg">
          <div className="meta-row">
            <StatusPill tone={comparison?.status === "completed" ? "success" : comparison?.status === "failed" ? "danger" : "warning"}>
              {comparison ? labelFor(comparison.status, statusLabels) : "读取中"}
            </StatusPill>
            <StatusPill tone={comparison?.degraded ? "warning" : "success"}>{comparison?.degraded ? "降级完成" : "完整结果"}</StatusPill>
          </div>
          <div>
            <p className="eyebrow">COMPETITOR COMPARISON</p>
            <h2>{headline}</h2>
            <p className="helper">{summary}</p>
          </div>
          {conclusion.rationale_bullets.length ? (
            <ul className="conclusion-list">
              {conclusion.rationale_bullets.map((item, index) => (
                <li key={`${item}-${index}`}>{item}</li>
              ))}
            </ul>
          ) : null}
          {error ? <p className="error">{error}</p> : null}
        </SignalPanel>

        <div className="pipeline-strip">
          {vehicles.map((vehicle, index) => {
            const item = vehicle as Record<string, unknown>;
            return (
              <div className="pipeline-node" key={`${asText(item.model_name, "车型")}-${index}`}>
                <strong>{asText(item.model_name, `车型 ${index + 1}`)}</strong>
                <StatusPill tone="accent">{asText(item.comment_fact_count, "0")} 条事实</StatusPill>
                <p className="field-hint" style={{ marginTop: 10 }}>
                  {asText(item.headline, "暂无摘要")}
                </p>
              </div>
            );
          })}
        </div>

        <div className="card">
          <div className="section-header compact">
            <p className="eyebrow">DIMENSION MATRIX</p>
            <h3>多车型多维度提及数对比</h3>
            <p className="field-hint">同一条评论在同一维度的优点或槽点最多计 1 次。</p>
          </div>
          {dimensions.length ? (
            <div className="comparison-table-wrap">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th rowSpan={2}>维度</th>
                    <th rowSpan={2}>车型对比胜者</th>
                    {dimensions[0].vehicles.map((vehicle) => (
                      <th colSpan={2} key={vehicle.model_name || "车型"}>
                        {vehicle.model_name || "车型"}
                      </th>
                    ))}
                  </tr>
                  <tr>
                    {dimensions[0].vehicles.map((vehicle) => (
                      <Fragment key={`${vehicle.model_name}-heads`}>
                        <th className="mention-positive">优点提及数</th>
                        <th className="mention-negative">槽点提及数</th>
                      </Fragment>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {dimensions.map((row) => (
                    <tr key={row.dimension}>
                      <th>{row.dimension}</th>
                      <td className="winner-cell">{row.winner_label}</td>
                      {row.vehicles.map((vehicle) => (
                        <Fragment key={`${row.dimension}-${vehicle.model_name}`}>
                          <td className="mention-positive">{vehicle.positive_mentions}</td>
                          <td className="mention-negative">{vehicle.negative_mentions}</td>
                        </Fragment>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="status-copy">当前对比结果暂未返回维度矩阵。</p>
          )}
        </div>

        <div className="card">
          <h3>结果下载</h3>
          <p className="field-hint">
            下载包仅包含 Excel、词云和关键词图片等业务产物；内部 JSON 仅用于页面展示和复用判断，不进入下载包。
          </p>
          <div className="actions" style={{ marginTop: 18 }}>
            {comparison ? (
              <a className="button" href={withBasePath(comparison.zip_url)}>
                下载对比 ZIP
              </a>
            ) : null}
            {dimensionExcel ? (
              <a className="button secondary" href={withBasePath(dimensionExcel.url)}>
                下载多维度 Excel
              </a>
            ) : null}
            <button
              className="button secondary"
              type="button"
              onClick={() => {
                clearFlowState();
                router.push("/tasks/new");
              }}
            >
              重新开始
            </button>
          </div>
        </div>
      </main>
    );
  }

  if (!flowState.accessVersion || !flowState.jobId) {
    return (
      <main className="panel guard">
        <p className="eyebrow">第 5 步 / 共 5 步</p>
        <h2>需要先创建任务</h2>
        <p className="helper">请先创建并完成任务，再查看结果页。</p>
        <div className="actions">
          <Link className="button" href="/tasks/new">
            前往新建任务
          </Link>
        </div>
      </main>
    );
  }

  if (error && !result) {
    return (
      <main className="panel guard">
        <p className="eyebrow">第 5 步 / 共 5 步</p>
        <h2>结果暂不可用</h2>
        <p className="helper">{error}</p>
        <div className="actions">
          <Link className="button" href="/progress">
            返回进度页
          </Link>
        </div>
      </main>
    );
  }

  if (!result) {
    return <main className="panel guard">正在加载结果...</main>;
  }

  const resultBundleUrl = withBasePath(`/api/jobs/${result.job_id}/artifacts.zip`);
  const downloadableCount = result.artifacts.filter((artifact) =>
    artifact.path.toLowerCase().endsWith(".xlsx") || artifact.path.toLowerCase().endsWith(".png")
  ).length;
  const vehicleName = flowState.vehicleQuery || result.model_name || result.template_report.title || "当前车型";
  const totalSamples = result.sample_summary.autohome_count + result.sample_summary.dcd_count;
  const aiHeadline = reportText(result.ai_report, ["headline", "title"], "智能一页纸");
  const executiveSummary = reportText(
    result.ai_report,
    ["executive_summary", "summary", "conclusion"],
    "当前任务已完成采集与结构化处理，可继续查看模板摘要、下载结果包或围绕当前结果提问。"
  );
  const bossBrief = reportList(
    result.ai_report,
    ["boss_brief", "key_findings", "findings", "highlights"],
    result.template_report.highlights.slice(0, 4)
  );
  const actionItems = reportList(result.ai_report, ["action_items", "recommendations", "next_steps"], []);
  const keywordRankings = result.wordcloud.keyword_rankings ?? { positive: [], negative: [], combined: [] };
  const maxDailyCommentCount = Math.max(...(commentSummary?.daily_counts.map((item) => item.count) ?? []), 0);
  const previewTotal = commentPreview?.total ?? 0;
  const primaryConclusion =
    result.template_report.highlights[0] || "本次分析结果已生成，可查看指标摘要和下载交付物。";
  const sectionSummary = [
    ...result.structured_sections.overview,
    ...result.structured_sections.compare,
    ...result.structured_sections.business,
    ...result.structured_sections.opportunities,
  ];
  const dimensionSummaryItems = sectionSummary.slice(0, 3);
  const topKeywords = [
    ...keywordRankings.positive.slice(0, 2),
    ...keywordRankings.negative.slice(0, 2),
    ...keywordRankings.combined.slice(0, 2),
  ].slice(0, 5);
  const previewSamples = commentPreview?.items.slice(0, 2) ?? [];

  return (
    <main className="result-dashboard-page">
      <section className="result-dashboard-hero">
        <div className="result-conclusion-card">
          <p className="eyebrow">RESULT DASHBOARD</p>
          <h1>{vehicleName || result.model_name}</h1>
          <p>{primaryConclusion}</p>
          <div className="meta-row">
            <StatusPill tone={result.degraded ? "warning" : "success"}>
              {labelFor(result.status, statusLabels)}
            </StatusPill>
            <StatusPill tone={result.ai_available ? "success" : "warning"}>
              AI 一页纸：{result.ai_available ? "可用" : "降级"}
            </StatusPill>
            <StatusPill tone={result.qa_available ? "success" : "warning"}>
              问答：{result.qa_available ? "已启用" : "未启用"}
            </StatusPill>
          </div>
        </div>

        <aside className="result-delivery-panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">DELIVERY</p>
              <h2>交付物</h2>
            </div>
            <StatusPill tone={downloadableCount ? "success" : "warning"}>{downloadableCount} 个文件</StatusPill>
          </div>
          <div className="download-list">
            {result.status === "expired" ? (
              <p className="status-copy">该任务结果已过期，请重新创建任务。</p>
            ) : downloadableCount ? (
              <a className="download-link primary-download" href={resultBundleUrl}>
                下载全部结果 ZIP
              </a>
            ) : (
              <p className="status-copy">暂无可打包下载的结果文件。</p>
            )}
            {result.artifacts.filter(isBusinessDownloadArtifact).slice(0, 4).map((artifact) => (
              <a className="download-link" href={withBasePath(artifact.url)} key={artifact.id}>
                {artifactFileName(artifact)}
              </a>
            ))}
          </div>
        </aside>
      </section>

      <section className="metric-grid" aria-label="结果指标摘要">
        <div className="metric-tile">
          <span className="metric-icon">总</span>
          <span>累计评论</span>
          <strong>{totalSamples}</strong>
          <p>双平台可分析样本</p>
        </div>
        <div className="metric-tile">
          <span className="metric-icon">家</span>
          <span>汽车之家</span>
          <strong>{result.sample_summary.autohome_count}</strong>
          <p>{result.collection_summary.autohome.pages_scanned} 页扫描</p>
        </div>
        <div className="metric-tile">
          <span className="metric-icon">懂</span>
          <span>懂车帝</span>
          <strong>{result.sample_summary.dcd_count}</strong>
          <p>{result.collection_summary.dongchedi.pages_scanned} 页扫描</p>
        </div>
        <div className="metric-tile">
          <span className="metric-icon">态</span>
          <span>报告状态</span>
          <strong>{labelFor(result.status, statusLabels)}</strong>
          <p>{result.degraded ? "降级链路" : "完整链路"}</p>
        </div>
      </section>

      <section className="result-dashboard-grid" aria-label="摘要入口">
        <article className="flat-panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">MATRIX</p>
              <h2>维度矩阵摘要</h2>
            </div>
            <StatusPill>{sectionSummary.length} 项</StatusPill>
          </div>
          <div className="dashboard-summary-list">
            {dimensionSummaryItems.length ? (
              dimensionSummaryItems.map((item, index) => {
                const values = Object.values(item).filter(Boolean);
                return <p key={`${values.join("-")}-${index}`}>{values.join(" / ")}</p>;
              })
            ) : (
              <p className="status-copy">结构化摘要将在报告生成后展示。</p>
            )}
          </div>
        </article>

        <article className="flat-panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">KEYWORDS</p>
              <h2>关键词榜单</h2>
            </div>
            <StatusPill tone="accent">Top {topKeywords.length}</StatusPill>
          </div>
          <div className="keyword-chip-list">
            {topKeywords.length ? (
              topKeywords.map((item, index) => (
                <span key={`${item.term}-${index}`}>
                  {item.term}
                  <strong>{item.count}</strong>
                </span>
              ))
            ) : (
              <p className="status-copy">暂无关键词词项。</p>
            )}
          </div>
        </article>

        <article className="flat-panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">SAMPLES</p>
              <h2>评论样本</h2>
            </div>
            <StatusPill>{previewTotal} 条</StatusPill>
          </div>
          <div className="dashboard-summary-list">
            {previewSamples.length ? (
              previewSamples.map((comment) => (
                <p key={comment.comment_id}>
                  <strong>{comment.platform}</strong>
                  {comment.positive_text || comment.negative_text || comment.full_text || "暂无评论正文"}
                </p>
              ))
            ) : (
              <p className="status-copy">选择时间范围后可查看脱敏评论样本。</p>
            )}
          </div>
        </article>
      </section>

      <section className="insight-layout">
        <article className="executive-report">
          <div className="report-kicker">
            <StatusPill tone="accent">智能一页纸</StatusPill>
            <StatusPill>{result.model_name}</StatusPill>
          </div>
          <h3>{aiHeadline}</h3>
          <p className="executive-summary">{executiveSummary}</p>

          <div className="brief-grid">
            {bossBrief.map((item, index) => (
              <div key={`${item}-${index}`} className="brief-card">
                <span>{String(index + 1).padStart(2, "0")}</span>
                <p>{item}</p>
              </div>
            ))}
          </div>

          {actionItems.length ? (
            <div className="action-strip">
              <h4>建议动作</h4>
              {actionItems.slice(0, 4).map((item) => (
                <p key={item}>{item}</p>
              ))}
            </div>
          ) : null}
        </article>

        <aside className="artifact-column">
          <div className="artifact-card">
            <h3>结构化产物</h3>
            <div className="artifact-matrix">
              <div>
                <span>总览摘要</span>
                <strong>{result.structured_sections.overview.length}</strong>
              </div>
              <div>
                <span>跨平台对比</span>
                <strong>{result.structured_sections.compare.length}</strong>
              </div>
              <div>
                <span>业务摘要</span>
                <strong>{result.structured_sections.business.length}</strong>
              </div>
              <div>
                <span>机会点</span>
                <strong>{result.structured_sections.opportunities.length}</strong>
              </div>
            </div>
          </div>

          <div className="artifact-card template-card">
            <h3>{result.template_report.title || "模板一页纸"}</h3>
            <div className="timeline">
              {result.template_report.highlights.map((item) => (
                <div key={item} className="timeline-item">
                  <span className="timeline-dot" />
                  <p>{item}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="artifact-card wordcloud-card">
            <h3>词云预览</h3>
            {result.wordcloud.positive_image_url || result.wordcloud.negative_image_url ? (
              <div className="wordcloud-preview">
                {result.wordcloud.positive_image_url ? (
                  <img src={withBasePath(result.wordcloud.positive_image_url)} alt="优点词云" />
                ) : null}
                {result.wordcloud.negative_image_url ? (
                  <img src={withBasePath(result.wordcloud.negative_image_url)} alt="槽点词云" />
                ) : null}
              </div>
            ) : (
              <p className="status-copy">当前结果未返回词云图片预览。</p>
            )}
          </div>
        </aside>
      </section>

      <section className="keyword-rank-section">
        <div className="keyword-rank-section-head">
          <div>
            <p className="eyebrow">WORD RANK</p>
            <h3>关键词出现次数排名</h3>
          </div>
          <p>按词云词项清单中的出现次数排序，分别查看优点、槽点和全量关键词的高频关注点。</p>
        </div>

        <div className="keyword-rank-grid">
          <KeywordRankList title="优点关键词" tone="positive" items={keywordRankings.positive} />
          <KeywordRankList title="槽点关键词" tone="negative" items={keywordRankings.negative} />
          <KeywordRankList title="全部关键词" tone="combined" items={keywordRankings.combined} />
        </div>
      </section>

      <section className="time-report-section">
        <div className="time-report-head">
          <div>
            <p className="eyebrow">TIME RANGE</p>
            <h3>按时间生成一页纸</h3>
            <p>先按日期预览脱敏评论，再生成当前车型的时间版一页纸；历史版本不会覆盖全量报告。</p>
          </div>
          <div className="meta-row">
            <StatusPill tone="accent">{result.model_name}</StatusPill>
            <StatusPill tone={activeTimeReportCount ? "warning" : "default"}>
              {activeTimeReportCount ? `生成中 ${activeTimeReportCount}` : "无运行任务"}
            </StatusPill>
          </div>
        </div>

        <div className="time-report-controls">
          <div className="field">
            <label htmlFor="time-start">开始日期</label>
            <input
              id="time-start"
              type="date"
              min={commentSummary?.date_min ?? undefined}
              max={commentSummary?.date_max ?? undefined}
              value={timeStart}
              onChange={(event) => {
                setTimeStart(event.target.value);
                setTimeError("");
              }}
              disabled={timeContextLoading || !commentSummary?.date_min}
            />
          </div>
          <div className="field">
            <label htmlFor="time-end">结束日期</label>
            <input
              id="time-end"
              type="date"
              min={commentSummary?.date_min ?? undefined}
              max={commentSummary?.date_max ?? undefined}
              value={timeEnd}
              onChange={(event) => {
                setTimeEnd(event.target.value);
                setTimeError("");
              }}
              disabled={timeContextLoading || !commentSummary?.date_max}
            />
          </div>
          <div className="time-report-submit">
            <button
              className="button"
              type="button"
              onClick={() => void createTimeReport()}
              disabled={creatingTimeReport || timePreviewLoading || !timeStart || !timeEnd || previewTotal === 0}
            >
              {creatingTimeReport ? "正在创建" : "生成一页纸"}
            </button>
          </div>
        </div>

        {timeError ? <p className="error">{timeError}</p> : null}

        <div className="time-report-body">
          <div className="time-report-preview">
            <div className="time-preview-head">
              <div>
                <h4>评论预览</h4>
                <p>{timePreviewLoading ? "正在加载..." : `当前范围 ${previewTotal} 条可分析评论`}</p>
              </div>
              {commentSummary ? (
                <div className="meta-row">
                  <StatusPill>总量 {commentSummary.total_count}</StatusPill>
                  <StatusPill>有日期 {commentSummary.dated_count}</StatusPill>
                  <StatusPill tone={commentSummary.undated_count ? "warning" : "success"}>
                    无日期 {commentSummary.undated_count}
                  </StatusPill>
                </div>
              ) : null}
            </div>

            {commentSummary?.daily_counts.length ? (
              <div className="time-distribution" aria-label="按天评论数量">
                {commentSummary.daily_counts.slice(0, 28).map((item) => {
                  const height = maxDailyCommentCount > 0 ? Math.max(12, Math.round((item.count / maxDailyCommentCount) * 58)) : 12;
                  return (
                    <span
                      key={item.date}
                      title={`${item.date}：${item.count} 条`}
                      style={{ height: `${height}px` }}
                    />
                  );
                })}
              </div>
            ) : null}

            <div className="comment-preview-list">
              {commentPreview?.items.length ? (
                commentPreview.items.map((comment) => (
                  <article className="comment-preview-card" key={comment.comment_id}>
                    <div className="comment-preview-meta">
                      <strong>{comment.platform}</strong>
                      <span>{comment.date}</span>
                      <span>{comment.model_name}</span>
                    </div>
                    {comment.positive_text ? (
                      <p>
                        <strong>最满意：</strong>
                        {comment.positive_text}
                      </p>
                    ) : null}
                    {comment.negative_text ? (
                      <p>
                        <strong>最不满意：</strong>
                        {comment.negative_text}
                      </p>
                    ) : null}
                    {comment.full_text ? (
                      <p>
                        <strong>评价全文：</strong>
                        {comment.full_text}
                      </p>
                    ) : null}
                  </article>
                ))
              ) : (
                <p className="status-copy">
                  {timePreviewLoading ? "正在加载脱敏评论..." : "该时间范围无可分析评论。"}
                </p>
              )}
            </div>
          </div>

          <div className="time-report-history">
            <div className="time-preview-head">
              <div>
                <h4>时间版历史</h4>
                <p>{timeReports.length ? `已生成或排队 ${timeReports.length} 个版本` : "暂无时间版报告"}</p>
              </div>
            </div>

            <div className="time-report-list">
              {timeReports.length ? (
                timeReports.map((report) => {
                  const headline = reportText(report.report_json, ["headline", "title"], "");
                  const summary = reportText(report.report_json, ["executive_summary", "summary"], "");
                  return (
                    <article className="time-report-card" key={report.report_id}>
                      <div className="time-report-card-head">
                        <div>
                          <strong>
                            {report.date_range.start_date} 至 {report.date_range.end_date}
                          </strong>
                          <span>{formatPlatformCounts(report.platform_counts)}</span>
                        </div>
                        <StatusPill tone={statusTone(report.status)}>
                          {labelFor(report.status, timeReportStatusLabels)}
                        </StatusPill>
                      </div>
                      <div className="time-report-card-meta">
                        <span>{report.sample_count} 条样本</span>
                        {report.source ? <span>{report.source}</span> : null}
                        <span>{new Date(report.created_at).toLocaleString("zh-CN")}</span>
                      </div>
                      {headline ? <h4>{headline}</h4> : null}
                      {summary ? <p>{summary}</p> : null}
                      {report.error_message ? <p className="error">{report.error_message}</p> : null}
                      <div className="actions">
                        {report.status === "completed" ? (
                          <a className="button secondary" href={withBasePath(report.zip_url)}>
                            下载 ZIP
                          </a>
                        ) : null}
                      </div>
                    </article>
                  );
                })
              ) : (
                <p className="status-copy">选择日期范围并生成后，这里会展示该车型的时间版一页纸。</p>
              )}
            </div>
          </div>
        </div>
      </section>

      <div className="result-actions">
        <div>
          <p className="eyebrow">NEXT</p>
          <h3>继续追问，或重新发起下一辆车</h3>
          <p>可以下载结果包、继续追问，或清空当前流程后发起下一辆车。</p>
        </div>
        <div className="actions">
          {result.status === "expired" ? (
            <Link className="button secondary" href="/tasks/new">
              重新创建任务
            </Link>
          ) : downloadableCount ? (
            <a className="button" href={resultBundleUrl}>
              下载结果包
            </a>
          ) : null}
            <button
              className="button secondary"
              type="button"
              onClick={() => {
                clearFlowState();
                router.push("/tasks/new");
              }}
            >
              重新开始
            </button>
        </div>
      </div>

      <SignalPanel tone="accent" className="qa-panel">
        <div className="qa-header">
          <div>
            <div className="meta-row">
              <StatusPill tone="accent">智能问答</StatusPill>
              <StatusPill tone={result.qa_available ? "success" : "warning"}>
                {result.qa_available ? "已启用" : "未启用"}
              </StatusPill>
            </div>
            <h3>围绕当前任务提问</h3>
            <p className="status-copy">回答由当前任务摘要和智能一页纸提供上下文，不显示引用证据。</p>
          </div>
        </div>

        <div className="qa-layout">
          <form className="stack" onSubmit={handleQaSubmit}>
            <div className="field">
              <label htmlFor="qa-question">提出问题</label>
              <input
                id="qa-question"
                value={qaQuestion}
                onChange={(event) => {
                  setQaQuestion(event.target.value);
                  if (qaError) {
                    setQaError("");
                  }
                }}
                placeholder="例如：这款车的主要槽点集中在哪些方面？"
                disabled={!result.qa_available || qaLoading}
              />
              <p className="field-hint">建议问卖点、槽点、平台差异、产品动作或老板汇报。</p>
            </div>

            {qaError ? <p className="error">{qaError}</p> : null}
            {qaLoading ? <p className="status-copy">正在生成回答...</p> : null}

            <div className="actions">
              <button
                className="button"
                type="submit"
                disabled={!result.qa_available || qaLoading || !qaQuestion.trim()}
              >
                {qaLoading ? "正在提问" : "提交问题"}
              </button>
            </div>
          </form>

          <div className="qa-result">
            {qaResult ? (
              <>
                <div className="meta-row">
                  <StatusPill>置信度：{labelFor(qaResult.confidence, confidenceLabels)}</StatusPill>
                  <StatusPill tone={qaResult.answer_source === "llm" ? "success" : "warning"}>
                    来源：{labelFor(qaResult.answer_source, answerSourceLabels)}
                  </StatusPill>
                  {qaResult.model_used ? <StatusPill>模型：{qaResult.model_used}</StatusPill> : null}
                  <StatusPill tone={qaResult.insufficient_evidence ? "warning" : "success"}>
                    {qaResult.insufficient_evidence ? "证据不足" : "有证据支撑"}
                  </StatusPill>
                  {qaResult.llm_error ? <StatusPill tone="warning">LLM：{qaResult.llm_error}</StatusPill> : null}
                </div>
                <div className="qa-answer-box">
                  <h4>回答</h4>
                  <p className="qa-answer">{qaResult.answer}</p>
                </div>

                <div className="qa-followups">
                  <h4>追问建议</h4>
                  <div className="actions">
                    {qaResult.follow_up_suggestions.length ? (
                      qaResult.follow_up_suggestions.map((suggestion) => (
                        <button
                          key={suggestion}
                          className="button secondary"
                          type="button"
                          disabled={qaLoading || !result.qa_available}
                          onClick={() => {
                            setQaQuestion(suggestion);
                            void askQuestion(suggestion);
                          }}
                        >
                          {suggestion}
                        </button>
                      ))
                    ) : (
                      <p className="status-copy">暂无追问建议。</p>
                    )}
                  </div>
                </div>
              </>
            ) : (
              <div className="qa-answer-box muted-box">
                <h4>等待提问</h4>
                <p className="status-copy">提交问题后，这里会显示完整回答，不会截断为省略号。</p>
              </div>
            )}
          </div>
        </div>
      </SignalPanel>
    </main>
  );
}
