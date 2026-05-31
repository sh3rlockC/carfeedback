"use client";

import { ArrowUpRight, Filter, ListChecks, Plus, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AccessGatePanel } from "@/app/components/access-gate";
import { useAccessSession } from "@/app/components/access-session";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError } from "@/lib/api";
import type { TaskListItem, TaskLoadResponse } from "@/lib/api-types";

type PlatformStatusFilter = "all" | "available" | "crowded";
type FlagFilter = "all" | "yes" | "no";
type LoadIssue = { kind: "missing" | "error"; message: string };

const runningStatuses = new Set(["running"]);
const queuedStatuses = new Set(["queued", "waiting_agent", "retry_wait"]);

const taskTypeLabels: Record<TaskListItem["task_type"], string> = {
  single: "单车型",
  comparison: "对比",
};

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
  workflow_start_failed: "启动失败",
};

function labelFor(value: string, labels: Record<string, string>) {
  return labels[value] ?? value;
}

function statusTone(status: string): "default" | "success" | "warning" | "danger" | "accent" {
  if (status === "completed") {
    return "success";
  }
  if (status === "completed_degraded" || status === "queued" || status === "waiting_agent" || status === "retry_wait") {
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
    return "-";
  }
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  return `${Math.ceil(seconds / 60)} 分钟`;
}

