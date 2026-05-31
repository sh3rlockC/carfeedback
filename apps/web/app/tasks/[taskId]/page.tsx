"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError } from "@/lib/api";
import type { TaskArtifact, TaskDetailResponse } from "@/lib/api-types";
import { withBasePath } from "@/lib/paths";

type DetailView = "progress" | "result";

const completedStatuses = new Set(["completed", "completed_degraded"]);
const activeStatuses = new Set(["queued", "running", "waiting_agent", "retry_wait", "retry_paused"]);
const resultArtifactTypes = new Set(["business_zip", "merged_raw_excel", "vehicle_raw_excel", "one_pager_excel"]);

const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  waiting_agent: "等待车道",
  retry_wait: "等待重试",
  retry_paused: "重试暂停",
  completed: "已完成",
  completed_degraded: "降级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

const stageLabels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  checking_incremental: "检查历史语料",
  collecting_autohome: "采集汽车之家",
  collecting_dcd: "采集懂车帝",
  postprocessing: "汇总整理",
  summarizing: "摘要生成",
  rendering_wordcloud: "词云生成",
  generating_hermes_outputs: "Hermes 报告生成",
  generating_ai_report: "AI 一页纸",
  building_qa_corpus: "问答索引",
  collecting_models: "补齐车型",
  comparing: "生成对比",
  completed: "已完成",
  completed_degraded: "降级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

function labelFor(value: string, labels: Record<string, string>) {
  return labels[value] ?? value;
}

function statusTone(status: string): "default" | "success" | "warning" | "danger" | "accent" {
  if (status === "completed") {
    return "success";
  }
  if (status === "completed_degraded" || status === "queued" || status === "waiting_agent" || status === "retry_wait" || status === "retry_paused") {
    return "warning";
  }
  if (status === "failed" || status === "cancelled" || status === "expired") {
    return "danger";
  }
  if (status === "running") {
    return "accent";
  }
  return "default";
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatEta(seconds: number | null) {
  if (seconds === null) {
    return "ETA 计算中";
  }
  if (seconds < 60) {
    return `预计剩余 ${seconds} 秒`;
  }
  return `预计剩余 ${Math.ceil(seconds / 60)} 分钟`;
}

function isResultArtifact(artifact: TaskArtifact) {
  return resultArtifactTypes.has(artifact.artifact_type);
}

function isOnePagerArtifact(artifact: TaskArtifact): artifact is TaskArtifact & { url: string } {
  return artifact.artifact_type === "json" && artifact.path.endsWith("final_report.json") && Boolean(artifact.url);
}

function artifactLabel(artifact: TaskArtifact) {
  const labels: Record<string, string> = {
    business_zip: "业务 ZIP",
    merged_raw_excel: "合并原始 Excel",
    vehicle_raw_excel: "车型原始 Excel",
    one_pager_excel: "一页纸 Excel",
  };
  return labels[artifact.artifact_type] ?? artifact.artifact_type;
}

function platformLabel(platform: string) {
  return platform === "autohome" ? "汽车之家" : platform === "dongchedi" ? "懂车帝" : platform;
}

function platformSeriesCell(vehicle: { enabled_platforms: string[]; autohome_series_id: string | null; dcd_series_id: string | null }, platform: "autohome" | "dongchedi") {
  if (!vehicle.enabled_platforms.includes(platform)) {
    return <StatusPill>用户跳过</StatusPill>;
  }
  return platform === "autohome" ? vehicle.autohome_series_id ?? "-" : vehicle.dcd_series_id ?? "-";
}

function formatPayload(payload: Record<string, unknown>) {
  return JSON.stringify(payload, null, 2);
}

type OnePagerBlock = {
  title: string;
  summary: string;
  evidenceIds: string[];
};

type OnePagerReport = {
  headline: string;
  executiveSummary: string;
  bossBrief: string[];
  platformStatuses: OnePagerBlock[];
  strengthBlocks: OnePagerBlock[];
  weaknessBlocks: OnePagerBlock[];
  platformDifferenceBlocks: OnePagerBlock[];
  actionBlocks: OnePagerBlock[];
};

function readString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function readStringArray(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map(readString).filter(Boolean);
}

function readBlocks(value: unknown) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      if (!item || typeof item !== "object") {
        return null;
      }
      const block = item as Record<string, unknown>;
      const title = readString(block.title);
      const summary = readString(block.summary);
      const evidenceIds = readStringArray(block.evidence_ids);
      if (!title && !summary) {
        return null;
      }
      return {
        title: title || "未命名条目",
        summary,
        evidenceIds,
      } satisfies OnePagerBlock;
    })
    .filter((item): item is OnePagerBlock => Boolean(item));
}

