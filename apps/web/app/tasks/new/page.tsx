"use client";

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
    <main className="stack-lg task-workbench">
      <SignalPanel className="stack">
        <SectionHeader eyebrow="CREATE TASK" title="创建任务" />
        <div className="task-mode-grid">
          <button className={`card ${mode === "single" ? "selected" : ""}`} type="button" onClick={() => setMode("single")}>
            <h3>单车型</h3>
            <p>创建一个车型的采集与分析任务。</p>
          </button>
          <button className={`card ${mode === "comparison" ? "selected" : ""}`} type="button" onClick={() => setMode("comparison")}>
            <h3>对比</h3>
            <p>2 到 5 个车型生成对比任务。</p>
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
                    <button className="button secondary" type="button" onClick={() => removeComparisonSlot(index)}>
                      移除
                    </button>
                  ) : null}
                </div>
              ))}
              <div className="actions">
                <button className="button secondary" type="button" onClick={addComparisonSlot} disabled={comparisonQueries.length >= 5}>
                  添加车型
                </button>
              </div>
            </div>
          )}

          {error ? <p className="error">{error}</p> : null}

          <div className="actions">
            <button className="button" type="submit" disabled={!canSubmit || submitting}>
              {submitting ? "创建中" : "创建任务"}
            </button>
          </div>
        </form>
      </SignalPanel>
    </main>
  );
}
