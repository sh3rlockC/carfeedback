"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useEffect, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError, toJsonBody } from "@/lib/api";
import type { ComparisonOptionsResponse, VehicleResolveResponse } from "@/lib/api-types";
import { getFlowState, setFlowState } from "@/lib/flow-state";

const exampleVehicles = ["风云T11", "风云X3L", "风云T9L", "QQ3"];

export default function VehiclePage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"single" | "comparison">("single");
  const [comparisonQueries, setComparisonQueries] = useState(["", ""]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setReady(true);
    const state = getFlowState();
    if (state.accessVersion) {
      setMode(state.mode === "comparison" ? "comparison" : "single");
      setQuery(state.vehicleQuery ?? "");
      if (state.comparisonVehicles?.length) {
        setComparisonQueries(state.comparisonVehicles.map((vehicle) => vehicle.query));
      }
    }
  }, []);

  if (!ready) {
    return <main className="panel guard">正在加载...</main>;
  }

  const flowState = getFlowState();
  if (!flowState.accessVersion) {
    return (
      <main className="panel guard">
        <p className="eyebrow">第 2 步 / 共 5 步</p>
        <h2>旧流程已并入工作台</h2>
        <p className="helper">当前版本可直接进入新建任务，无需单独校验。</p>
        <div className="actions">
          <Link className="button" href="/">
            返回工作台
          </Link>
          <Link className="button secondary" href="/tasks/new">
            前往新建任务
          </Link>
        </div>
      </main>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      setError("请先输入车型名称。");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const payload = await apiRequest<VehicleResolveResponse>("/api/vehicles/resolve", {
        method: "POST",
        body: toJsonBody({ query: trimmed }),
      });

      setFlowState({
        mode: "single",
        vehicleQuery: trimmed,
        vehicleResolve: payload,
        selectedCandidates: null,
        jobId: null,
        jobProgress: null,
        comparisonId: null,
        comparisonOptions: null,
        comparisonVehicles: null,
        comparisonProgress: null,
      });
      router.push("/candidates");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("访问会话已过期，请返回工作台重新发起任务。");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("无法识别车型候选，请稍后重试或使用手动兜底。");
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleComparisonSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const vehicles = comparisonQueries.map((item) => item.trim()).filter(Boolean);
    if (vehicles.length < 2) {
      setError("多车型竞品对比至少需要 2 个车型。");
      return;
    }
    if (new Set(vehicles.map((item) => item.toLowerCase())).size !== vehicles.length) {
      setError("请不要重复输入同一个车型。");
      return;
    }

    setLoading(true);
    setError("");
    try {
      const payload = await apiRequest<ComparisonOptionsResponse>("/api/comparisons/options", {
        method: "POST",
        body: toJsonBody({ vehicles: vehicles.map((vehicle) => ({ query: vehicle })) }),
      });
      setFlowState({
        mode: "comparison",
        vehicleQuery: vehicles.join(" / "),
        vehicleResolve: null,
        selectedCandidates: null,
        jobId: null,
        jobProgress: null,
        comparisonId: null,
        comparisonOptions: payload,
        comparisonVehicles: null,
        comparisonProgress: null,
      });
      if (startDate || endDate) {
        window.sessionStorage.setItem("koubei-comparison-date-range", JSON.stringify({ startDate, endDate }));
      } else {
        window.sessionStorage.removeItem("koubei-comparison-date-range");
      }
      router.push("/candidates");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("访问会话已过期，请返回工作台重新发起任务。");
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("无法查询竞品对比选项，请稍后重试。");
      }
    } finally {
      setLoading(false);
    }
  }

  function updateComparisonQuery(index: number, value: string) {
    setComparisonQueries((current) => current.map((item, itemIndex) => (itemIndex === index ? value : item)));
  }

  return (
    <main className="page-grid">
      <SignalPanel tone="accent" className="stack-lg">
        <SectionHeader
          eyebrow="LEGACY FLOW"
          title="旧流程车型输入"
          copy="这是旧流程兼容页面。新查询建议从任务中心创建。"
        />
        <div className="actions">
          <Link className="button secondary" href="/tasks/new">
            前往新建任务
          </Link>
        </div>

        <div className="meta-row">
          <button className={`quick-chip ${mode === "single" ? "selected" : ""}`} type="button" onClick={() => setMode("single")}>
            单车型评论收集
          </button>
          <button className={`quick-chip ${mode === "comparison" ? "selected" : ""}`} type="button" onClick={() => setMode("comparison")}>
            多车型竞品对比
          </button>
        </div>

        {mode === "single" ? (
        <form className="stack" onSubmit={handleSubmit}>
          <div className="field hero-input">
            <label htmlFor="query">车型名称</label>
            <input
              id="query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="输入车型，例如 风云T11"
            />
            <p className="field-hint">已确认过的车型会优先复用缓存；识别失败时下一步可以手动填写平台车系编号。</p>
          </div>

          <div className="quick-actions" aria-label="示例车型">
            {exampleVehicles.map((vehicle) => (
              <button key={vehicle} className="quick-chip" type="button" onClick={() => setQuery(vehicle)}>
                {vehicle}
              </button>
            ))}
          </div>

          {error ? <p className="error">{error}</p> : null}

          <div className="actions">
            <button className="button" type="submit" disabled={loading || !query.trim()}>
              {loading ? "正在识别车系" : "进入车系确认"}
            </button>
          </div>
        </form>
        ) : (
        <form className="stack" onSubmit={handleComparisonSubmit}>
          <div className="split-grid">
            {comparisonQueries.map((vehicle, index) => (
              <div className="field" key={`comparison-${index}`}>
                <label htmlFor={`comparison-${index}`}>车型 {index + 1}</label>
                <input
                  id={`comparison-${index}`}
                  value={vehicle}
                  onChange={(event) => updateComparisonQuery(index, event.target.value)}
                  placeholder={index === 0 ? "输入主车型" : "输入竞品车型"}
                />
              </div>
            ))}
          </div>
          <div className="meta-row">
            <button
              className="button secondary"
              type="button"
              disabled={comparisonQueries.length >= 5}
              onClick={() => setComparisonQueries((current) => [...current, ""])}
            >
              添加车型
            </button>
            <button
              className="button secondary"
              type="button"
              disabled={comparisonQueries.length <= 2}
              onClick={() => setComparisonQueries((current) => current.slice(0, -1))}
            >
              移除末位
            </button>
          </div>
          <div className="split-grid">
            <div className="field">
              <label htmlFor="comparison-start">开始日期</label>
              <input id="comparison-start" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="comparison-end">结束日期</label>
              <input id="comparison-end" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </div>
          </div>
          <p className="field-hint">每个车型仍需确认汽车之家和懂车帝编号；新任务会优先对照长期语料库并采集新增评论。</p>
          {error ? <p className="error">{error}</p> : null}
          <div className="actions">
            <button className="button" type="submit" disabled={loading || comparisonQueries.filter((item) => item.trim()).length < 2}>
              {loading ? "正在查询对比选项" : "进入多车型确认"}
            </button>
          </div>
        </form>
        )}
      </SignalPanel>

      <aside className="stack">
        <div className="card">
          <h3>本次任务会执行什么</h3>
          <div className="timeline">
            <div className="timeline-item">
              <span className="timeline-dot" />
              <p>搜索并解析汽车之家车系 ID。</p>
            </div>
            <div className="timeline-item">
              <span className="timeline-dot" />
              <p>搜索并解析懂车帝车系 ID。</p>
            </div>
            <div className="timeline-item">
              <span className="timeline-dot" />
              <p>确认后由两个 agent 分别采集口碑数据。</p>
            </div>
            <div className="timeline-item">
              <span className="timeline-dot" />
              <p>生成 Excel、词云、智能一页纸和问答上下文。</p>
            </div>
          </div>
        </div>

        <div className="card">
          <h3>当前会话</h3>
          <p className="status-copy">确认后的车系编号会保存到服务器，后续同车型可优先复用；评论数据会进入长期语料库并按增量方式更新。</p>
          <div className="meta-row" style={{ marginTop: 14 }}>
            <StatusPill>后端识别</StatusPill>
            <StatusPill tone="success">会话状态</StatusPill>
            <StatusPill tone="accent">支持手动兜底</StatusPill>
          </div>
          <p className="field-hint" style={{ marginTop: 14 }}>
            上一次查询：{flowState.vehicleQuery || "暂无"}
          </p>
        </div>
      </aside>
    </main>
  );
}
