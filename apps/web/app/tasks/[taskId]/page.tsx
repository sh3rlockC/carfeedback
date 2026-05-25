"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError } from "@/lib/api";
import type { TaskArtifact, TaskDetailResponse } from "@/lib/api-types";

type DetailView = "progress" | "result";

const completedStatuses = new Set(["completed", "completed_degraded"]);
const activeStatuses = new Set(["queued", "running", "waiting_agent", "retry_wait", "retry_paused"]);
const resultArtifactTypes = new Set(["business_zip", "merged_raw_excel", "vehicle_raw_excel"]);

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

function artifactLabel(artifact: TaskArtifact) {
  const labels: Record<string, string> = {
    business_zip: "业务 ZIP",
    merged_raw_excel: "合并原始 Excel",
    vehicle_raw_excel: "车型原始 Excel",
  };
  return labels[artifact.artifact_type] ?? artifact.artifact_type;
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

  const resultArtifacts = useMemo(() => (task?.artifacts ?? []).filter(isResultArtifact), [task?.artifacts]);

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
        <SignalPanel className="stack" tone={completedStatuses.has(task.status) ? "success" : "default"}>
          <div className="task-panel-head">
            <div>
              <p className="eyebrow">RESULT</p>
              <h3>{completedStatuses.has(task.status) ? "结果产物" : "结果待生成"}</h3>
            </div>
            <StatusPill tone={statusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill>
          </div>
          <div className="artifact-grid">
            {resultArtifacts.map((artifact) => (
              <div className="card" key={artifact.artifact_id}>
                <h4>{artifactLabel(artifact)}</h4>
                <p className="artifact-path">{artifact.path}</p>
                <StatusPill tone="warning">{artifact.downloadable ? "下载入口未暴露" : "不可直接下载"}</StatusPill>
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
