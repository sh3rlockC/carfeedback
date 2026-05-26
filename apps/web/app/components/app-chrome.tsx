"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { Columns3, Rows3 } from "lucide-react";
import { defaultDensityMode, nextDensityMode, readDensityMode, writeDensityMode, type DensityMode } from "@/lib/density";
import { getFlowState, type FlowState } from "@/lib/flow-state";
import { StepRail, stepForPath } from "./ui";

const stageLabels: Record<string, string> = {
  queued: "排队中",
  collecting_autohome: "汽车之家采集",
  collecting_dcd: "懂车帝采集",
  postprocessing: "汇总整理",
  summarizing: "摘要生成",
  rendering_wordcloud: "词云生成",
  generating_ai_report: "AI 一页纸",
  building_qa_corpus: "问答索引",
  collecting_models: "补齐车型",
  comparing: "竞品对比",
  completed: "已完成",
  completed_degraded: "降级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

const emptyFlowState: FlowState = {
  accessVersion: null,
  mode: null,
  vehicleQuery: null,
  vehicleResolve: null,
  selectedCandidates: null,
  jobId: null,
  jobProgress: null,
  comparisonId: null,
  comparisonOptions: null,
  comparisonVehicles: null,
  comparisonProgress: null,
};

function shortJobId(jobId: string | null) {
  if (!jobId) {
    return "未创建";
  }
  return jobId.length > 12 ? `${jobId.slice(0, 8)}...${jobId.slice(-4)}` : jobId;
}

function currentStageLabel(state: FlowState) {
  if (state.mode === "comparison") {
    const comparisonStage = state.comparisonProgress?.current_stage;
    if (!comparisonStage) {
      return state.comparisonId ? "等待对比进度" : "未启动";
    }
    return stageLabels[comparisonStage] ?? comparisonStage;
  }
  const stage = state.jobProgress?.current_stage;
  if (!stage) {
    return state.jobId ? "等待进度" : "未启动";
  }
  return stageLabels[stage] ?? stage;
}

function activeTaskId(state: FlowState) {
  return state.mode === "comparison" ? state.comparisonId : state.jobId;
}

export function AppChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const activeStep = stepForPath(pathname);
  const [flowState, setLocalFlowState] = useState<FlowState>(emptyFlowState);
  const [density, setDensity] = useState<DensityMode>(defaultDensityMode);

  useEffect(() => {
    const refresh = () => setLocalFlowState(getFlowState());
    refresh();
    window.addEventListener("focus", refresh);
    window.addEventListener("storage", refresh);
    const intervalId = window.setInterval(refresh, 1600);

    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("storage", refresh);
      window.clearInterval(intervalId);
    };
  }, [pathname]);

  useEffect(() => {
    setDensity(readDensityMode());
  }, []);

  function toggleDensity() {
    setDensity((current) => {
      const next = nextDensityMode(current);
      writeDensityMode(next);
      return next;
    });
  }

  return (
    <div className="app-shell workbench-shell" data-density={density}>
      <header className="command-bar">
        <div className="brand-lockup">
          <p className="eyebrow">VEHICLE KOUBEI INTEL</p>
          <h1>车型口碑情报舱</h1>
          <p className="topbar-copy">双平台采集、AI 一页纸、词云和智能问答的内部演示工作台。</p>
        </div>

        <div className="mission-status" aria-label="当前任务状态">
          <div>
            <span>车型</span>
            <strong>{flowState.mode === "comparison" ? `${flowState.comparisonVehicles?.length ?? 0} 车对比` : flowState.vehicleQuery || "待输入"}</strong>
          </div>
          <div>
            <span>任务</span>
            <strong>{shortJobId(activeTaskId(flowState))}</strong>
          </div>
          <div>
            <span>阶段</span>
            <strong>{currentStageLabel(flowState)}</strong>
          </div>
          <div>
            <span>访问</span>
            <strong>{flowState.accessVersion ? "已授权" : "待口令"}</strong>
          </div>
        </div>
      </header>

      <nav className="utility-nav" aria-label="全局导航">
        <button
          className="icon-text-button"
          type="button"
          onClick={toggleDensity}
          aria-label={`切换显示密度，当前为${density === "compact" ? "紧凑" : "标准"}`}
          aria-pressed={density === "compact"}
        >
          {density === "compact" ? <Rows3 size={16} /> : <Columns3 size={16} />}
          {density === "compact" ? "紧凑" : "标准"}
        </button>
        <Link href="/tasks">任务中心</Link>
      </nav>

      <StepRail activeStep={activeStep} />

      <div className="content-stage">{children}</div>
    </div>
  );
}
