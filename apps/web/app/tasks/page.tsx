"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError } from "@/lib/api";
import {
  formatDateTime,
  formatEtaCell,
  labelFor,
  newestFirst,
  queuedStatuses,
  runningStatuses,
  stageLabels,
  statusLabels,
  taskListStatusTone,
  taskTypeLabels,
} from "@/lib/task-display";
import type { TaskListItem, TaskLoadResponse } from "@/lib/api-types";

type PlatformStatusFilter = "all" | "available" | "crowded";
type FlagFilter = "all" | "yes" | "no";
type LoadIssue = { kind: "missing" | "error"; message: string };

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
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [load, setLoad] = useState<TaskLoadResponse | null>(null);
  const [loadIssue, setLoadIssue] = useState<LoadIssue | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [degradedFilter, setDegradedFilter] = useState<FlagFilter>("all");
  const [upgradedFilter, setUpgradedFilter] = useState<FlagFilter>("all");
  const [platformStatus, setPlatformStatus] = useState<PlatformStatusFilter>("all");

  useEffect(() => {
    let cancelled = false;

    const loadTasks = async () => {
      setLoading(true);
      setError("");
      try {
        const taskPayload = await apiRequest<TaskListItem[]>("/api/tasks");
        if (cancelled) {
          return;
        }
        setTasks(taskPayload);

        try {
          const loadPayload = await apiRequest<TaskLoadResponse>("/api/tasks/load");
          if (!cancelled) {
            setLoad(loadPayload);
            setLoadIssue(null);
          }
        } catch (err) {
          if (!cancelled) {
            setLoad(null);
            if (err instanceof ApiError && err.status === 404) {
              setLoadIssue({ kind: "missing", message: "负载数据暂不可用" });
            } else {
              setLoadIssue({
                kind: "error",
                message: err instanceof ApiError ? `负载接口异常：${err.message}` : "负载接口异常。",
              });
            }
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "无法读取任务列表。");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadTasks();
    return () => {
      cancelled = true;
    };
  }, []);

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
        <div className="task-page-head">
          <SectionHeader eyebrow="TASK CENTER" title="任务中心" />
          <Link className="button" href="/tasks/new">
            创建任务
          </Link>
        </div>

        {error ? <p className="error">{error}</p> : null}

        <div className="task-load-row">
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

      <SignalPanel className="stack" tone="accent">
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
              <option value="single">{taskTypeLabels.single}</option>
              <option value="comparison">{taskTypeLabels.comparison}</option>
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
            <span>降级</span>
            <select value={degradedFilter} onChange={(event) => setDegradedFilter(event.target.value as FlagFilter)}>
              <option value="all">全部</option>
              <option value="yes">是</option>
              <option value="no">否</option>
            </select>
          </label>
          <label className="field">
            <span>升级</span>
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
              <option value="available">可用</option>
              <option value="crowded">拥挤</option>
            </select>
          </label>
        </div>
      </SignalPanel>

      <SignalPanel className="stack">
        <div className="task-table-head">
          <strong>{loading ? "读取中" : `${filteredTasks.length} 个任务`}</strong>
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
                <th>ETA</th>
                <th>标记</th>
                <th>创建</th>
                <th>完成</th>
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
                    <StatusPill tone={taskListStatusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill>
                  </td>
                  <td>{labelFor(task.current_stage, stageLabels)}</td>
                  <td>{formatEtaCell(task.eta_seconds)}</td>
                  <td>
                    <div className="meta-row">
                      {task.degraded ? <StatusPill tone="warning">降级</StatusPill> : null}
                      {task.upgraded_to_full ? <StatusPill tone="success">升级</StatusPill> : null}
                      {!task.degraded && !task.upgraded_to_full ? "-" : null}
                    </div>
                  </td>
                  <td>{formatDateTime(task.created_at)}</td>
                  <td>{formatDateTime(task.completed_at)}</td>
                </tr>
              ))}
              {!filteredTasks.length ? (
                <tr>
                  <td colSpan={8}>没有匹配任务。</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </SignalPanel>
    </main>
  );
}
