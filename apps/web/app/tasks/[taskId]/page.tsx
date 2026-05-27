"use client";

import Link from "next/link";
import { BarChart3, Clock3, Download, FileArchive, Hourglass, Play, RefreshCw, ShieldAlert, Sparkles, XCircle } from "lucide-react";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError } from "@/lib/api";
import {
  activeStatuses,
  completedStatuses,
  formatDateTime,
  formatEtaMinutes,
  labelFor,
  queuedStatuses,
  stageLabels,
  statusLabels,
  statusTone,
} from "@/lib/task-display";
import type { TaskDetailResponse } from "@/lib/api-types";
import type { KeywordRankItem, TaskResultArtifact, TaskResultResponse } from "@/lib/api-types";
import { withBasePath } from "@/lib/paths";

type DetailView = "progress" | "result";

function platformLabel(platform: string) {
  if (platform === "autohome") {
    return "汽车之家";
  }
  if (platform === "dongchedi") {
    return "懂车帝";
  }
  return platform;
}

function progressPercent(task: TaskDetailResponse) {
  if (completedStatuses.has(task.status)) {
    return 100;
  }
  if (task.status === "running") {
    return 55;
  }
  if (queuedStatuses.has(task.status)) {
    return 12;
  }
  return 0;
}

function resultUrlWithToken(path: string | null, viewToken: string | null) {
  if (!path) {
    return "";
  }
  const [base, query = ""] = path.split("?");
  const params = new URLSearchParams(query);
  if (viewToken) {
    params.set("view_token", viewToken);
  }
  const suffix = params.toString();
  return withBasePath(`${base}${suffix ? `?${suffix}` : ""}`);
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
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

function reportBlocks(report: Record<string, unknown> | null) {
  if (!report) {
    return [];
  }
  return ["strength_blocks", "weakness_blocks", "action_blocks"].flatMap((key) => {
    const value = report[key];
    if (!Array.isArray(value)) {
      return [];
    }
    return value
      .map((item) => {
        const record = asRecord(item);
        return {
          title: asText(record.title),
          summary: asText(record.summary),
        };
      })
      .filter((item) => item.title || item.summary);
  });
}

function artifactFileName(artifact: TaskResultArtifact) {
  return artifact.path.split("/").pop() || artifact.path;
}

function KeywordRankList({ title, items }: { title: string; items: KeywordRankItem[] }) {
  const maxCount = Math.max(...items.map((item) => item.count), 0);
  return (
    <div className="keyword-rank-card keyword-rank-combined">
      <div className="keyword-rank-head">
        <h4>{title}</h4>
        <span>{items.length ? `Top ${items.length}` : "暂无数据"}</span>
      </div>
      <div className="keyword-rank-list">
        {items.map((item) => (
          <div className="keyword-rank-row" key={`${title}-${item.term}`}>
            <span>{item.term}</span>
            <div className="keyword-rank-track" aria-hidden="true">
              <span style={{ width: `${maxCount ? Math.max(8, (item.count / maxCount) * 100) : 0}%` }} />
            </div>
            <strong>{item.count}</strong>
          </div>
        ))}
        {!items.length ? <p className="status-copy">暂无关键词。</p> : null}
      </div>
    </div>
  );
}

function TaskDetailContent() {
  const params = useParams<{ taskId: string }>();
  const searchParams = useSearchParams();
  const taskId = params.taskId;
  const viewToken = searchParams.get("view_token");
  const manageToken = searchParams.get("manage_token");
  const [task, setTask] = useState<TaskDetailResponse | null>(null);
  const [taskResult, setTaskResult] = useState<TaskResultResponse | null>(null);
  const [view, setView] = useState<DetailView>("progress");
  const [loading, setLoading] = useState(true);
  const [resultLoading, setResultLoading] = useState(false);
  const [artifactDetailsOpen, setArtifactDetailsOpen] = useState(false);
  const [actionLoading, setActionLoading] = useState("");
  const [error, setError] = useState("");

  const detailPath = useMemo(() => {
    const paramsForRequest = new URLSearchParams();
    if (viewToken) {
      paramsForRequest.set("view_token", viewToken);
    }
    const suffix = paramsForRequest.toString();
    return `/api/tasks/${taskId}${suffix ? `?${suffix}` : ""}`;
  }, [taskId, viewToken]);

  const resultPath = useMemo(() => {
    const paramsForRequest = new URLSearchParams();
    if (viewToken) {
      paramsForRequest.set("view_token", viewToken);
    }
    const suffix = paramsForRequest.toString();
    return `/api/tasks/${taskId}/result${suffix ? `?${suffix}` : ""}`;
  }, [taskId, viewToken]);

  const loadTask = async () => {
    setError("");
    try {
      const payload = await apiRequest<TaskDetailResponse>(detailPath);
      setTask(payload);
      setView(completedStatuses.has(payload.status) || payload.status === "retry_paused" ? "result" : "progress");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "无法读取任务详情。");
    } finally {
      setLoading(false);
    }
  };

  const loadTaskResult = async () => {
    setResultLoading(true);
    try {
      const payload = await apiRequest<TaskResultResponse>(resultPath);
      setTaskResult(payload);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "无法读取任务结果。");
    } finally {
      setResultLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      setLoading(true);
      setError("");
      try {
        const payload = await apiRequest<TaskDetailResponse>(detailPath);
        if (!cancelled) {
          setTask(payload);
          setView(completedStatuses.has(payload.status) || payload.status === "retry_paused" ? "result" : "progress");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "无法读取任务详情。");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [detailPath]);

  useEffect(() => {
    if (!task || view !== "result") {
      return;
    }
    void loadTaskResult();
  }, [task?.task_id, task?.status, view, resultPath]);

  useEffect(() => {
    if (!task || !activeStatuses.has(task.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadTask();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [task?.status, detailPath]);

  const runManagementAction = async (action: "cancel" | "retry" | "pause-retry" | "degrade-result") => {
    if (!manageToken) {
      return;
    }
    setActionLoading(action);
    setError("");
    try {
      const payload = await apiRequest<TaskDetailResponse>(`/api/tasks/${taskId}/${action}?manage_token=${encodeURIComponent(manageToken)}`, {
        method: "POST",
      });
      setTask(payload);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "管理动作失败。");
    } finally {
      setActionLoading("");
    }
  };

  if (loading) {
    return <main className="panel guard">正在加载任务...</main>;
  }

  if (!task) {
    return (
      <main className="panel guard">
        <p className="eyebrow">TASK DETAIL</p>
        <h2>无法打开任务</h2>
        {error ? <p className="error">{error}</p> : null}
        <div className="actions">
          <Link className="button secondary" href="/tasks">
            返回任务中心
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="stack-lg task-workbench">
      <SignalPanel className="stack">
        <div className="task-page-head">
          <SectionHeader eyebrow="TASK DETAIL" title={task.display_name} />
          <div className="actions">
            <Link className="button secondary" href="/tasks">
              返回列表
            </Link>
          </div>
        </div>

        {error ? <p className="error">{error}</p> : null}
        {task.degraded ? (
          <SignalPanel tone="warning" className="task-inline-banner">
            <strong>降级完成</strong>
            <span>部分数据或产物使用降级路径生成。</span>
          </SignalPanel>
        ) : null}
        {task.upgraded_to_full ? (
          <SignalPanel tone="success" className="task-inline-banner">
            <strong>已升级</strong>
            <span>后续重试补齐了完整结果。</span>
          </SignalPanel>
        ) : null}

        <div className="task-load-row">
          <div>
            <span>状态</span>
            <strong>{labelFor(task.status, statusLabels)}</strong>
          </div>
          <div>
            <span>阶段</span>
            <strong>{labelFor(task.current_stage, stageLabels)}</strong>
          </div>
          <div>
            <span>创建</span>
            <strong>{formatDateTime(task.created_at)}</strong>
          </div>
          <div>
            <span>完成</span>
            <strong>{formatDateTime(task.completed_at)}</strong>
          </div>
        </div>
      </SignalPanel>

      <div className="task-tabs" role="tablist" aria-label="任务详情视图">
        <button className={view === "progress" ? "active" : ""} type="button" onClick={() => setView("progress")}>
          进度
        </button>
        <button className={view === "result" ? "active" : ""} type="button" onClick={() => setView("result")}>
          结果
        </button>
      </div>

      {view === "progress" ? (
        <SignalPanel className="stack" tone={activeStatuses.has(task.status) ? "accent" : "default"}>
          <div className="task-panel-head">
            <div>
              <p className="eyebrow">PROGRESS</p>
              <h3 className="task-panel-title">
                <Play size={18} aria-hidden="true" />
                {labelFor(task.current_stage, stageLabels)}
              </h3>
            </div>
            <StatusPill tone={statusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill>
          </div>
          <section className="task-detail-grid">
            <div className="eta-card">
              <div className="detail-card-icon" aria-hidden="true">
                <Clock3 size={18} />
              </div>
              <div>
                <span>预计完成</span>
                <strong>{formatEtaMinutes(task.eta_seconds)}</strong>
                <p>{task.eta_reason || "系统正在根据队列、平台车道和当前阶段估算剩余时间。"}</p>
              </div>
              <Hourglass className="eta-card-watermark" size={42} aria-hidden="true" />
            </div>
            <div className="action-card">
              <span>可操作选项</span>
              <div className="actions vertical-actions">
                <button className="button secondary" type="button" disabled={Boolean(actionLoading)} onClick={() => void loadTask()}>
                  <RefreshCw size={16} aria-hidden="true" />
                  继续等待
                </button>
                {task.status === "completed_degraded" ? (
                  <button className="button secondary" type="button" disabled={!manageToken || Boolean(actionLoading)} onClick={() => void runManagementAction("retry")}>
                    <ShieldAlert size={16} aria-hidden="true" />
                    继续等待重试并输出全结果
                  </button>
                ) : (
                  <button className="button secondary" type="button" disabled title="当前会在部分平台失败后自动生成降级结果">
                    <ShieldAlert size={16} aria-hidden="true" />
                    降级结果自动生成
                  </button>
                )}
                <button
                  className="button danger"
                  type="button"
                  disabled={!manageToken || Boolean(actionLoading)}
                  onClick={() => void runManagementAction("cancel")}
                >
                  <XCircle size={16} aria-hidden="true" />
                  取消任务
                </button>
              </div>
            </div>
          </section>
          <div className="bar" aria-hidden="true">
            <span style={{ width: `${progressPercent(task)}%` }} />
          </div>
          <div className="meta-row">
            <StatusPill tone="warning">
              {task.eta_seconds === null ? "ETA 计算中" : `预计剩余 ${formatEtaMinutes(task.eta_seconds)}`}
            </StatusPill>
            {task.eta_reason ? <StatusPill tone="accent">{task.eta_reason}</StatusPill> : null}
          </div>
          <div className="platform-progress-grid">
            {(task.collection_runs?.length ? task.collection_runs : []).map((run) => {
              const latestEvent = run.events.at(-1);
              return (
                <div className="platform-progress-card" key={run.run_id}>
                  <div className="task-panel-head compact">
                    <strong>{platformLabel(run.platform)}</strong>
                    <StatusPill tone={statusTone(run.status)}>{labelFor(run.status, statusLabels)}</StatusPill>
                  </div>
                  <p>
                    {run.mode === "incremental" ? "增量采集" : "全量刷新"} / seriesId {run.series_id}
                    {run.agent_id ? ` / ${run.agent_id}` : ""}
                  </p>
                  <p>{latestEvent ? `${latestEvent.event_type} · ${formatDateTime(latestEvent.created_at)}` : "等待采集调度。"}</p>
                  {run.failure_category ? <p className="error">失败类型：{run.failure_category}</p> : null}
                  {run.output_path ? <p className="artifact-path">{run.output_path}</p> : null}
                </div>
              );
            })}
            {!task.collection_runs?.length ? (
              <>
                <div className="platform-progress-card">
                  <strong>汽车之家</strong>
                  <p>等待创建采集 run。</p>
                </div>
                <div className="platform-progress-card">
                  <strong>懂车帝</strong>
                  <p>等待创建采集 run。</p>
                </div>
              </>
            ) : null}
          </div>
          <div className="task-table-wrap">
            <table className="task-table">
              <thead>
                <tr>
                  <th>车型</th>
                  <th>状态</th>
                  <th>汽车之家</th>
                  <th>懂车帝</th>
                </tr>
              </thead>
              <tbody>
                {task.vehicles.map((vehicle) => (
                  <tr key={vehicle.task_vehicle_id}>
                    <td>{vehicle.model_name || vehicle.query}</td>
                    <td>{labelFor(vehicle.status, statusLabels)}</td>
                    <td>{vehicle.autohome_series_id ?? "-"}</td>
                    <td>{vehicle.dcd_series_id ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SignalPanel>
      ) : (
        <div className="result-dashboard-page">
          {task.status === "retry_paused" ? (
            <SignalPanel className="stack" tone="warning">
              <div className="task-panel-head">
                <div>
                  <p className="eyebrow">REPORT PAUSED</p>
                  <h3>智能一页纸生成暂停</h3>
                </div>
                <StatusPill tone="warning">需要选择</StatusPill>
              </div>
              <p className="status-copy">采集数据已经进入语料库，但完整智能一页纸没有在自动重试窗口内生成。请选择生成降级结果，或继续等待重试并输出全结果。</p>
              <div className="actions">
                <button className="button" type="button" disabled={!manageToken || Boolean(actionLoading)} onClick={() => void runManagementAction("retry")}>
                  <RefreshCw size={16} aria-hidden="true" />
                  继续等待重试并输出全结果
                </button>
                <button className="button secondary" type="button" disabled={!manageToken || Boolean(actionLoading)} onClick={() => void runManagementAction("degrade-result")}>
                  <ShieldAlert size={16} aria-hidden="true" />
                  生成降级结果
                </button>
              </div>
            </SignalPanel>
          ) : null}

          {resultLoading && !taskResult ? <SignalPanel className="stack">正在加载智能一页纸...</SignalPanel> : null}

          {taskResult ? (
            <>
              <section className="result-dashboard-hero">
                <div className="result-conclusion-card">
                  <p className="eyebrow">SMART ONE-PAGER</p>
                  <h1>{taskResult.model_name || taskResult.display_name}</h1>
                  <p>
                    {reportText(
                      taskResult.ai_report,
                      ["headline", "title"],
                      taskResult.template_report.highlights[0] || "智能一页纸结果已生成。"
                    )}
                  </p>
                  <div className="meta-row">
                    <StatusPill tone={taskResult.degraded ? "warning" : "success"}>
                      {taskResult.degraded ? "降级结果" : "完整结果"}
                    </StatusPill>
                    <StatusPill tone={taskResult.report_ready ? "success" : "warning"}>
                      报告：{taskResult.report_ready ? "可用" : "生成中"}
                    </StatusPill>
                    {taskResult.generated_at ? <StatusPill>{formatDateTime(taskResult.generated_at)}</StatusPill> : null}
                  </div>
                </div>

                <aside className="result-delivery-panel">
                  <div className="panel-head">
                    <div>
                      <p className="eyebrow">DELIVERY</p>
                      <h2>下载</h2>
                    </div>
                    <StatusPill tone="accent">{taskResult.artifacts.length} 个产物</StatusPill>
                  </div>
                  <a className="download-link primary-download" href={resultUrlWithToken(taskResult.zip_url, viewToken)}>
                    <FileArchive size={16} aria-hidden="true" />
                    下载全部结果包
                  </a>
                  <details className="artifact-details" open={artifactDetailsOpen} onToggle={(event) => setArtifactDetailsOpen(event.currentTarget.open)}>
                    <summary>产物明细</summary>
                    <div className="download-list">
                      {taskResult.artifacts.filter((artifact) => artifact.downloadable).map((artifact) => (
                        <a className="download-link" href={resultUrlWithToken(artifact.url, viewToken)} key={artifact.id}>
                          {artifactFileName(artifact)}
                          <Download size={14} aria-hidden="true" />
                        </a>
                      ))}
                    </div>
                  </details>
                </aside>
              </section>

              <section className="metric-grid" aria-label="结果指标摘要">
                <div className="metric-tile">
                  <span className="metric-icon">总</span>
                  <span>累计评论</span>
                  <strong>{taskResult.sample_summary.autohome_count + taskResult.sample_summary.dcd_count}</strong>
                  <p>双平台可分析样本</p>
                </div>
                <div className="metric-tile">
                  <span className="metric-icon">家</span>
                  <span>汽车之家</span>
                  <strong>{taskResult.sample_summary.autohome_count}</strong>
                  <p>{taskResult.collection_summary.autohome?.mode || "累计语料"}</p>
                </div>
                <div className="metric-tile">
                  <span className="metric-icon">懂</span>
                  <span>懂车帝</span>
                  <strong>{taskResult.sample_summary.dcd_count}</strong>
                  <p>{taskResult.collection_summary.dongchedi?.mode || "累计语料"}</p>
                </div>
                <div className="metric-tile">
                  <span className="metric-icon">报</span>
                  <span>报告状态</span>
                  <strong>{taskResult.ai_available ? "AI 完整" : "规则降级"}</strong>
                  <p>{taskResult.report_ready ? "完整报告可下载" : "等待报告产物"}</p>
                </div>
              </section>

              <section className="insight-layout">
                <article className="executive-report">
                  <div className="report-kicker">
                    <StatusPill tone="accent">智能一页纸</StatusPill>
                    <StatusPill>{taskResult.model_name}</StatusPill>
                  </div>
                  <h3>{reportText(taskResult.ai_report, ["headline", "title"], taskResult.template_report.title || "口碑摘要")}</h3>
                  <p className="executive-summary">
                    {reportText(
                      taskResult.ai_report,
                      ["executive_summary", "summary", "conclusion"],
                      taskResult.template_report.highlights[0] || "当前车型口碑报告已生成，可查看关键发现、关键词和下载完整结果包。"
                    )}
                  </p>
                  <div className="brief-grid">
                    {reportList(taskResult.ai_report, ["boss_brief", "key_findings", "findings"], taskResult.template_report.highlights.slice(0, 4)).slice(0, 4).map((item, index) => (
                      <div className="brief-card" key={`${item}-${index}`}>
                        <span>{String(index + 1).padStart(2, "0")}</span>
                        <p>{item}</p>
                      </div>
                    ))}
                  </div>
                  <div className="dashboard-summary-list">
                    {reportBlocks(taskResult.ai_report).slice(0, 5).map((block, index) => (
                      <p key={`${block.title}-${index}`}>
                        <strong>{block.title}</strong>
                        {block.summary}
                      </p>
                    ))}
                  </div>
                </article>

                <aside className="artifact-column">
                  <div className="artifact-card template-card">
                    <h3>{taskResult.template_report.title || "模板一页纸"}</h3>
                    <div className="timeline">
                      {taskResult.template_report.highlights.slice(0, 6).map((item) => (
                        <div className="timeline-item" key={item}>
                          <span className="timeline-dot" />
                          <p>{item}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="artifact-card wordcloud-card">
                    <h3>词云预览</h3>
                    {taskResult.wordcloud.positive_image_url || taskResult.wordcloud.negative_image_url ? (
                      <div className="wordcloud-preview">
                        {taskResult.wordcloud.positive_image_url ? <img src={resultUrlWithToken(taskResult.wordcloud.positive_image_url, viewToken)} alt="优点词云" /> : null}
                        {taskResult.wordcloud.negative_image_url ? <img src={resultUrlWithToken(taskResult.wordcloud.negative_image_url, viewToken)} alt="槽点词云" /> : null}
                      </div>
                    ) : (
                      <p className="status-copy">词云生成后会显示在这里。</p>
                    )}
                  </div>
                </aside>
              </section>

              <section className="result-dashboard-grid" aria-label="结构化摘要">
                <article className="flat-panel">
                  <div className="panel-head">
                    <div>
                      <p className="eyebrow">MATRIX</p>
                      <h2>维度矩阵摘要</h2>
                    </div>
                    <BarChart3 size={18} aria-hidden="true" />
                  </div>
                  <div className="dashboard-summary-list">
                    {[...taskResult.structured_sections.overview, ...taskResult.structured_sections.compare, ...taskResult.structured_sections.business, ...taskResult.structured_sections.opportunities].slice(0, 5).map((item, index) => (
                      <p key={`${Object.values(item).join("-")}-${index}`}>{Object.values(item).filter(Boolean).join(" / ")}</p>
                    ))}
                  </div>
                </article>
                <article className="flat-panel">
                  <div className="panel-head">
                    <div>
                      <p className="eyebrow">EVIDENCE</p>
                      <h2>脱敏样本</h2>
                    </div>
                    <Sparkles size={18} aria-hidden="true" />
                  </div>
                  <div className="dashboard-summary-list">
                    {taskResult.evidence_samples.slice(0, 4).map((sample) => (
                      <p key={sample.comment_id}>
                        <strong>{sample.platform || sample.comment_id}</strong>
                        {sample.text}
                      </p>
                    ))}
                    {!taskResult.evidence_samples.length ? <p className="status-copy">暂无可展示脱敏样本。</p> : null}
                  </div>
                </article>
                <article className="flat-panel">
                  <div className="panel-head">
                    <div>
                      <p className="eyebrow">KEYWORDS</p>
                      <h2>关键词</h2>
                    </div>
                    <StatusPill tone="accent">Top</StatusPill>
                  </div>
                  <div className="keyword-chip-list">
                    {[...taskResult.wordcloud.keyword_rankings.positive, ...taskResult.wordcloud.keyword_rankings.negative].slice(0, 8).map((item) => (
                      <span key={`${item.term}-${item.count}`}>
                        {item.term}
                        <strong>{item.count}</strong>
                      </span>
                    ))}
                  </div>
                </article>
              </section>

              <section className="keyword-rank-section">
                <div className="keyword-rank-section-head">
                  <div>
                    <p className="eyebrow">WORD RANK</p>
                    <h3>关键词出现次数排名</h3>
                  </div>
                  <p>按词项清单中的出现次数排序，展示优点、槽点和全量关键词。</p>
                </div>
                <div className="keyword-rank-grid">
                  <KeywordRankList title="优点关键词" items={taskResult.wordcloud.keyword_rankings.positive} />
                  <KeywordRankList title="槽点关键词" items={taskResult.wordcloud.keyword_rankings.negative} />
                  <KeywordRankList title="全部关键词" items={taskResult.wordcloud.keyword_rankings.combined} />
                </div>
              </section>
            </>
          ) : null}

          {!taskResult && !resultLoading && task.status !== "retry_paused" ? (
            <SignalPanel className="stack">
              <div className="task-panel-head">
                <div>
                  <p className="eyebrow">RESULT</p>
                  <h3>结果待生成</h3>
                </div>
                <StatusPill tone={statusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill>
              </div>
              <p className="status-copy">智能一页纸生成后会展示在这里。</p>
            </SignalPanel>
          ) : null}
        </div>
      )}

      {manageToken ? (
        <SignalPanel className="stack" tone="warning">
          <div className="task-panel-head">
            <div>
              <p className="eyebrow">MANAGE</p>
              <h3>管理动作</h3>
            </div>
            <StatusPill tone="warning">manage_token</StatusPill>
          </div>
          <div className="actions">
            <button className="button danger" type="button" disabled={Boolean(actionLoading)} onClick={() => void runManagementAction("cancel")}>
              {actionLoading === "cancel" ? "处理中" : "取消"}
            </button>
            <button className="button" type="button" disabled={Boolean(actionLoading)} onClick={() => void runManagementAction("retry")}>
              {actionLoading === "retry" ? "处理中" : "重试"}
            </button>
            <button className="button secondary" type="button" disabled={Boolean(actionLoading)} onClick={() => void runManagementAction("pause-retry")}>
              {actionLoading === "pause-retry" ? "处理中" : "暂停重试"}
            </button>
            <button className="button secondary" type="button" disabled={Boolean(actionLoading)} onClick={() => void loadTask()}>
              刷新
            </button>
          </div>
        </SignalPanel>
      ) : null}

      <SignalPanel className="stack">
        <div className="task-panel-head">
          <div>
            <p className="eyebrow">EVENTS</p>
            <h3>事件</h3>
          </div>
        </div>
        <div className="timeline">
          {task.events.map((event) => (
            <div className="timeline-item" key={event.event_id}>
              <span className="timeline-dot" aria-hidden="true" />
              <div>
                <strong>{event.event_type}</strong>
                <p className="field-hint">{formatDateTime(event.created_at)}</p>
              </div>
            </div>
          ))}
          {!task.events.length ? <p className="status-copy">暂无事件。</p> : null}
        </div>
      </SignalPanel>
    </main>
  );
}

export default function TaskDetailPage() {
  return (
    <Suspense fallback={<main className="panel guard">正在加载任务...</main>}>
      <TaskDetailContent />
    </Suspense>
  );
}
