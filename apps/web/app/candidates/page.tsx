"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError, toJsonBody } from "@/lib/api";
import type {
  ComparisonCreateResponse,
  ComparisonVehicleInput,
  ComparisonVehicleOption,
  CreateJobResponse,
  PlatformCandidate,
  SelectedCandidates,
} from "@/lib/api-types";
import { getFlowState, setFlowState } from "@/lib/flow-state";

function hasCandidate(candidate: PlatformCandidate | null | undefined): candidate is PlatformCandidate {
  return Boolean(candidate?.series_id && candidate.title && candidate.source);
}

function uniqueCandidates(candidates: PlatformCandidate[]) {
  const seen = new Set<string>();
  return candidates.filter((candidate) => {
    const key = `${candidate.series_id ?? ""}|${candidate.url ?? ""}|${candidate.title ?? ""}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function platformOptions(best: PlatformCandidate | null, candidates: PlatformCandidate[]) {
  return uniqueCandidates([best, ...candidates].filter(Boolean) as PlatformCandidate[]).filter(hasCandidate);
}

function kindLabel(kind: string | null | undefined) {
  if (kind === "manual") {
    return "手动";
  }
  if (kind === "confirmed") {
    return "已确认";
  }
  if (kind === "best") {
    return "最佳匹配";
  }
  return "候选";
}

function manualCandidate(platform: "autohome" | "dongchedi", query: string, seriesId: string): PlatformCandidate | null {
  const trimmed = seriesId.trim();
  if (!trimmed) {
    return null;
  }
  return {
    series_id: trimmed,
    url: platform === "autohome" ? `https://k.autohome.com.cn/${trimmed}/` : `https://www.dongchedi.com/auto/series/${trimmed}`,
    title: `${query}（手动输入）`,
    source: "手动输入",
    kind: "manual",
    note: "由用户手动填写车系编号",
  };
}

type ComparisonDraft = {
  autohome: PlatformCandidate | null;
  dongchedi: PlatformCandidate | null;
  manualAutohomeId: string;
  manualDongchediId: string;
  reuseJobId: string | null;
};

function draftFromOption(option: ComparisonVehicleOption): ComparisonDraft {
  return {
    autohome: platformOptions(option.resolve.autohome.best, option.resolve.autohome.candidates)[0] ?? null,
    dongchedi: platformOptions(option.resolve.dongchedi.best, option.resolve.dongchedi.candidates)[0] ?? null,
    manualAutohomeId: "",
    manualDongchediId: "",
    reuseJobId: option.reuse_options[0]?.job_id ?? null,
  };
}

export default function CandidatesPage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [selectedAutohome, setSelectedAutohome] = useState<PlatformCandidate | null>(null);
  const [selectedDongchedi, setSelectedDongchedi] = useState<PlatformCandidate | null>(null);
  const [manualAutohomeId, setManualAutohomeId] = useState("");
  const [manualDongchediId, setManualDongchediId] = useState("");
  const [comparisonDrafts, setComparisonDrafts] = useState<ComparisonDraft[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setReady(true);
    const state = getFlowState();
    const resolve = state.vehicleResolve;
    if (resolve) {
      const autohomeOptions = platformOptions(resolve.autohome.best, resolve.autohome.candidates);
      const dongchediOptions = platformOptions(resolve.dongchedi.best, resolve.dongchedi.candidates);
      setSelectedAutohome(autohomeOptions[0] ?? null);
      setSelectedDongchedi(dongchediOptions[0] ?? null);
    }
    if (state.comparisonOptions) {
      setComparisonDrafts(state.comparisonOptions.vehicles.map(draftFromOption));
    }
  }, []);

  if (!ready) {
    return <main className="panel guard">正在加载...</main>;
  }

  const flowState = getFlowState();
  const resolve = flowState.vehicleResolve;

  function updateComparisonDraft(index: number, patch: Partial<ComparisonDraft>) {
    setComparisonDrafts((current) => current.map((draft, itemIndex) => (itemIndex === index ? { ...draft, ...patch } : draft)));
  }

  async function handleCreateComparison() {
    const options = flowState.comparisonOptions?.vehicles ?? [];
    const vehicles: ComparisonVehicleInput[] = options.map((option, index) => {
      const draft = comparisonDrafts[index] ?? draftFromOption(option);
      const autohome = draft.autohome ?? manualCandidate("autohome", option.query, draft.manualAutohomeId);
      const dongchedi = draft.dongchedi ?? manualCandidate("dongchedi", option.query, draft.manualDongchediId);
      return {
        query: option.query,
        model_name: option.query,
        selected_candidates: {
          autohome: autohome as PlatformCandidate,
          dongchedi: dongchedi as PlatformCandidate,
        },
        reuse_job_id: draft.reuseJobId,
      };
    });

    if (vehicles.some((vehicle) => !hasCandidate(vehicle.selected_candidates.autohome) || !hasCandidate(vehicle.selected_candidates.dongchedi))) {
      setError("每个车型都需要确认汽车之家和懂车帝编号，或手动填写两个平台编号。");
      return;
    }

    let dateRange: { startDate?: string; endDate?: string } = {};
    try {
      dateRange = JSON.parse(window.sessionStorage.getItem("koubei-comparison-date-range") || "{}") as { startDate?: string; endDate?: string };
    } catch {
      dateRange = {};
    }

    setLoading(true);
    setError("");
    try {
      const payload = await apiRequest<ComparisonCreateResponse>("/api/comparisons", {
        method: "POST",
        body: toJsonBody({
          vehicles,
          ...(dateRange.startDate ? { start_date: dateRange.startDate } : {}),
          ...(dateRange.endDate ? { end_date: dateRange.endDate } : {}),
        }),
      });
      setFlowState({
        mode: "comparison",
        comparisonId: payload.comparison_id,
        comparisonVehicles: vehicles,
        comparisonProgress: null,
        jobId: null,
        jobProgress: null,
      });
      router.push("/progress");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "无法创建竞品对比任务，请检查候选信息后重试。");
    } finally {
      setLoading(false);
    }
  }

  if (flowState.mode === "comparison") {
    const options = flowState.comparisonOptions?.vehicles ?? [];
    if (options.length < 2) {
      return (
        <main className="panel guard">
          <p className="eyebrow">第 3 步 / 共 5 步</p>
          <h2>需要先查询竞品车型</h2>
          <p className="helper">请先输入 2-5 个车型，系统拿到候选结果后才能进入确认步骤。</p>
          <div className="actions">
            <Link className="button" href="/vehicle">
              返回车型输入
            </Link>
            <Link className="button secondary" href="/tasks/new">
              前往新建任务
            </Link>
          </div>
        </main>
      );
    }

    return (
      <main className="stack-lg">
        <SignalPanel tone="accent" className="stack-lg">
          <SectionHeader
            eyebrow="LEGACY FLOW"
            title="确认竞品车型"
            copy="这是旧流程兼容页面。新查询建议从任务中心创建。"
          />
          <div className="actions">
            <Link className="button secondary" href="/tasks/new">
              前往新建任务
            </Link>
          </div>
          {options.map((option, index) => {
            const draft = comparisonDrafts[index] ?? draftFromOption(option);
            const autohomeOptions = platformOptions(option.resolve.autohome.best, option.resolve.autohome.candidates);
            const dongchediOptions = platformOptions(option.resolve.dongchedi.best, option.resolve.dongchedi.candidates);
            const effectiveAutohome = draft.autohome ?? manualCandidate("autohome", option.query, draft.manualAutohomeId);
            const effectiveDongchedi = draft.dongchedi ?? manualCandidate("dongchedi", option.query, draft.manualDongchediId);

            return (
              <div className="card stack" key={`${option.query}-${index}`}>
                <div className="platform-head">
                  <div>
                    <p className="eyebrow">车型 {index + 1}</p>
                    <h3 className="platform-title">{option.query}</h3>
                  </div>
                  <StatusPill tone={draft.reuseJobId ? "success" : "accent"}>{draft.reuseJobId ? "复用历史结果" : "增量采集"}</StatusPill>
                </div>

                {option.reuse_options.length ? (
                  <div className="field">
                    <label htmlFor={`reuse-${index}`}>历史结果</label>
                    <select
                      id={`reuse-${index}`}
                      value={draft.reuseJobId ?? ""}
                      onChange={(event) => updateComparisonDraft(index, { reuseJobId: event.target.value || null })}
                    >
                      <option value="">执行增量采集</option>
                      {option.reuse_options.map((item) => (
                        <option key={item.job_id} value={item.job_id}>
                          {item.model_name} · {item.job_id}
                        </option>
                      ))}
                    </select>
                  </div>
                ) : null}

                <div className="split-grid">
                  <div className="field">
                    <label>汽车之家</label>
                    <select
                      value={draft.autohome?.series_id ?? ""}
                      onChange={(event) => {
                        const candidate = autohomeOptions.find((item) => item.series_id === event.target.value) ?? null;
                        updateComparisonDraft(index, { autohome: candidate, manualAutohomeId: "" });
                      }}
                    >
                      <option value="">手动输入</option>
                      {autohomeOptions.map((candidate) => (
                        <option key={`ah-${candidate.series_id}-${candidate.url}`} value={candidate.series_id ?? ""}>
                          {candidate.title} · {candidate.series_id}
                        </option>
                      ))}
                    </select>
                    {!draft.autohome ? (
                      <input
                        value={draft.manualAutohomeId}
                        onChange={(event) => updateComparisonDraft(index, { manualAutohomeId: event.target.value })}
                        placeholder="汽车之家车系编号"
                      />
                    ) : null}
                    <p className="field-hint">{effectiveAutohome?.series_id ? `已确认 ${effectiveAutohome.series_id}` : "待确认"}</p>
                  </div>
                  <div className="field">
                    <label>懂车帝</label>
                    <select
                      value={draft.dongchedi?.series_id ?? ""}
                      onChange={(event) => {
                        const candidate = dongchediOptions.find((item) => item.series_id === event.target.value) ?? null;
                        updateComparisonDraft(index, { dongchedi: candidate, manualDongchediId: "" });
                      }}
                    >
                      <option value="">手动输入</option>
                      {dongchediOptions.map((candidate) => (
                        <option key={`dcd-${candidate.series_id}-${candidate.url}`} value={candidate.series_id ?? ""}>
                          {candidate.title} · {candidate.series_id}
                        </option>
                      ))}
                    </select>
                    {!draft.dongchedi ? (
                      <input
                        value={draft.manualDongchediId}
                        onChange={(event) => updateComparisonDraft(index, { manualDongchediId: event.target.value })}
                        placeholder="懂车帝车系编号"
                      />
                    ) : null}
                    <p className="field-hint">{effectiveDongchedi?.series_id ? `已确认 ${effectiveDongchedi.series_id}` : "待确认"}</p>
                  </div>
                </div>
              </div>
            );
          })}

          {error ? <p className="error">{error}</p> : null}
          <div className="actions">
            <button className="button" type="button" disabled={loading} onClick={handleCreateComparison}>
              {loading ? "正在创建对比任务" : "启动竞品对比"}
            </button>
            <Link className="button secondary" href="/vehicle">
              返回车型输入
            </Link>
          </div>
        </SignalPanel>
      </main>
    );
  }

  if (!flowState.vehicleQuery || !resolve) {
    return (
      <main className="panel guard">
        <p className="eyebrow">第 3 步 / 共 5 步</p>
        <h2>需要先识别车型</h2>
        <p className="helper">请先输入车型名称，系统拿到候选结果后才能进入确认步骤。</p>
        <div className="actions">
          <Link className="button" href="/vehicle">
            返回车型输入
          </Link>
          <Link className="button secondary" href="/tasks/new">
            前往新建任务
          </Link>
        </div>
      </main>
    );
  }

  const resolved = resolve;
  const autohomeOptions = platformOptions(resolved.autohome.best, resolved.autohome.candidates);
  const dongchediOptions = platformOptions(resolved.dongchedi.best, resolved.dongchedi.candidates);
  const effectiveAutohome = selectedAutohome ?? manualCandidate("autohome", resolved.query, manualAutohomeId);
  const effectiveDongchedi = selectedDongchedi ?? manualCandidate("dongchedi", resolved.query, manualDongchediId);
  const hasAutomaticCandidates = Boolean(autohomeOptions.length && dongchediOptions.length);

  async function handleCreateJob() {
    if (!hasCandidate(effectiveAutohome) || !hasCandidate(effectiveDongchedi)) {
      setError("请先为两个平台都选择候选，或手动填写两个平台的车系编号。");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const payload = await apiRequest<CreateJobResponse>("/api/jobs", {
        method: "POST",
        body: toJsonBody({
          query: resolved.query,
          selected_candidates: {
            autohome: effectiveAutohome,
            dongchedi: effectiveDongchedi,
          } satisfies SelectedCandidates,
        }),
      });

      setFlowState({
        mode: "single",
        selectedCandidates: {
          autohome: effectiveAutohome,
          dongchedi: effectiveDongchedi,
        },
        jobId: payload.job_id,
        jobProgress: null,
        comparisonId: null,
        comparisonOptions: null,
        comparisonVehicles: null,
        comparisonProgress: null,
      });
      router.push("/progress");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("无法创建任务，请检查候选信息后重试。");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page-grid">
      <SignalPanel tone="accent" className="stack-lg">
        <SectionHeader
          eyebrow="LEGACY FLOW"
          title="确认平台车系"
          copy="这是旧流程兼容页面。新查询建议从任务中心创建。"
        />
        <div className="actions">
          <Link className="button secondary" href="/tasks/new">
            前往新建任务
          </Link>
        </div>

        {!hasAutomaticCandidates ? (
          <div className="card manual-fallback">
            <h3>自动识别未返回完整候选</h3>
            <p>当前至少有一个平台没有候选。可以返回重新输入车型名称，也可以在下方高级校正里手动填写两个平台的车系编号。</p>
          </div>
        ) : null}

        <div className="platform-grid">
          <div className="card platform-card">
            <div className="platform-head">
              <div>
                <p className="eyebrow">AUTOHOME</p>
                <h3 className="platform-title">汽车之家</h3>
              </div>
              <StatusPill tone={hasCandidate(effectiveAutohome) ? "success" : "warning"}>
                {hasCandidate(effectiveAutohome) ? "已锁定" : "待选择"}
              </StatusPill>
            </div>
            <div className="candidate-list">
              {autohomeOptions.length ? autohomeOptions.map((candidate) => {
                const selected = candidate.series_id === selectedAutohome?.series_id;
                return (
                  <button
                    key={`autohome-${candidate.series_id}-${candidate.url}`}
                    type="button"
                    className={`card candidate-card ${selected ? "selected" : ""}`}
                    onClick={() => setSelectedAutohome(candidate)}
                  >
                    <h4>{candidate.title}</h4>
                    <p>{candidate.note || candidate.source}</p>
                    <div className="meta-row">
                      <StatusPill>车系编号 {candidate.series_id}</StatusPill>
                      <StatusPill tone={candidate.kind === "best" ? "success" : "default"}>
                        {kindLabel(candidate.kind)}
                      </StatusPill>
                    </div>
                  </button>
                );
              }) : <p className="status-copy">未返回汽车之家候选，请使用高级校正手动输入。</p>}
            </div>
          </div>

          <div className="card platform-card">
            <div className="platform-head">
              <div>
                <p className="eyebrow">DONGCHEDI</p>
                <h3 className="platform-title">懂车帝</h3>
              </div>
              <StatusPill tone={hasCandidate(effectiveDongchedi) ? "success" : "warning"}>
                {hasCandidate(effectiveDongchedi) ? "已锁定" : "待选择"}
              </StatusPill>
            </div>
            <div className="candidate-list">
              {dongchediOptions.length ? dongchediOptions.map((candidate) => {
                const selected = candidate.series_id === selectedDongchedi?.series_id;
                return (
                  <button
                    key={`dongchedi-${candidate.series_id}-${candidate.url}`}
                    type="button"
                    className={`card candidate-card ${selected ? "selected" : ""}`}
                    onClick={() => setSelectedDongchedi(candidate)}
                  >
                    <h4>{candidate.title}</h4>
                    <p>{candidate.note || candidate.source}</p>
                    <div className="meta-row">
                      <StatusPill>车系编号 {candidate.series_id}</StatusPill>
                      <StatusPill tone={candidate.kind === "best" ? "success" : "default"}>
                        {kindLabel(candidate.kind)}
                      </StatusPill>
                    </div>
                  </button>
                );
              }) : <p className="status-copy">未返回懂车帝候选，请使用高级校正手动输入。</p>}
            </div>
          </div>
        </div>

        <details className="card manual-fallback">
          <summary>高级校正：手动填写车系编号</summary>
          <p className="status-copy" style={{ marginTop: 10 }}>
            自动识别失败或候选不准时，填写两个平台的车系编号也可以继续创建任务。
          </p>
          <div className="split-grid" style={{ marginTop: 16 }}>
            <div className="field">
              <label htmlFor="manual-autohome">汽车之家车系编号</label>
              <input
                id="manual-autohome"
                value={manualAutohomeId}
                onChange={(event) => {
                  setManualAutohomeId(event.target.value);
                  setSelectedAutohome(null);
                }}
                placeholder="例如：8089"
              />
            </div>
            <div className="field">
              <label htmlFor="manual-dongchedi">懂车帝车系编号</label>
              <input
                id="manual-dongchedi"
                value={manualDongchediId}
                onChange={(event) => {
                  setManualDongchediId(event.target.value);
                  setSelectedDongchedi(null);
                }}
                placeholder="例如：25398"
              />
            </div>
          </div>
        </details>

        {error ? <p className="error">{error}</p> : null}

        <div className="actions">
          <button
            className="button"
            type="button"
            disabled={loading || !hasCandidate(effectiveAutohome) || !hasCandidate(effectiveDongchedi)}
            onClick={handleCreateJob}
          >
            {loading ? "正在创建采集任务" : "启动双源采集"}
          </button>
          <Link className="button secondary" href="/vehicle">
            返回车型输入
          </Link>
        </div>
      </SignalPanel>

      <aside className="stack">
        <div className="card">
          <h3>已识别车型</h3>
          <p className="status-copy">{resolved.query}</p>
          <div className="timeline" style={{ marginTop: 16 }}>
            <div className="timeline-item">
              <span className="timeline-dot" />
              <div>
                <strong>汽车之家</strong>
                <p>{effectiveAutohome?.title || "未选择候选"}</p>
              </div>
            </div>
            <div className="timeline-item">
              <span className="timeline-dot" />
              <div>
                <strong>懂车帝</strong>
                <p>{effectiveDongchedi?.title || "未选择候选"}</p>
              </div>
            </div>
          </div>
        </div>

        <div className="card">
          <h3>创建任务前检查</h3>
          <p className="status-copy">只有两个平台都锁定后才会创建 job，避免一个平台空跑或采集错车系。</p>
          <div className="meta-row" style={{ marginTop: 14 }}>
            <StatusPill tone={hasCandidate(effectiveAutohome) ? "success" : "warning"}>汽车之家</StatusPill>
            <StatusPill tone={hasCandidate(effectiveDongchedi) ? "success" : "warning"}>懂车帝</StatusPill>
          </div>
        </div>
      </aside>
    </main>
  );
}
