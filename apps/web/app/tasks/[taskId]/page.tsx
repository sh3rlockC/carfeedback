"use client";

import Link from "next/link";
import { Clock3, Download, Hourglass, Play, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError } from "@/lib/api";
import {
  activeStatuses,
  artifactLabel,
  completedStatuses,
  formatDateTime,
  formatEtaMinutes,
  labelFor,
  queuedStatuses,
  stageLabels,
  statusLabels,
  statusTone,
} from "@/lib/task-display";
import type { TaskArtifact, TaskDetailResponse } from "@/lib/api-types";

type DetailView = "progress" | "result";

const resultArtifactTypes = new Set(["business_zip", "merged_raw_excel", "vehicle_raw_excel"]);

function isResultArtifact(artifact: TaskArtifact) {
  return resultArtifactTypes.has(artifact.artifact_type);
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
                <button
                  className="button secondary"
                  type="button"
                  disabled={!manageToken || Boolean(actionLoading)}
                  title="后端暂未开放该动作"
                >
                  <ShieldAlert size={16} aria-hidden="true" />
                  生成降级结果
                </button>
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
            <div className="platform-progress-card">
              <strong>汽车之家</strong>
              <p>优先确认车系 ID 与原始口碑入口，当前阶段会持续回填匹配状态和采集结果。</p>
            </div>
            <div className="platform-progress-card">
              <strong>懂车帝</strong>
              <p>并行检查平台车道、车系识别和评论采集进度，结果产物生成前会保留归档路径。</p>
            </div>
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
          <div className="result-delivery-grid">
            {resultArtifacts.map((artifact) => (
              <div className="delivery-card" key={artifact.artifact_id}>
                <div className="delivery-card-head">
                  <Download size={18} aria-hidden="true" />
                  <h4>{artifactLabel(artifact)}</h4>
                </div>
                <StatusPill tone={artifact.downloadable ? "success" : "warning"}>{artifact.downloadable ? "可下载" : "仅归档"}</StatusPill>
                <p className="artifact-path">{artifact.path}</p>
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
