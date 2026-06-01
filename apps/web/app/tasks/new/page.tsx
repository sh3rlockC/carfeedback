"use client";

import { BadgeCheck, CarFront, GitCompareArrows, Plus, RefreshCw, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import type { FormEvent } from "react";
import { useMemo, useState } from "react";
import { AccessGatePanel } from "@/app/components/access-gate";
import { useAccessSession } from "@/app/components/access-session";
import { SectionHeader, SignalPanel, StatusPill } from "@/app/components/ui";
import { apiRequest, ApiError, toJsonBody } from "@/lib/api";
import type {
  PlatformCandidate,
  PlatformCandidateGroup,
  PlatformName,
  SelectedCandidates,
  SeriesValidationResponse,
  TaskCollectionMode,
  TaskCreateRequest,
  TaskCreateResponse,
  VehicleResolveResponse,
} from "@/lib/api-types";

type CreateMode = "single" | "comparison";
type PlatformDraft = {
  selectedSeriesId: string;
  manualId: string;
  acceptedOverride: boolean;
};
type ComparisonConfirmation = {
  query: string;
  resolve: VehicleResolveResponse;
  drafts: Record<PlatformName, PlatformDraft>;
};

const comparisonSlots = [0, 1, 2, 3, 4];
const platforms: PlatformName[] = ["autohome", "dongchedi"];
const platformLabels: Record<PlatformName, string> = {
  autohome: "汽车之家",
  dongchedi: "懂车帝",
};

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

function hasCandidate(candidate: PlatformCandidate | null | undefined): candidate is PlatformCandidate {
  return Boolean(candidate?.series_id);
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

function platformOptions(group: PlatformCandidateGroup) {
  return uniqueCandidates([group.best, ...group.candidates].filter(hasCandidate)).slice(0, 3);
}

function emptyResolve(query: string): VehicleResolveResponse {
  return {
    query,
    autohome: { best: null, candidates: [] },
    dongchedi: { best: null, candidates: [] },
  };
}

function validationKey(platform: PlatformName, seriesId: string) {
  return `${platform}:${seriesId}`;
}

function comparisonValidationKey(index: number, platform: PlatformName, seriesId: string) {
  return `comparison:${index}:${platform}:${seriesId}`;
}

function candidateUrl(platform: PlatformName, seriesId: string) {
  return platform === "autohome" ? `https://k.autohome.com.cn/${seriesId}/` : `https://www.dongchedi.com/auto/series/${seriesId}`;
}

function manualCandidate(platform: PlatformName, query: string, seriesId: string): PlatformCandidate {
  return {
    series_id: seriesId,
    url: candidateUrl(platform, seriesId),
    title: `${query}（手动输入）`,
    source: "手动输入",
    kind: "manual",
    note: "由用户手动填写车系编号",
  };
}

function emptyCandidate(platform: PlatformName, query: string): PlatformCandidate {
  return {
    series_id: null,
    url: null,
    title: `${query}（未启用 ${platformLabels[platform]}）`,
    source: "disabled",
  };
}

function initialDraft(group: PlatformCandidateGroup): PlatformDraft {
  const first = platformOptions(group)[0];
  return {
    selectedSeriesId: first?.series_id ?? "",
    manualId: "",
    acceptedOverride: false,
  };
}

function initialDrafts(resolve: VehicleResolveResponse): Record<PlatformName, PlatformDraft> {
  return {
    autohome: initialDraft(resolve.autohome),
    dongchedi: initialDraft(resolve.dongchedi),
  };
}

function validationTone(validation: SeriesValidationResponse | undefined): "default" | "success" | "warning" | "danger" | "accent" {
  if (!validation) {
    return "warning";
  }
  if (validation.status === "matched") {
    return "success";
  }
  if (validation.status === "unverified") {
    return "warning";
  }
  return "danger";
}

function validationLabel(validation: SeriesValidationResponse | undefined) {
  if (!validation) {
    return "待验证";
  }
  if (validation.status === "matched") {
    return "已验证";
  }
  if (validation.status === "unverified") {
    return "未验证";
  }
  if (validation.status === "mismatch") {
    return "不匹配";
  }
  return "无效";
}

export default function NewTaskPage() {
  const access = useAccessSession();
  const router = useRouter();
  const [mode, setMode] = useState<CreateMode>("single");
  const [collectionMode, setCollectionMode] = useState<TaskCollectionMode>("incremental");
  const [singleQuery, setSingleQuery] = useState("");
  const [singleResolve, setSingleResolve] = useState<VehicleResolveResponse | null>(null);
  const [singleDrafts, setSingleDrafts] = useState<Record<PlatformName, PlatformDraft>>({
    autohome: initialDraft({ best: null, candidates: [] }),
    dongchedi: initialDraft({ best: null, candidates: [] }),
  });
  const [enabledPlatforms, setEnabledPlatforms] = useState<Record<PlatformName, boolean>>({
    autohome: true,
    dongchedi: true,
  });
  const [validations, setValidations] = useState<Record<string, SeriesValidationResponse>>({});
  const [comparisonQueries, setComparisonQueries] = useState(["", ""]);
  const [comparisonConfirmations, setComparisonConfirmations] = useState<ComparisonConfirmation[]>([]);
  const [comparisonValidations, setComparisonValidations] = useState<Record<string, SeriesValidationResponse>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const canUseWorkbench = !access.accessControlEnabled || access.accessState === "authorized";

  const comparisonVehicles = useMemo(() => comparisonQueries.map((query) => query.trim()).filter(Boolean), [comparisonQueries]);
  const comparisonReady = useMemo(
    () =>
      comparisonConfirmations.length === comparisonVehicles.length &&
      comparisonConfirmations.every((item, index) => item.query === comparisonVehicles[index]),
    [comparisonConfirmations, comparisonVehicles]
  );
  const canSubmit = mode === "single" ? Boolean(singleQuery.trim()) : comparisonVehicles.length >= 2 && comparisonVehicles.length <= 5;

  const resetComparisonConfirmation = () => {
    setComparisonConfirmations([]);
    setComparisonValidations({});
  };

  const updateComparisonQuery = (index: number, value: string) => {
    setComparisonQueries((current) => {
      const next = [...current];
      next[index] = value;
      return next;
    });
    resetComparisonConfirmation();
  };

  const addComparisonSlot = () => {
    setComparisonQueries((current) => (current.length >= 5 ? current : [...current, ""]));
    resetComparisonConfirmation();
  };

  const removeComparisonSlot = (index: number) => {
    setComparisonQueries((current) => current.filter((_, itemIndex) => itemIndex !== index));
    resetComparisonConfirmation();
  };

  const resetSingleConfirmation = () => {
    setSingleResolve(null);
    setValidations({});
    setSingleDrafts({
      autohome: initialDraft({ best: null, candidates: [] }),
      dongchedi: initialDraft({ best: null, candidates: [] }),
    });
    setEnabledPlatforms({ autohome: true, dongchedi: true });
  };

  const validateSeries = async (query: string, platform: PlatformName, candidate: PlatformCandidate): Promise<SeriesValidationResponse> => {
    try {
      return await apiRequest<SeriesValidationResponse>("/api/vehicles/validate-series", {
        method: "POST",
        body: toJsonBody({
          query,
          platform,
          series_id: candidate.series_id ?? "",
          url: candidate.url,
        }),
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        access.reset();
        throw err;
      }
      return {
        query,
        platform,
        series_id: candidate.series_id ?? "",
        url: candidate.url ?? candidateUrl(platform, candidate.series_id ?? ""),
        status: "unverified",
        can_create: true,
        cacheable: false,
        requires_confirmation: true,
        message: "页面暂时无法验证，请人工确认后继续",
      };
    }
  };

  const prepareSingleConfirmation = async () => {
    const query = singleQuery.trim();
    let resolved = emptyResolve(query);
    try {
      resolved = await apiRequest<VehicleResolveResponse>("/api/vehicles/resolve", {
        method: "POST",
        body: toJsonBody({ query }),
      });
      setError("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        access.reset();
        throw err;
      }
      setError(err instanceof ApiError ? `自动搜索失败：${err.message}` : "自动搜索失败，请手动填写车系编号。");
    }

    setSingleResolve(resolved);
    setSingleDrafts({
      autohome: initialDraft(resolved.autohome),
      dongchedi: initialDraft(resolved.dongchedi),
    });
    setEnabledPlatforms({ autohome: true, dongchedi: true });

    const checks = platforms.flatMap((platform) => platformOptions(resolved[platform]).map((candidate) => ({ platform, candidate })));
    const checked = await Promise.all(checks.map((item) => validateSeries(resolved.query, item.platform, item.candidate)));
    setValidations(Object.fromEntries(checked.map((validation) => [validationKey(validation.platform, validation.series_id), validation])));
  };

  const updateDraft = (platform: PlatformName, patch: Partial<PlatformDraft>) => {
    setSingleDrafts((current) => ({ ...current, [platform]: { ...current[platform], ...patch } }));
  };

  const currentCandidate = (platform: PlatformName): { candidate: PlatformCandidate | null; source: "auto" | "manual" } => {
    const resolve = singleResolve ?? emptyResolve(singleQuery.trim());
    const draft = singleDrafts[platform];
    const manualId = draft.manualId.trim();
    if (manualId) {
      return { candidate: manualCandidate(platform, resolve.query, manualId), source: "manual" };
    }
    const candidate = platformOptions(resolve[platform]).find((item) => item.series_id === draft.selectedSeriesId) ?? null;
    return { candidate, source: "auto" };
  };

  const comparisonCandidate = (
    confirmation: ComparisonConfirmation,
    platform: PlatformName
  ): { candidate: PlatformCandidate | null; source: "auto" | "manual" } => {
    const draft = confirmation.drafts[platform];
    const manualId = draft.manualId.trim();
    if (manualId) {
      return { candidate: manualCandidate(platform, confirmation.resolve.query, manualId), source: "manual" };
    }
    const candidate = platformOptions(confirmation.resolve[platform]).find((item) => item.series_id === draft.selectedSeriesId) ?? null;
    return { candidate, source: "auto" };
  };

  const updateComparisonDraft = (index: number, platform: PlatformName, patch: Partial<PlatformDraft>) => {
    setComparisonConfirmations((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, drafts: { ...item.drafts, [platform]: { ...item.drafts[platform], ...patch } } } : item
      )
    );
  };

  const prepareComparisonConfirmation = async () => {
    let hadResolveError = false;
    const resolved = await Promise.all(
      comparisonVehicles.map(async (query) => {
        try {
          return await apiRequest<VehicleResolveResponse>("/api/vehicles/resolve", {
            method: "POST",
            body: toJsonBody({ query }),
          });
        } catch (err) {
          if (err instanceof ApiError && err.status === 401) {
            access.reset();
            throw err;
          }
          hadResolveError = true;
          return emptyResolve(query);
        }
      })
    );
    const confirmations = resolved.map((resolve) => ({
      query: resolve.query,
      resolve,
      drafts: initialDrafts(resolve),
    }));
    const checks = confirmations.flatMap((confirmation, index) =>
      platforms.flatMap((platform) =>
        platformOptions(confirmation.resolve[platform]).map((candidate) => ({
          index,
          platform,
          candidate,
          query: confirmation.resolve.query,
        }))
      )
    );
    const checked = await Promise.all(checks.map((item) => validateSeries(item.query, item.platform, item.candidate)));
    setComparisonConfirmations(confirmations);
    setComparisonValidations(
      Object.fromEntries(
        checked.map((validation, itemIndex) => {
          const source = checks[itemIndex];
          return [comparisonValidationKey(source.index, validation.platform, validation.series_id), validation];
        })
      )
    );
    setError(hadResolveError ? "部分车型自动搜索失败，请手动填写车系编号后继续。" : "");
  };

  const createSingleTask = async () => {
    const resolve = singleResolve ?? emptyResolve(singleQuery.trim());
    const enabled = platforms.filter((platform) => enabledPlatforms[platform]);
    if (!enabled.length) {
      setError("至少选择一个平台。");
      return;
    }

    const nextValidations = { ...validations };
    const selected: Partial<Record<PlatformName, PlatformCandidate>> = {};
    const cacheConfirmedPlatforms: PlatformName[] = [];

    for (const platform of enabled) {
      const { candidate, source } = currentCandidate(platform);
      if (!candidate?.series_id) {
        setError(`请填写或选择${platformLabels[platform]}车系编号。`);
        return;
      }
      const key = validationKey(platform, candidate.series_id);
      let validation = nextValidations[key];
      if (!validation || source === "manual") {
        validation = await validateSeries(resolve.query, platform, candidate);
        nextValidations[key] = validation;
      }
      if (validation.status === "invalid") {
        setValidations(nextValidations);
        setError(`${platformLabels[platform]}车系编号无效：${validation.message}`);
        return;
      }
      if (source === "auto" && validation.status === "mismatch") {
        setValidations(nextValidations);
        setError(`${platformLabels[platform]}自动候选未匹配车型名，请手动填写正确编号。`);
        return;
      }
      if (validation.requires_confirmation && !singleDrafts[platform].acceptedOverride) {
        setValidations(nextValidations);
        setError(`请确认仍使用${platformLabels[platform]}编号 ${candidate.series_id}。`);
        return;
      }
      if (validation.cacheable) {
        cacheConfirmedPlatforms.push(platform);
      }
      selected[platform] = candidate;
    }

    setValidations(nextValidations);
    const payload: TaskCreateRequest = {
      task_type: "single",
      collection_mode: collectionMode,
      vehicles: [
        {
          query: resolve.query,
          model_name: resolve.query,
          selected_candidates: {
            autohome: selected.autohome ?? emptyCandidate("autohome", resolve.query),
            dongchedi: selected.dongchedi ?? emptyCandidate("dongchedi", resolve.query),
          } satisfies SelectedCandidates,
          enabled_platforms: enabled,
          cache_confirmed_platforms: cacheConfirmedPlatforms,
        },
      ],
    };

    const response = await apiRequest<TaskCreateResponse>("/api/tasks", {
      method: "POST",
      body: toJsonBody(payload),
    });
    router.push(normalizeManageUrl(response.manage_url));
  };

  const createComparisonTask = async () => {
    const nextValidations = { ...comparisonValidations };
    const vehicles = [];

    for (const [index, confirmation] of comparisonConfirmations.entries()) {
      const selected: Partial<Record<PlatformName, PlatformCandidate>> = {};
      const cacheConfirmedPlatforms: PlatformName[] = [];
      for (const platform of platforms) {
        const { candidate, source } = comparisonCandidate(confirmation, platform);
        if (!candidate?.series_id) {
          setError(`车型 ${index + 1} 请填写或选择${platformLabels[platform]}车系编号。`);
          return;
        }
        const key = comparisonValidationKey(index, platform, candidate.series_id);
        let validation = nextValidations[key];
        if (!validation || source === "manual") {
          validation = await validateSeries(confirmation.resolve.query, platform, candidate);
          nextValidations[key] = validation;
        }
        if (validation.status === "invalid") {
          setComparisonValidations(nextValidations);
          setError(`车型 ${index + 1} ${platformLabels[platform]}车系编号无效：${validation.message}`);
          return;
        }
        if (source === "auto" && validation.status === "mismatch") {
          setComparisonValidations(nextValidations);
          setError(`车型 ${index + 1} ${platformLabels[platform]}自动候选未匹配车型名，请手动填写正确编号。`);
          return;
        }
        if (validation.requires_confirmation && !confirmation.drafts[platform].acceptedOverride) {
          setComparisonValidations(nextValidations);
          setError(`请确认车型 ${index + 1} 仍使用${platformLabels[platform]}编号 ${candidate.series_id}。`);
          return;
        }
        if (validation.cacheable) {
          cacheConfirmedPlatforms.push(platform);
        }
        selected[platform] = candidate;
      }
      vehicles.push({
        query: confirmation.resolve.query,
        model_name: confirmation.resolve.query,
        selected_candidates: {
          autohome: selected.autohome ?? emptyCandidate("autohome", confirmation.resolve.query),
          dongchedi: selected.dongchedi ?? emptyCandidate("dongchedi", confirmation.resolve.query),
        } satisfies SelectedCandidates,
        enabled_platforms: platforms,
        cache_confirmed_platforms: cacheConfirmedPlatforms,
      });
    }

    setComparisonValidations(nextValidations);
    const response = await apiRequest<TaskCreateResponse>("/api/tasks", {
      method: "POST",
      body: toJsonBody({ task_type: "comparison", collection_mode: collectionMode, vehicles } satisfies TaskCreateRequest),
    });
    router.push(normalizeManageUrl(response.manage_url));
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canSubmit) {
      setError(mode === "single" ? "请输入车型。" : "对比任务需要 2 到 5 个车型。");
      return;
    }

    setSubmitting(true);
    setError("");
    try {
      if (mode === "single") {
        if (!singleResolve || singleResolve.query !== singleQuery.trim()) {
          await prepareSingleConfirmation();
          return;
        }
        await createSingleTask();
        return;
      }

      if (!comparisonReady) {
        await prepareComparisonConfirmation();
        return;
      }
      await createComparisonTask();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        access.reset();
        setError("访问状态已失效，请刷新后重试。");
        return;
      }
      setError(err instanceof ApiError ? err.message : "任务创建失败。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="stack-lg task-workbench">
      <SignalPanel className="stack">
        <div className="page-head">
          <SectionHeader
            eyebrow="CREATE TASK"
            title="创建任务"
            copy="默认从这里发起单车型或多车型对比，不再使用旧车型输入页。"
          />
          <div className="task-tabs" role="tablist" aria-label="任务模式">
            <button className={mode === "single" ? "active" : ""} type="button" onClick={() => setMode("single")}>
              <CarFront size={15} />
              单车型
            </button>
            <button className={mode === "comparison" ? "active" : ""} type="button" onClick={() => setMode("comparison")}>
              <GitCompareArrows size={15} />
              多车型对比
            </button>
          </div>
        </div>
        <div className="meta-row">
          <StatusPill tone={mode === "single" ? "accent" : "default"}>{mode === "single" ? "单车型采集" : "多车型对比"}</StatusPill>
          <StatusPill tone={collectionMode === "full_refresh" ? "warning" : "accent"}>{collectionMode === "full_refresh" ? "全量采集" : "增量采集"}</StatusPill>
          <StatusPill tone={!access.accessControlEnabled || access.accessState === "authorized" ? "success" : access.accessState === "checking" ? "accent" : "warning"}>
            {!access.accessControlEnabled ? "开放访问" : access.accessState === "authorized" ? "已授权" : access.accessState === "checking" ? "校验中" : "待授权"}
          </StatusPill>
          {mode === "comparison" ? <StatusPill tone={comparisonVehicles.length >= 2 ? "success" : "warning"}>{comparisonVehicles.length}/5</StatusPill> : null}
        </div>
      </SignalPanel>

      <AccessGatePanel
        session={access}
        title="创建前授权"
        description="口令校验内嵌在当前工作台里，授权后会继续停留在新建任务页。"
        compact
      />

      <SignalPanel tone="accent">
        <form className="stack-lg" onSubmit={handleSubmit}>
          <fieldset className="task-form-frame" disabled={!canUseWorkbench || submitting}>
            <div className="field">
              <span>采集策略</span>
              <div className="task-tabs" role="radiogroup" aria-label="采集策略">
                <button className={collectionMode === "incremental" ? "active" : ""} type="button" onClick={() => setCollectionMode("incremental")}>
                  <Plus size={15} />
                  增量
                </button>
                <button className={collectionMode === "full_refresh" ? "active" : ""} type="button" onClick={() => setCollectionMode("full_refresh")}>
                  <RefreshCw size={15} />
                  全量
                </button>
              </div>
            </div>
          {mode === "single" ? (
            <div className="stack">
              <label className="field">
                <span>车型</span>
                <input
                  value={singleQuery}
                  onChange={(event) => {
                    setSingleQuery(event.target.value);
                    resetSingleConfirmation();
                  }}
                  placeholder="输入车型名称"
                />
              </label>

              {singleResolve ? (
                <div className="stack">
                  <div className="task-form-head">
                    <strong>确认双平台车系编号</strong>
                    <button className="button secondary" type="button" onClick={() => void prepareSingleConfirmation()} disabled={submitting}>
                      <RefreshCw size={16} />
                      重新搜索
                    </button>
                  </div>

                  <div className="split-grid">
                    {platforms.map((platform) => {
                      const options = platformOptions(singleResolve[platform]);
                      const draft = singleDrafts[platform];
                      const { candidate, source } = currentCandidate(platform);
                      const validation = candidate?.series_id ? validations[validationKey(platform, candidate.series_id)] : undefined;
                      const enabled = enabledPlatforms[platform];
                      const showConfirm = enabled && validation?.requires_confirmation && !(source === "auto" && validation.status === "mismatch");

                      return (
                        <div className="card stack platform-card" key={platform}>
                          <div className="task-panel-head">
                            <div>
                              <p className="eyebrow">{platform.toUpperCase()}</p>
                              <h3>{platformLabels[platform]}</h3>
                            </div>
                            <StatusPill tone={enabled ? validationTone(validation) : "default"}>
                              {enabled ? validationLabel(validation) : "用户跳过"}
                            </StatusPill>
                          </div>

                          <label className="task-toggle-row">
                            <input
                              type="checkbox"
                              checked={enabled}
                              onChange={(event) => setEnabledPlatforms((current) => ({ ...current, [platform]: event.target.checked }))}
                            />
                            <span>采集该平台</span>
                          </label>

                          {enabled ? (
                            <>
                              <label className="field">
                                <span>候选</span>
                                <select
                                  value={draft.manualId ? "" : draft.selectedSeriesId}
                                  onChange={(event) => updateDraft(platform, { selectedSeriesId: event.target.value, manualId: "", acceptedOverride: false })}
                                >
                                  <option value="">手动输入</option>
                                  {options.map((item) => (
                                    <option key={`${platform}-${item.series_id}-${item.url}`} value={item.series_id ?? ""}>
                                      {item.title ?? singleResolve.query} · {item.series_id}
                                    </option>
                                  ))}
                                </select>
                              </label>

                              {!draft.selectedSeriesId || draft.manualId ? (
                                <label className="field">
                                  <span>手动编号</span>
                                  <input
                                    value={draft.manualId}
                                    onChange={(event) => updateDraft(platform, { manualId: event.target.value, acceptedOverride: false })}
                                    placeholder={`${platformLabels[platform]}车系编号`}
                                  />
                                </label>
                              ) : null}

                              <div className="meta-row">
                                <StatusPill tone={validationTone(validation)}>{validationLabel(validation)}</StatusPill>
                                {candidate?.series_id ? <StatusPill>{candidate.series_id}</StatusPill> : null}
                                {source === "manual" ? <StatusPill tone="accent">手动</StatusPill> : null}
                              </div>
                              {validation ? <p className={validation.status === "matched" ? "status-copy" : "error"}>{validation.message}</p> : null}

                              {candidate?.url ? (
                                <details>
                                  <summary>查看链接</summary>
                                  <a className="artifact-path" href={candidate.url} target="_blank" rel="noreferrer">
                                    {candidate.url}
                                  </a>
                                </details>
                              ) : null}

                              {showConfirm ? (
                                <label className="task-toggle-row">
                                  <input
                                    type="checkbox"
                                    checked={draft.acceptedOverride}
                                    onChange={(event) => updateDraft(platform, { acceptedOverride: event.target.checked })}
                                  />
                                  <span>我确认仍使用此编号</span>
                                </label>
                              ) : null}
                            </>
                          ) : (
                            <p className="status-copy">该平台不会创建采集 run，最终结果将按单平台降级展示。</p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>
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
                  <Plus size={16} />
                  添加车型
                </button>
              </div>
              {comparisonConfirmations.length ? (
                <div className="stack">
                  <div className="task-form-head">
                    <strong>确认每辆车的双平台编号</strong>
                    <button className="button secondary" type="button" onClick={() => void prepareComparisonConfirmation()} disabled={submitting}>
                      <RefreshCw size={16} />
                      重新搜索
                    </button>
                  </div>
                  {comparisonConfirmations.map((confirmation, index) => (
                    <div className="stack comparison-vehicle-panel" key={`${confirmation.query}-${index}`}>
                      <div className="task-panel-head">
                        <div>
                          <p className="eyebrow">VEHICLE {index + 1}</p>
                          <h3>{confirmation.query}</h3>
                        </div>
                        <StatusPill tone="accent">对比车型</StatusPill>
                      </div>
                      <div className="split-grid">
                        {platforms.map((platform) => {
                          const options = platformOptions(confirmation.resolve[platform]);
                          const draft = confirmation.drafts[platform];
                          const { candidate, source } = comparisonCandidate(confirmation, platform);
                          const validation = candidate?.series_id ? comparisonValidations[comparisonValidationKey(index, platform, candidate.series_id)] : undefined;
                          const showConfirm = validation?.requires_confirmation && !(source === "auto" && validation.status === "mismatch");

                          return (
                            <div className="card stack platform-card" key={`${confirmation.query}-${platform}`}>
                              <div className="task-panel-head">
                                <div>
                                  <p className="eyebrow">{platform.toUpperCase()}</p>
                                  <h3>{platformLabels[platform]}</h3>
                                </div>
                                <StatusPill tone={validationTone(validation)}>{validationLabel(validation)}</StatusPill>
                              </div>

                              <label className="field">
                                <span>候选</span>
                                <select
                                  value={draft.manualId ? "" : draft.selectedSeriesId}
                                  onChange={(event) => updateComparisonDraft(index, platform, { selectedSeriesId: event.target.value, manualId: "", acceptedOverride: false })}
                                >
                                  <option value="">手动输入</option>
                                  {options.map((item) => (
                                    <option key={`${index}-${platform}-${item.series_id}-${item.url}`} value={item.series_id ?? ""}>
                                      {item.title ?? confirmation.query} · {item.series_id}
                                    </option>
                                  ))}
                                </select>
                              </label>

                              {!draft.selectedSeriesId || draft.manualId ? (
                                <label className="field">
                                  <span>手动编号</span>
                                  <input
                                    value={draft.manualId}
                                    onChange={(event) => updateComparisonDraft(index, platform, { manualId: event.target.value, acceptedOverride: false })}
                                    placeholder={`${platformLabels[platform]}车系编号`}
                                  />
                                </label>
                              ) : null}

                              <div className="meta-row">
                                <StatusPill tone={validationTone(validation)}>{validationLabel(validation)}</StatusPill>
                                {candidate?.series_id ? <StatusPill>{candidate.series_id}</StatusPill> : null}
                                {source === "manual" ? <StatusPill tone="accent">手动</StatusPill> : null}
                              </div>
                              {validation ? <p className={validation.status === "matched" ? "status-copy" : "error"}>{validation.message}</p> : null}

                              {candidate?.url ? (
                                <details>
                                  <summary>查看链接</summary>
                                  <a className="artifact-path" href={candidate.url} target="_blank" rel="noreferrer">
                                    {candidate.url}
                                  </a>
                                </details>
                              ) : null}

                              {showConfirm ? (
                                <label className="task-toggle-row">
                                  <input
                                    type="checkbox"
                                    checked={draft.acceptedOverride}
                                    onChange={(event) => updateComparisonDraft(index, platform, { acceptedOverride: event.target.checked })}
                                  />
                                  <span>我确认仍使用此编号</span>
                                </label>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          )}
          </fieldset>

          {error ? <p className="error">{error}</p> : null}

          {access.accessControlEnabled && access.accessState !== "authorized" ? (
            <p className="status-copy">授权完成后，会继续留在当前页面并允许搜索车型、校验 seriesId、创建任务。</p>
          ) : null}

          <div className="actions">
            <button className="button" type="submit" disabled={!canUseWorkbench || !canSubmit || submitting}>
              {(mode === "single" && !singleResolve) || (mode === "comparison" && !comparisonReady) ? <Search size={16} /> : <BadgeCheck size={16} />}
              {submitting ? "处理中" : (mode === "single" && !singleResolve) || (mode === "comparison" && !comparisonReady) ? "搜索车系" : "创建任务"}
            </button>
          </div>
        </form>
      </SignalPanel>
    </main>
  );
}
