"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError } from "@/lib/api";
import type { ComparisonProgressResponse, JobProgressResponse } from "@/lib/api-types";
import { getFlowState, setFlowState } from "@/lib/flow-state";

const terminalStatuses = new Set(["completed", "completed_degraded", "failed", "cancelled", "expired"]);

const stageLabels: Record<string, string> = {
  queued: "排队中",
  checking_incremental: "检查历史语料",
  collecting_autohome: "采集汽车之家",
  collecting_dcd: "采集懂车帝",
  postprocessing: "汇总整理",
  summarizing: "生成摘要",
  rendering_wordcloud: "生成词云",
  generating_ai_report: "生成大模型一页纸",
  building_qa_corpus: "构建问答索引",
  collecting_models: "补齐车型结果",
  comparing: "生成竞品对比",
  completed: "已完成",
  completed_degraded: "降级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

const statusLabels: Record<string, string> = {
  waiting: "等待中",
  queued: "排队中",
  running: "运行中",
  success: "成功",
  retrying: "重试中",
  degraded: "已降级",
  failed: "失败",
  completed: "已完成",
  completed_degraded: "降级完成",
  reused: "复用历史结果",
  excluded: "已排除",
};

const pipelineStages = [
  "postprocessing",
  "summarizing",
  "rendering_wordcloud",
  "generating_ai_report",
  "building_qa_corpus",
];

function labelFor(value: string, labels: Record<string, string>) {
  return labels[value] ?? value;
}

function collectionStage(current: JobProgressResponse | null | undefined, name: "collecting_autohome" | "collecting_dcd") {
  const stage = current?.stages.find((item) => item.name === name);
  const percent = Math.max(0, Math.min(100, stage?.progress_percent ?? (stage?.status === "success" ? 100 : 0)));
  return {
    name,
    label: labelFor(name, stageLabels),
    agentId: name === "collecting_autohome" ? "autohome" : "dongchedi",
    status: stage?.status ?? "waiting",
    percent,
    attemptNo: stage?.attempt_no ?? 0,
    message: stage?.progress_message || (stage ? `第 ${stage.attempt_no} 次尝试` : "已投递/等待 agent 响应"),
  };
}

function stageSummary(current: JobProgressResponse | null | undefined, name: string) {
  const stage = current?.stages.find((item) => item.name === name);
  return {
    name,
    label: labelFor(name, stageLabels),
    status: stage?.status ?? "waiting",
    message: stage?.progress_message || stage?.error_message || "等待上游产物",
  };
}

function statusTone(status: string): "default" | "success" | "warning" | "danger" | "accent" {
  if (["success", "completed", "reused"].includes(status)) {
    return "success";
  }
  if (["retrying", "degraded", "queued", "waiting"].includes(status)) {
    return "warning";
  }
  if (["failed", "cancelled", "expired", "excluded"].includes(status)) {
    return "danger";
  }
  if (status === "running") {
    return "accent";
  }
  return "default";
}

export default function ProgressPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [progress, setProgress] = useState<JobProgressResponse | null>(null);
  const [comparisonProgress, setComparisonProgress] = useState<ComparisonProgressResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) {
      return;
    }

    const state = getFlowState();
    if (state.mode === "comparison") {
      if (!state.comparisonId) {
        return;
      }

      let cancelled = false;
      let intervalId = 0;

      const fetchProgress = async () => {
        try {
          const payload = await apiRequest<ComparisonProgressResponse>(`/api/comparisons/${state.comparisonId}/progress`);
          if (cancelled) {
            return;
          }
          setComparisonProgress(payload);
          setFlowState({ comparisonProgress: payload });
          if (terminalStatuses.has(payload.status)) {
            window.clearInterval(intervalId);
            router.push("/result");
          }
        } catch (err) {
          if (cancelled) {
            return;
          }
          setError(err instanceof ApiError ? err.message : "无法读取竞品对比进度。");
        }
      };

      void fetchProgress();
      intervalId = window.setInterval(() => {
        void fetchProgress();
      }, 2000);

      return () => {
        cancelled = true;
        window.clearInterval(intervalId);
      };
    }

    if (!state.jobId) {
      return;
    }

    let cancelled = false;
    let intervalId = 0;

    const fetchProgress = async () => {
      try {
        const payload = await apiRequest<JobProgressResponse>(`/api/jobs/${state.jobId}/progress`);
        if (cancelled) {
          return;
        }

        setProgress(payload);
        setFlowState({ jobProgress: payload });

        if (terminalStatuses.has(payload.status)) {
          window.clearInterval(intervalId);
          router.push("/result");
        }
      } catch (err) {
        if (cancelled) {
          return;
        }

        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("无法读取任务进度。");
        }
      }
    };

    void fetchProgress();
    intervalId = window.setInterval(() => {
      void fetchProgress();
    }, 2000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [ready, router]);

  if (!ready) {
    return <main className="panel guard">正在加载...</main>;
  }

  const flowState = getFlowState();
  if (flowState.mode === "comparison") {
    if (!flowState.comparisonId) {
      return (
        <main className="panel guard">
          <p className="eyebrow">第 4 步 / 共 5 步</p>
          <h2>需要先创建竞品对比任务</h2>
          <p className="helper">请先在多车型确认页创建任务，再查看进度。</p>
          <div className="actions">
            <Link className="button" href="/candidates">
              返回候选确认
            </Link>
            <Link className="button secondary" href="/tasks/new">
              前往新建任务
            </Link>
          </div>
        </main>
      );
    }

    const currentComparison = comparisonProgress ?? flowState.comparisonProgress;
    return (
      <main className="stack-lg">
        <SignalPanel tone="accent" className="stack-lg">
          <SectionHeader
            eyebrow="LEGACY FLOW"
            title="多车型对比进度"
            copy="这是旧流程兼容页面。新查询建议从任务中心创建。"
          />
          <div className="actions">
            <Link className="button secondary" href="/tasks/new">
              前往新建任务
            </Link>
          </div>
          {error ? <p className="error">{error}</p> : null}
          <div className="stack">
            <div className="bar" aria-hidden="true">
              <span style={{ width: `${currentComparison?.overall_percent ?? 0}%` }} />
            </div>
            <div className="meta-row">
              <StatusPill tone={statusTone(currentComparison?.status ?? "waiting")}>
                {currentComparison ? `${currentComparison.overall_percent}%` : "等待进度"}
              </StatusPill>
              <StatusPill tone="warning">{currentComparison?.eta_label ?? "预计剩余时间计算中"}</StatusPill>
              <StatusPill tone="accent">
                {currentComparison ? labelFor(currentComparison.current_stage, stageLabels) : "等待第一条进度"}
              </StatusPill>
            </div>
            <p className="status-copy">{currentComparison?.message || "竞品对比进度会显示在这里。"}</p>
          </div>
        </SignalPanel>

        <div className="pipeline-strip">
          {(currentComparison?.vehicles ?? []).map((vehicle, index) => (
            <div key={`${vehicle.query}-${index}`} className="pipeline-node">
              <strong>{vehicle.model_name || vehicle.query}</strong>
              <StatusPill tone={statusTone(vehicle.status)}>{labelFor(vehicle.status, statusLabels)}</StatusPill>
              <p className="field-hint" style={{ marginTop: 10 }}>
                {vehicle.eta_label}
              </p>
              {vehicle.source_job_id ? <p className="field-hint">复用：{vehicle.source_job_id}</p> : null}
              {vehicle.child_job_id ? <p className="field-hint">采集：{vehicle.child_job_id}</p> : null}
              {vehicle.error_message ? <p className="error">{vehicle.error_message}</p> : null}
            </div>
          ))}
        </div>
      </main>
    );
  }

  if (!flowState.jobId) {
    return (
      <main className="panel guard">
        <p className="eyebrow">第 4 步 / 共 5 步</p>
        <h2>需要先创建任务</h2>
        <p className="helper">请先在候选确认页创建任务，再查看进度。</p>
        <div className="actions">
          <Link className="button" href="/candidates">
            返回候选确认
          </Link>
          <Link className="button secondary" href="/tasks/new">
            前往新建任务
          </Link>
        </div>
      </main>
    );
  }

  const current = progress ?? flowState.jobProgress;
  const collectionProgress = [
    collectionStage(current, "collecting_autohome"),
    collectionStage(current, "collecting_dcd"),
  ];

  const pipelineProgress = pipelineStages.map((stageName) => stageSummary(current, stageName));

  return (
    <main className="stack-lg">
      <SignalPanel tone="accent" className="stack-lg">
        <SectionHeader
          eyebrow="LEGACY FLOW"
          title="任务执行进度"
          copy="这是旧流程兼容页面。新查询建议从任务中心创建。"
        />
        <div className="actions">
          <Link className="button secondary" href="/tasks/new">
            前往新建任务
          </Link>
        </div>

        {error ? <p className="error">{error}</p> : null}

        <div className="stack">
          <div className="bar" aria-hidden="true">
            <span style={{ width: `${current?.overall_percent ?? 0}%` }} />
          </div>
          <div className="meta-row">
            <StatusPill tone={statusTone(current?.status ?? "waiting")}>
              {current ? `${current.overall_percent}%` : "等待进度"}
            </StatusPill>
            <StatusPill>{current ? labelFor(current.status, statusLabels) : "未返回状态"}</StatusPill>
            <StatusPill tone="accent">
              {current ? labelFor(current.current_stage, stageLabels) : "等待第一条进度"}
            </StatusPill>
            <StatusPill tone="warning">{current?.eta_label ?? "预计剩余时间计算中"}</StatusPill>
          </div>
          <p className="status-copy">{current?.message || "后端进度会显示在这里。"}</p>
        </div>

        <div className="collector-grid">
          {collectionProgress.map((stage) => (
            <SignalPanel key={stage.name} className="collector-card" tone={statusTone(stage.status)} aria-label={`${stage.label}进度`}>
              <div className="collector-head">
                <div>
                  <p className="eyebrow">AGENT_ID={stage.agentId}</p>
                  <h3>{stage.label}</h3>
                  <p>{labelFor(stage.status, statusLabels)} · 第 {stage.attemptNo || 1} 次尝试</p>
                </div>
                <strong>{stage.percent}%</strong>
              </div>
              <div
                className="collector-bar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={stage.percent}
              >
                <span style={{ width: `${stage.percent}%` }} />
              </div>
              <p className="collector-message">{stage.message}</p>
            </SignalPanel>
          ))}
        </div>
      </SignalPanel>

      <div className="pipeline-strip">
        {pipelineProgress.map((stage) => (
          <div key={stage.name} className="pipeline-node">
            <strong>{stage.label}</strong>
            <StatusPill tone={statusTone(stage.status)}>{labelFor(stage.status, statusLabels)}</StatusPill>
            <p className="field-hint" style={{ marginTop: 10 }}>
              {stage.message}
            </p>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>阶段明细</h3>
        <div className="timeline">
          {(current?.stages ?? []).map((stage) => (
            <div key={`${stage.name}-${stage.attempt_no}`} className="timeline-item">
              <span className="timeline-dot" />
              <div>
                <strong>
                  {labelFor(stage.name, stageLabels)} · {labelFor(stage.status, statusLabels)}
                </strong>
                <p className="status-copy">第 {stage.attempt_no} 次尝试</p>
                {stage.error_message ? <p className="error">{stage.error_message}</p> : null}
              </div>
            </div>
          ))}
        </div>
      </div>
    </main>
  );
}