function readPlatformStatuses(value: unknown): OnePagerBlock[] {
  if (!value || typeof value !== "object") {
    return [];
  }
  const blocks: OnePagerBlock[] = [];
  for (const [platform, status] of Object.entries(value as Record<string, Record<string, unknown>>)) {
    if (!status || typeof status !== "object") {
      continue;
    }
    const label = readString(status.label) || readString(status.status);
    const rowCount = typeof status.row_count === "number" ? status.row_count : 0;
    const title = `${platformLabel(platform)}：${label}`;
    const summary =
      label === "未查新增"
        ? `本轮未完成新增检查，当前结论使用已入库历史评论 ${rowCount} 条。`
        : `本轮已完成新增检查，当前结论使用已入库历史评论与新增评论 ${rowCount} 条。`;
    blocks.push({ title, summary, evidenceIds: [] });
  }
  return blocks;
}

function normalizeOnePagerReport(payload: Record<string, unknown> | null): OnePagerReport | null {
  if (!payload) {
    return null;
  }
  return {
    headline: readString(payload.headline),
    executiveSummary: readString(payload.executive_summary),
    bossBrief: readStringArray(payload.boss_brief),
    platformStatuses: readPlatformStatuses(payload.platform_source_status),
    strengthBlocks: readBlocks(payload.strength_blocks),
    weaknessBlocks: readBlocks(payload.weakness_blocks),
    platformDifferenceBlocks: readBlocks(payload.platform_difference_blocks),
    actionBlocks: readBlocks(payload.action_blocks),
  };
}

function OnePagerSection({ title, blocks }: { title: string; blocks: OnePagerBlock[] }) {
  if (!blocks.length) {
    return null;
  }
  return (
    <section className="one-pager-section">
      <div className="one-pager-section-head">
        <p className="eyebrow">SECTION</p>
        <h5>{title}</h5>
      </div>
      <div className="one-pager-blocks">
        {blocks.map((block) => (
          <article className="one-pager-block" key={`${title}-${block.title}-${block.summary}`}>
            <strong>{block.title}</strong>
            {block.summary ? <p>{block.summary}</p> : null}
            {block.evidenceIds.length ? <span>证据：{block.evidenceIds.join("、")}</span> : null}
          </article>
        ))}
      </div>
    </section>
  );
}

function progressPercent(task: TaskDetailResponse) {
  if (completedStatuses.has(task.status)) {
    return 100;
  }
  if (task.status === "running") {
    return 55;
  }
  if (task.status === "queued" || task.status === "waiting_agent" || task.status === "retry_wait") {
    return 12;
  }
  return 0;
}

