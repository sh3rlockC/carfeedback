"use client";

import { GitCompareArrows, Plus, Search, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useMemo, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError, toJsonBody } from "@/lib/api";
import type { TaskCreateRequest, TaskCreateResponse } from "@/lib/api-types";

type CreateMode = "single" | "comparison";

const comparisonSlots = [0, 1, 2, 3, 4];

function normalizeManageUrl(manageUrl: string) {
  try {
    const parsed = new URL(manageUrl, window.location.origin);
    const segments = parsed.pathname.split("/").filter(Boolean);
    const tasksIndex = segments.indexOf("tasks");
    const taskId = tasksIndex >= 0 ? segments[tasksIndex + 1] : "";
    const manageToken = parsed.searchParams.get("manage_token");
    if (taskId) {
      return `/tasks/${taskId}${manageToken ? `?manage_token=${encodeURIComponent(manageToken)}` : ""}`;
    }
  } catch {
    return manageUrl.replace(/\/manage(\?manage_token=)/, "$1");
  }
  return manageUrl.replace(/\/manage(\?manage_token=)/, "$1");
}

export default function NewTaskPage() {
  const router = useRouter();
  const [mode, setMode] = useState<CreateMode>("single");
  const [singleQuery, setSingleQuery] = useState("");
  const [comparisonQueries, setComparisonQueries] = useState(["", ""]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const comparisonVehicles = useMemo(() => comparisonQueries.map((query) => query.trim()).filter(Boolean), [comparisonQueries]);
  const canSubmit = mode === "single" ? Boolean(singleQuery.trim()) : comparisonVehicles.length >= 2 && comparisonVehicles.length <= 5;

  const updateComparisonQuery = (index: number, value: string) => {
    setComparisonQueries((current) => {
      const next = [...current];
      next[index] = value;
      return next;
    });
  };

  const addComparisonSlot = () => {
    setComparisonQueries((current) => (current.length >= 5 ? current : [...current, ""]));
  };

  const removeComparisonSlot = (index: number) => {
    setComparisonQueries((current) => current.filter((_, itemIndex) => itemIndex !== index));
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) {
      setError(mode === "single" ? "请输入车型。" : "对比任务需要 2 到 5 个车型。");
      return;
    }

    const payload: TaskCreateRequest =
      mode === "single"
        ? { task_type: "single", vehicles: [{ query: singleQuery.trim() }] }
        : { task_type: "comparison", vehicles: comparisonVehicles.map((query) => ({ query })) };

    setSubmitting(true);
    setError("");
    try {
      const response = await apiRequest<TaskCreateResponse>("/api/tasks", {
        method: "POST",
        body: toJsonBody(payload),
      });
      router.push(normalizeManageUrl(response.manage_url));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "任务创建失败。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="stack-lg task-workbench task-create-layout">
      <SignalPanel className="stack">
        <SectionHeader eyebrow="CREATE TASK" title="创建任务" />
        <div className="task-mode-grid" role="group" aria-label="任务类型">
          <button
            className={`mode-card ${mode === "single" ? "selected" : ""}`}
            type="button"
            onClick={() => setMode("single")}
            aria-pressed={mode === "single"}
          >
            <Search size={18} aria-hidden="true" />
            <span>
              <strong>单车型查询</strong>
              <small>一个车型的增量采集、报告和交付物。</small>
            </span>
          </button>
          <button
            className={`mode-card ${mode === "comparison" ? "selected" : ""}`}
            type="button"
            onClick={() => setMode("comparison")}
            aria-pressed={mode === "comparison"}
          >
            <GitCompareArrows size={18} aria-hidden="true" />
            <span>
              <strong>多车型对比</strong>
              <small>2 到 5 个车型并行采集并生成对比结果。</small>
            </span>
          </button>
        </div>
      </SignalPanel>

      <SignalPanel tone="accent">
        <form className="stack-lg" onSubmit={handleSubmit}>
          {mode === "single" ? (
            <label className="field">
              <span>车型</span>
              <input value={singleQuery} onChange={(event) => setSingleQuery(event.target.value)} placeholder="输入车型名称" />
            </label>
          ) : (
            <div className="stack">
              <div className="task-form-head">
                <strong>对比车型</strong>
                <StatusPill tone={comparisonVehicles.length >= 2 ? "success" : "warning"}>{comparisonVehicles.length}/5</StatusPill>
              </div>
              {comparisonQueries.map((query, index) => (
                <div className="task-vehicle-row" key={`${index}-${comparisonSlots[index] ?? index}`}>
                  <label className="field">
                    <span>车型 {index + 1}</span>
                    <input value={query} onChange={(event) => updateComparisonQuery(index, event.target.value)} placeholder="输入车型名称" />
                  </label>
                  {comparisonQueries.length > 2 ? (
                    <button className="button secondary task-remove-button" type="button" onClick={() => removeComparisonSlot(index)} aria-label={`移除车型 ${index + 1}`}>
                      <Trash2 size={16} aria-hidden="true" />
                    </button>
                  ) : null}
                </div>
              ))}
              <div className="actions">
                <button className="button secondary" type="button" onClick={addComparisonSlot} disabled={comparisonQueries.length >= 5}>
                  <Plus size={16} aria-hidden="true" />
                  添加车型
                </button>
              </div>
            </div>
          )}

          <p className="helper task-create-helper">系统会先对照历史语料库，再采集新增评论。多车型任务会进入可用车道排队。</p>

          {error ? <p className="error">{error}</p> : null}

          <div className="actions">
            <button className="button" type="submit" disabled={!canSubmit || submitting}>
              <Plus size={16} aria-hidden="true" />
              {submitting ? "正在创建" : "创建增量采集任务"}
            </button>
          </div>
        </form>
      </SignalPanel>
    </main>
  );
}
