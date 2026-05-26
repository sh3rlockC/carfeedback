"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowRight, CheckCircle2, Clock3, Gauge, ListChecks, LoaderCircle, Plus, Route, Timer } from "lucide-react";
import { StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError } from "@/lib/api";
import type { TaskListItem, TaskLoadResponse } from "@/lib/api-types";
import {
  formatDateTime,
  formatEtaCell,
  labelFor,
  newestFirst,
  queuedStatuses,
  runningStatuses,
  stageLabels,
  statusLabels,
  statusTone,
  taskTypeLabels,
} from "@/lib/task-display";

type LoadIssue = { kind: "missing" | "error"; message: string };

function fallbackLoad(tasks: TaskListItem[]): TaskLoadResponse {
  return {
    running_task_count: tasks.filter((task) => runningStatuses.has(task.status)).length,
    queued_task_count: tasks.filter((task) => queuedStatuses.has(task.status)).length,
    platforms: {},
  };
}

function completedAtFirst(a: TaskListItem, b: TaskListItem) {
  const left = new Date(a.completed_at ?? a.created_at).getTime();
  const right = new Date(b.completed_at ?? b.created_at).getTime();
  return (Number.isNaN(right) ? 0 : right) - (Number.isNaN(left) ? 0 : left);
}

export default function WorkbenchOverviewPage() {
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [load, setLoad] = useState<TaskLoadResponse | null>(null);
  const [loadIssue, setLoadIssue] = useState<LoadIssue | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    const loadOverview = async () => {
      setLoading(true);
      setError("");
      setLoadIssue(null);

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
          }
        } catch (err) {
          if (!cancelled) {
            setLoad(null);
            setLoadIssue(
              err instanceof ApiError && err.status === 404
                ? { kind: "missing", message: "负载接口暂不可用，已按任务列表估算。" }
                : { kind: "error", message: err instanceof ApiError ? `负载接口异常：${err.message}` : "负载接口异常，已按任务列表估算。" },
            );
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "无法读取任务列表。");
          setTasks([]);
          setLoad(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadOverview();
    return () => {
      cancelled = true;
    };
  }, []);

  const effectiveLoad = load ?? fallbackLoad(tasks);

  const sortedTasks = useMemo(() => tasks.slice().sort(newestFirst), [tasks]);
  const runningTasks = useMemo(() => sortedTasks.filter((task) => runningStatuses.has(task.status)).slice(0, 6), [sortedTasks]);
  const queuedTasks = useMemo(() => sortedTasks.filter((task) => queuedStatuses.has(task.status)).slice(0, 6), [sortedTasks]);
  const recentCompleted = useMemo(
    () =>
      tasks
        .filter((task) => task.completed_at || task.status === "completed" || task.status === "completed_degraded")
        .slice()
        .sort(completedAtFirst)
        .slice(0, 4),
    [tasks],
  );
  const availableLaneCount = useMemo(
    () => Object.values(effectiveLoad.platforms).reduce((total, platform) => total + platform.available, 0),
    [effectiveLoad.platforms],
  );
  const totalLaneCount = useMemo(
    () => Object.values(effectiveLoad.platforms).reduce((total, platform) => total + platform.total, 0),
    [effectiveLoad.platforms],
  );
  const platformEntries = useMemo(() => Object.entries(effectiveLoad.platforms).sort(([left], [right]) => left.localeCompare(right)), [effectiveLoad.platforms]);
  const degradedCount = useMemo(() => tasks.filter((task) => task.degraded).length, [tasks]);
  const upgradedCount = useMemo(() => tasks.filter((task) => task.upgraded_to_full).length, [tasks]);

  return (
    <div className="overview-page">
      <section className="overview-hero" aria-labelledby="overview-title">
        <div>
          <p className="eyebrow">WORKBENCH OVERVIEW</p>
          <h1 id="overview-title">工作台总览</h1>
          <p>集中查看任务负载、车道余量、运行队列和最近交付结果。</p>
        </div>
        <div className="overview-hero-actions" aria-label="工作台操作">
          <Link className="button" href="/tasks/new">
            <Plus size={16} />
            新建任务
          </Link>
          <Link className="button secondary" href="/tasks">
            <ListChecks size={16} />
            任务中心
          </Link>
        </div>
      </section>

      {error ? (
        <section className="flat-panel overview-alert" aria-live="polite">
          <AlertCircle size={18} />
          <span>{error}</span>
        </section>
      ) : null}

      <section className="metric-grid" aria-label="任务概览指标">
        <article className="metric-tile">
          <div className="metric-icon">
            <LoaderCircle size={18} />
          </div>
          <span>运行中</span>
          <strong>{loading ? "-" : effectiveLoad.running_task_count}</strong>
          <p>{runningTasks[0] ? labelFor(runningTasks[0].current_stage, stageLabels) : "暂无运行任务"}</p>
        </article>
        <article className="metric-tile">
          <div className="metric-icon">
            <Clock3 size={18} />
          </div>
          <span>排队中</span>
          <strong>{loading ? "-" : effectiveLoad.queued_task_count}</strong>
          <p>{queuedTasks[0] ? `${queuedTasks[0].display_name} 等待调度` : "队列空闲"}</p>
        </article>
        <article className="metric-tile">
          <div className="metric-icon">
            <Route size={18} />
          </div>
          <span>可用车道</span>
          <strong>
            {loading ? "-" : totalLaneCount ? `${availableLaneCount}/${totalLaneCount}` : "未知"}
          </strong>
          <p>{loadIssue ? loadIssue.message : platformEntries.length ? "负载已同步" : "暂无平台负载数据"}</p>
        </article>
        <article className="metric-tile">
          <div className="metric-icon">
            <CheckCircle2 size={18} />
          </div>
          <span>最近完成</span>
          <strong>{loading ? "-" : recentCompleted.length}</strong>
          <p>
            降级 {degradedCount} · 升级 {upgradedCount}
          </p>
        </article>
      </section>

      <section className="overview-grid">
        <section className="flat-panel" aria-labelledby="running-title">
          <div className="panel-head">
            <div>
              <p className="eyebrow">LIVE LANES</p>
              <h2 id="running-title">运行任务</h2>
            </div>
            <StatusPill tone={runningTasks.length ? "accent" : "success"}>{runningTasks.length ? "处理中" : "空闲"}</StatusPill>
          </div>
          <div className="overview-table-wrap">
            <table className="overview-table">
              <thead>
                <tr>
                  <th>任务</th>
                  <th>类型</th>
                  <th>阶段</th>
                  <th>ETA</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {runningTasks.map((task) => (
                  <tr key={task.task_id}>
                    <td>
                      <strong>{task.display_name}</strong>
                      <span>{task.task_id}</span>
                    </td>
                    <td>{taskTypeLabels[task.task_type]}</td>
                    <td>{labelFor(task.current_stage, stageLabels)}</td>
                    <td>{formatEtaCell(task.eta_seconds)}</td>
                    <td>
                      <Link className="table-action" href={`/tasks/${task.task_id}`} aria-label={`查看 ${task.display_name}`}>
                        <ArrowRight size={15} />
                      </Link>
                    </td>
                  </tr>
                ))}
                {!runningTasks.length ? (
                  <tr>
                    <td colSpan={5}>{loading ? "正在读取任务。" : "暂无运行任务。"}</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>

        <section className="flat-panel" aria-labelledby="queue-title">
          <div className="panel-head">
            <div>
              <p className="eyebrow">QUEUE</p>
              <h2 id="queue-title">排队任务</h2>
            </div>
            <StatusPill tone={queuedTasks.length ? "warning" : "success"}>{queuedTasks.length ? `${queuedTasks.length} 个等待` : "无等待"}</StatusPill>
          </div>
          <div className="queue-list">
            {queuedTasks.map((task) => (
              <Link className="queue-row" href={`/tasks/${task.task_id}`} key={task.task_id}>
                <div>
                  <strong>{task.display_name}</strong>
                  <span>
                    {taskTypeLabels[task.task_type]} · {labelFor(task.status, statusLabels)}
                  </span>
                </div>
                <StatusPill tone={statusTone(task.status)}>{labelFor(task.current_stage, stageLabels)}</StatusPill>
              </Link>
            ))}
            {!queuedTasks.length ? <p className="empty-copy">{loading ? "正在读取队列。" : "队列当前没有等待任务。"}</p> : null}
          </div>
        </section>

        <section className="flat-panel lane-panel" aria-labelledby="lanes-title">
          <div className="panel-head">
            <div>
              <p className="eyebrow">CAPACITY</p>
              <h2 id="lanes-title">可用车道</h2>
            </div>
            <StatusPill tone={loadIssue?.kind === "error" ? "warning" : "accent"}>{loadIssue ? "估算" : "实时"}</StatusPill>
          </div>
          <div className="lane-list">
            {platformEntries.map(([platform, value]) => (
              <div className="lane-row" key={platform}>
                <div>
                  <strong>{platform}</strong>
                  <span>
                    {value.available} 可用 / {value.total} 总量
                  </span>
                </div>
                <Gauge size={18} />
              </div>
            ))}
            {!platformEntries.length ? <p className="empty-copy">{loadIssue?.message ?? "暂无平台车道数据。"}</p> : null}
          </div>
        </section>

        <section className="flat-panel quick-create-panel" aria-labelledby="quick-create-title">
          <div className="panel-head">
            <div>
              <p className="eyebrow">CREATE</p>
              <h2 id="quick-create-title">快速入口</h2>
            </div>
          </div>
          <Link className="quick-create-link" href="/tasks/new">
            <Plus size={18} />
            <div>
              <strong>创建单车型或多车型对比</strong>
              <span>进入任务创建页，提交后在总览中跟踪队列与结果。</span>
            </div>
          </Link>
          <Link className="quick-create-link" href="/tasks">
            <ListChecks size={18} />
            <div>
              <strong>筛选全部任务</strong>
              <span>按状态、类型、日期、降级和升级标记查看完整列表。</span>
            </div>
          </Link>
        </section>
      </section>

      <section className="flat-panel" aria-labelledby="recent-title">
        <div className="panel-head">
          <div>
            <p className="eyebrow">RECENT RESULTS</p>
            <h2 id="recent-title">最近完成</h2>
          </div>
          <Link className="table-action table-action-wide" href="/tasks">
            全部任务
            <ArrowRight size={15} />
          </Link>
        </div>
        <div className="recent-result-grid">
          {recentCompleted.map((task) => (
            <Link className="result-card" href={`/tasks/${task.task_id}`} key={task.task_id}>
              <div className="result-card-head">
                <strong>{task.display_name}</strong>
                <StatusPill tone={statusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill>
              </div>
              <dl>
                <div>
                  <dt>类型</dt>
                  <dd>{taskTypeLabels[task.task_type]}</dd>
                </div>
                <div>
                  <dt>完成</dt>
                  <dd>{formatDateTime(task.completed_at)}</dd>
                </div>
                <div>
                  <dt>阶段</dt>
                  <dd>{labelFor(task.current_stage, stageLabels)}</dd>
                </div>
              </dl>
              <div className="result-card-foot">
                {task.degraded ? <StatusPill tone="warning">降级</StatusPill> : null}
                {task.upgraded_to_full ? <StatusPill tone="success">升级</StatusPill> : null}
                {!task.degraded && !task.upgraded_to_full ? <StatusPill>标准结果</StatusPill> : null}
                <Timer size={15} />
              </div>
            </Link>
          ))}
          {!recentCompleted.length ? <p className="empty-copy">{loading ? "正在读取完成结果。" : "暂无完成任务。"}</p> : null}
        </div>
      </section>
    </div>
  );
}
