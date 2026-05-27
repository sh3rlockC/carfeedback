"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { Columns3, LayoutDashboard, ListChecks, PackageCheck, Plus, Rows3 } from "lucide-react";
import { defaultDensityMode, nextDensityMode, readDensityMode, writeDensityMode, type DensityMode } from "@/lib/density";
import { getFlowState, type FlowState } from "@/lib/flow-state";
import { withoutBasePath } from "@/lib/paths";

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
  const currentPathname = withoutBasePath(pathname) ?? "/";
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

  function isNavActive(href: string) {
    if (href === "/") {
      return currentPathname === "/";
    }

    if (href === "/tasks/new") {
      return currentPathname === "/tasks/new";
    }

    if (href === "/tasks") {
      return currentPathname === "/tasks" || (currentPathname.startsWith("/tasks/") && currentPathname !== "/tasks/new");
    }

    if (href === "/result") {
      return currentPathname === "/result";
    }

    return currentPathname === href;
  }

  function navClass(href: string) {
    return isNavActive(href) ? "active" : "";
  }

  function navAriaCurrent(href: string) {
    return isNavActive(href) ? "page" : undefined;
  }

  return (
    <div className="workbench-shell" data-density={density}>
      <aside className="workbench-sidebar">
        <Link className="workbench-brand" href="/">
          <LayoutDashboard size={20} />
          <span>车型口碑工作台</span>
        </Link>
        <nav className="workbench-nav" aria-label="主导航">
          <Link className={navClass("/")} href="/" aria-current={navAriaCurrent("/")}>
            <LayoutDashboard size={17} />
            工作台总览
          </Link>
          <Link className={navClass("/tasks/new")} href="/tasks/new" aria-current={navAriaCurrent("/tasks/new")}>
            <Plus size={17} />
            新建任务
          </Link>
          <Link className={navClass("/tasks")} href="/tasks" aria-current={navAriaCurrent("/tasks")}>
            <ListChecks size={17} />
            任务中心
          </Link>
          <Link className={navClass("/result")} href="/result" aria-current={navAriaCurrent("/result")}>
            <PackageCheck size={17} />
            结果归档
          </Link>
        </nav>
      </aside>
      <div className="workbench-main">
        <header className="workbench-topbar">
          <div>
            <p className="topbar-label">当前工作区</p>
            <strong>{currentStageLabel(flowState)}</strong>
          </div>
          <div className="topbar-metrics">
            <span>{flowState.mode === "comparison" ? `${flowState.comparisonVehicles?.length ?? 0} 车对比` : flowState.vehicleQuery || "暂无运行任务"}</span>
            <span>{shortJobId(activeTaskId(flowState))}</span>
          </div>
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
        </header>
        <main className="workbench-content">{children}</main>
      </div>
    </div>
  );
}