function TaskDetailContent() {
  const params = useParams<{ taskId: string }>();
  const searchParams = useSearchParams();
  const taskId = params.taskId;
  const viewToken = searchParams.get("view_token");
  const manageToken = searchParams.get("manage_token");
  const [task, setTask] = useState<TaskDetailResponse | null>(null);
  const [view, setView] = useState<DetailView>("progress");
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState("");
  const [error, setError] = useState("");
  const [onePagerReport, setOnePagerReport] = useState<Record<string, unknown> | null>(null);
  const [onePagerError, setOnePagerError] = useState("");
  const [onePagerLoading, setOnePagerLoading] = useState(false);

  const detailPath = useMemo(() => {
    const paramsForRequest = new URLSearchParams();
    if (viewToken) {
      paramsForRequest.set("view_token", viewToken);
    }
    const suffix = paramsForRequest.toString();
    return `/api/tasks/${taskId}${suffix ? `?${suffix}` : ""}`;
  }, [taskId, viewToken]);

  const loadTask = async () => {
    setError("");
    try {
      const payload = await apiRequest<TaskDetailResponse>(detailPath);
      setTask(payload);
      setView(completedStatuses.has(payload.status) ? "result" : "progress");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "无法读取任务详情。");
    } finally {
      setLoading(false);
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
          setView(completedStatuses.has(payload.status) ? "result" : "progress");
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
    if (!task || !activeStatuses.has(task.status)) {
      return;
    }

    const timer = window.setInterval(() => {
      void loadTask();
    }, 5000);

    return () => {
      window.clearInterval(timer);
    };
  }, [detailPath, task, task?.status]);

  const resultArtifacts = useMemo(() => (task?.artifacts ?? []).filter(isResultArtifact), [task?.artifacts]);
  const onePagerArtifact = useMemo(() => (task?.artifacts ?? []).find(isOnePagerArtifact) || null, [task?.artifacts]);
  const onePager = normalizeOnePagerReport(onePagerReport);

  useEffect(() => {
    if (!onePagerArtifact?.url) {
      setOnePagerReport(null);
      setOnePagerError("");
      return;
    }

    let cancelled = false;

    const run = async () => {
      setOnePagerLoading(true);
      setOnePagerError("");
      try {
        const artifactUrl = onePagerArtifact.url!;
        const report = await apiRequest<Record<string, unknown>>(withBasePath(artifactUrl));
        if (!cancelled) {
          setOnePagerReport(report);
        }
      } catch (err) {
        if (!cancelled) {
          setOnePagerError(err instanceof ApiError ? err.message : "读取一页纸失败。");
        }
      } finally {
        if (!cancelled) {
          setOnePagerLoading(false);
        }
      }
    };

    void run();
    return () => {
      cancelled = true;
    };
  }, [onePagerArtifact?.url]);

  const runManagementAction = async (action: "cancel" | "retry" | "pause-retry") => {
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
        {task.issue_summary ? (
          <SignalPanel tone="danger" className="task-inline-banner">
            <strong>问题摘要</strong>
            <span>{task.issue_summary}</span>
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
              <h3>{labelFor(task.current_stage, stageLabels)}</h3>
            </div>
            <StatusPill tone={statusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill>
          </div>
          <div className="bar" aria-hidden="true">
            <span style={{ width: `${progressPercent(task)}%` }} />
          </div>
          <div className="meta-row">
            <StatusPill tone="warning">{formatEta(task.eta_seconds)}</StatusPill>
            {task.eta_reason ? <StatusPill tone="accent">{task.eta_reason}</StatusPill> : null}
          </div>
          <div className="task-table-wrap">
            <table className="task-table">
              <thead>
                <tr>
                  <th>车型</th>
                  <th>状态</th>
                  <th>汽车之家</th>
                  <th>懂车帝</th>
                  <th>问题</th>
                </tr>
              </thead>
              <tbody>
                {task.vehicles.map((vehicle) => (
                  <tr key={vehicle.task_vehicle_id}>
                    <td>{vehicle.model_name || vehicle.query}</td>
                    <td>
                      <StatusPill tone={statusTone(vehicle.status)}>{labelFor(vehicle.status, statusLabels)}</StatusPill>
                    </td>
                    <td>{platformSeriesCell(vehicle, "autohome")}</td>
                    <td>{platformSeriesCell(vehicle, "dongchedi")}</td>
                    <td>
                      {vehicle.error_code || vehicle.error_message || vehicle.missing_platforms.length ? (
                        <div className="stack">
                          {vehicle.error_code ? <StatusPill tone="danger">{vehicle.error_code}</StatusPill> : null}
                          {vehicle.missing_platforms.length ? (
                            <span className="field-hint">缺失：{vehicle.missing_platforms.map(platformLabel).join("、")}</span>
                          ) : null}
                          {vehicle.error_message ? <span className="error">{vehicle.error_message}</span> : null}
                        </div>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SignalPanel>
      ) : (
        <SignalPanel className="stack" tone={completedStatuses.has(task.status) ? "success" : "default"}>
          <div className="task-panel-head">
            <div>
              <p className="eyebrow">RESULT</p>
              <h3>{completedStatuses.has(task.status) ? "结果产物" : "结果待生成"}</h3>
            </div>
            <StatusPill tone={statusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill>
          </div>
          {onePagerArtifact ? <h4>AI 一页纸（在线）</h4> : null}
          {onePagerArtifact ? (
            <div className="stack">
              {onePagerLoading ? <p className="status-copy">一页纸加载中...</p> : null}
              {onePagerError ? <p className="error">{onePagerError}</p> : null}
              {onePager ? (
                <section className="one-pager-sheet">
                  <div className="one-pager-hero">
                    <p className="eyebrow">ONE PAGER</p>
                    <h4>{onePager.headline || "AI 一页纸"}</h4>
                    {onePager.executiveSummary ? <p>{onePager.executiveSummary}</p> : null}
                  </div>
                  {onePager.bossBrief.length ? (
                    <section className="one-pager-section one-pager-brief">
                      <div className="one-pager-section-head">
                        <p className="eyebrow">BOSS BRIEF</p>
                        <h5>管理层摘要</h5>
                      </div>
                      <div className="one-pager-brief-list">
                        {onePager.bossBrief.map((item) => (
                          <article className="brief-card" key={item}>
                            <span>摘要</span>
                            <p>{item}</p>
                          </article>
                        ))}
                      </div>
                    </section>
                  ) : null}
                  <OnePagerSection title="数据状态" blocks={onePager.platformStatuses} />
                  <div className="one-pager-grid">
                    <OnePagerSection title="核心优势" blocks={onePager.strengthBlocks} />
                    <OnePagerSection title="核心短板" blocks={onePager.weaknessBlocks} />
                  </div>
                  <div className="one-pager-grid">
                    <OnePagerSection title="平台差异" blocks={onePager.platformDifferenceBlocks} />
                    <OnePagerSection title="行动建议" blocks={onePager.actionBlocks} />
                  </div>
                </section>
              ) : null}
              {onePagerReport && !onePager ? <pre className="artifact-path">{formatPayload(onePagerReport)}</pre> : null}
              <a className="button secondary" href={withBasePath(onePagerArtifact.url)}>
                下载一页纸 JSON
              </a>
            </div>
          ) : null}
          <div className="artifact-grid">
            {resultArtifacts.map((artifact) => (
              <div className="card" key={artifact.artifact_id}>
                <h4>{artifactLabel(artifact)}</h4>
                <p className="artifact-path">{artifact.path}</p>
                {artifact.downloadable && artifact.url ? (
                  <a className="button secondary" href={withBasePath(artifact.url)}>
                    下载
                  </a>
                ) : (
                  <StatusPill tone="warning">不可直接下载</StatusPill>
                )}
              </div>
            ))}
            {!resultArtifacts.length ? <p className="status-copy">当前详情未返回可展示产物。</p> : null}
          </div>
        </SignalPanel>
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
                {event.summary ? <p className="status-copy">{event.summary}</p> : null}
                {Object.keys(event.payload).length ? (
                  <details>
                    <summary>查看 payload</summary>
                    <pre className="artifact-path">{formatPayload(event.payload)}</pre>
                  </details>
                ) : null}
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