function safeTimestamp(value: string | null | undefined) {
  if (!value) {
    return 0;
  }
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function newestFirst(a: TaskListItem, b: TaskListItem) {
  return safeTimestamp(b.created_at) - safeTimestamp(a.created_at) || b.task_id.localeCompare(a.task_id);
}

function matchesFlag(value: boolean, filter: FlagFilter) {
  return filter === "all" || (filter === "yes" ? value : !value);
}

function taskMatchesDateRange(task: TaskListItem, dateFrom: string, dateTo: string) {
  const createdDate = task.created_at.slice(0, 10);
  return (!dateFrom || createdDate >= dateFrom) && (!dateTo || createdDate <= dateTo);
}

function fallbackLoad(tasks: TaskListItem[]): TaskLoadResponse {
  return {
    running_task_count: tasks.filter((task) => runningStatuses.has(task.status)).length,
    queued_task_count: tasks.filter((task) => queuedStatuses.has(task.status)).length,
    platforms: {},
  };
}

export default function TasksPage() {
  const access = useAccessSession();
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [load, setLoad] = useState<TaskLoadResponse | null>(null);
  const [loadIssue, setLoadIssue] = useState<LoadIssue | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [degradedFilter, setDegradedFilter] = useState<FlagFilter>("all");
  const [upgradedFilter, setUpgradedFilter] = useState<FlagFilter>("all");
  const [platformStatus, setPlatformStatus] = useState<PlatformStatusFilter>("all");
  const canUseWorkbench = !access.accessControlEnabled || access.accessState === "authorized";

  const loadTasks = async () => {
    setLoading(true);
    setError("");
    try {
      const taskPayload = await apiRequest<TaskListItem[]>("/api/tasks");
      setTasks(taskPayload);

      try {
        const loadPayload = await apiRequest<TaskLoadResponse>("/api/tasks/load");
        setLoad(loadPayload);
        setLoadIssue(null);
      } catch (err) {
        setLoad(null);
        if (err instanceof ApiError && err.status === 404) {
          setLoadIssue({ kind: "missing", message: "负载数据暂不可用" });
        } else if (err instanceof ApiError && err.status === 401) {
          access.reset();
          return;
        } else {
          setLoadIssue({
            kind: "error",
            message: err instanceof ApiError ? `负载接口异常：${err.message}` : "负载接口异常。",
          });
        }
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        access.reset();
        setTasks([]);
        setLoad(null);
        setLoadIssue(null);
        return;
      }
      setError(err instanceof ApiError ? err.message : "无法读取任务列表。");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (access.accessControlEnabled && access.accessState === "checking") {
      setLoading(true);
      return;
    }
    if (!canUseWorkbench) {
      setLoading(false);
      setTasks([]);
      setLoad(null);
      setLoadIssue(null);
      setError("");
      return;
    }
    void loadTasks();
  }, [access.accessControlEnabled, access.accessState, canUseWorkbench]);

  const effectiveLoad = load ?? fallbackLoad(tasks);

  const statusOptions = useMemo(() => Array.from(new Set(tasks.map((task) => task.status))).sort(), [tasks]);

  const filteredPlatforms = useMemo(() => {
    const entries = Object.entries(effectiveLoad.platforms);
    if (platformStatus === "available") {
      return entries.filter(([, value]) => value.available > 0);
    }
    if (platformStatus === "crowded") {
      return entries.filter(([, value]) => value.total > 0 && value.available === 0);
    }
    return entries;
  }, [effectiveLoad.platforms, platformStatus]);

  const filteredTasks = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return tasks
      .filter((task) => statusFilter === "all" || task.status === statusFilter)
      .filter((task) => typeFilter === "all" || task.task_type === typeFilter)
      .filter((task) => !normalizedKeyword || `${task.task_id} ${task.display_name}`.toLowerCase().includes(normalizedKeyword))
      .filter((task) => taskMatchesDateRange(task, dateFrom, dateTo))
      .filter((task) => matchesFlag(task.degraded, degradedFilter))
      .filter((task) => matchesFlag(task.upgraded_to_full, upgradedFilter))
      .slice()
      .sort(newestFirst);
  }, [dateFrom, dateTo, degradedFilter, keyword, statusFilter, tasks, typeFilter, upgradedFilter]);

  return (
    <main className="stack-lg task-workbench">
      <SignalPanel className="stack">
        <div className="page-head">
          <SectionHeader
            eyebrow="TASK CENTER"
            title="任务中心"
            copy="高密度查看运行队列、平台负载、历史任务和结果交付入口。"
          />
          <div className="actions">
            {canUseWorkbench ? (
              <button className="button secondary" type="button" onClick={() => void loadTasks()} disabled={loading}>
                <RefreshCw size={16} />
                刷新
              </button>
            ) : null}
            <Link className="button" href="/tasks/new">
              <Plus size={16} />
              新建任务
            </Link>
          </div>
        </div>

        {error ? <p className="error">{error}</p> : null}

        <div className="metric-grid">
          <div>
            <span>运行中</span>
            <strong>{effectiveLoad.running_task_count}</strong>
          </div>
          <div>
            <span>排队中</span>
            <strong>{effectiveLoad.queued_task_count}</strong>
          </div>
          <div className="task-load-platforms">
            <span>平台可用车道</span>
            <strong>
              {filteredPlatforms.length
                ? filteredPlatforms.map(([platform, value]) => `${platform} ${value.available}/${value.total}`).join(" / ")
                : loadIssue
                  ? loadIssue.message
                  : "暂无平台数据"}
            </strong>
          </div>
        </div>
        {loadIssue?.kind === "error" ? <p className="error">{loadIssue.message}</p> : null}
      </SignalPanel>

      <AccessGatePanel session={access} title="工作台授权" description="输入周口令后，任务中心会自动加载运行队列、平台负载和历史结果。" compact />

      {canUseWorkbench ? (
        <>
          <SignalPanel className="stack">
            <div className="task-panel-head">
              <h3 className="panel-title">
                <Filter size={16} />
                筛选
              </h3>
              <StatusPill tone={loading ? "warning" : "accent"}>{loading ? "同步中" : "已同步"}</StatusPill>
            </div>
            <div className="task-filter-grid">
              <label className="field">
                <span>状态</span>
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                  <option value="all">全部</option>
                  {statusOptions.map((status) => (
                    <option key={status} value={status}>
                      {labelFor(status, statusLabels)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>类型</span>
                <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
                  <option value="all">全部</option>
                  <option value="single">单车型</option>
                  <option value="comparison">多车型对比</option>
                </select>
              </label>
              <label className="field">
                <span>关键词</span>
                <input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="任务 ID / 车型" />
              </label>
              <label className="field">
                <span>开始日期</span>
                <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
              </label>
              <label className="field">
                <span>结束日期</span>
                <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
              </label>
              <label className="field">
                <span>降级结果</span>
                <select value={degradedFilter} onChange={(event) => setDegradedFilter(event.target.value as FlagFilter)}>
                  <option value="all">全部</option>
                  <option value="yes">是</option>
                  <option value="no">否</option>
                </select>
              </label>
              <label className="field">
                <span>后续升级</span>
                <select value={upgradedFilter} onChange={(event) => setUpgradedFilter(event.target.value as FlagFilter)}>
                  <option value="all">全部</option>
                  <option value="yes">是</option>
                  <option value="no">否</option>
                </select>
              </label>
              <label className="field">
                <span>平台状态</span>
                <select value={platformStatus} onChange={(event) => setPlatformStatus(event.target.value as PlatformStatusFilter)}>
                  <option value="all">全部</option>
                  <option value="available">有空闲车道</option>
                  <option value="crowded">车道占满</option>
                </select>
              </label>
            </div>
          </SignalPanel>

          <SignalPanel className="stack">
            <div className="task-table-head">
              <div className="panel-title-wrap">
                <h3 className="panel-title">
                  <ListChecks size={16} />
                  任务列表
                </h3>
                <p className="helper">{loading ? "正在同步最新任务状态。" : `当前共 ${filteredTasks.length} 个匹配任务。`}</p>
              </div>
              {loadIssue ? (
                <StatusPill tone={loadIssue.kind === "error" ? "danger" : "warning"}>{loadIssue.message}</StatusPill>
              ) : (
                <StatusPill tone="accent">负载已同步</StatusPill>
              )}
            </div>
            <div className="task-table-wrap">
              <table className="task-table">
                <thead>
                  <tr>
                    <th>任务</th>
                    <th>类型</th>
                    <th>状态</th>
                    <th>阶段</th>
                    <th>预计</th>
                    <th>标记</th>
                    <th>创建</th>
                    <th>完成</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredTasks.map((task) => (
                    <tr key={task.task_id}>
                      <td>
                        <Link className="task-link" href={`/tasks/${task.task_id}`}>
                          <strong>{task.display_name}</strong>
                          <span>{task.task_id}</span>
                        </Link>
                      </td>
                      <td>{taskTypeLabels[task.task_type]}</td>
                      <td>
                        <StatusPill tone={statusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill>
                      </td>
                      <td>{labelFor(task.current_stage, stageLabels)}</td>
                      <td>{formatEta(task.eta_seconds)}</td>
                      <td>
                        <div className="meta-row">
                          {task.degraded ? <StatusPill tone="warning">降级</StatusPill> : null}
                          {task.upgraded_to_full ? <StatusPill tone="success">升级</StatusPill> : null}
                          {task.issue_summary ? <StatusPill tone="danger">{task.issue_summary}</StatusPill> : null}
                          {!task.degraded && !task.upgraded_to_full && !task.issue_summary ? <span>-</span> : null}
                        </div>
                      </td>
                      <td>{formatDateTime(task.created_at)}</td>
                      <td>{formatDateTime(task.completed_at)}</td>
                      <td>
                        <Link className="table-action" href={`/tasks/${task.task_id}`}>
                          <ArrowUpRight size={15} />
                          查看
                        </Link>
                      </td>
                    </tr>
                  ))}
                  {!filteredTasks.length ? (
                    <tr>
                      <td colSpan={9}>{loading ? "正在读取任务..." : "没有匹配任务。"}</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </SignalPanel>
        </>
      ) : (
        <SignalPanel className="stack" tone="accent">
          <div className="empty-state">
            <div>
              <p className="eyebrow">WORKBENCH LOCKED</p>
              <h3>解锁后加载任务和结果列表</h3>
              <p className="helper">任务中心默认就是主入口，不再跳去独立门禁页。授权完成后会直接留在当前页面。</p>
            </div>
          </div>
        </SignalPanel>
      )}
    </main>
  );
}
