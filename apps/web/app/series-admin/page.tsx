"use client";

import {
  Download,
  FileSpreadsheet,
  History,
  Pencil,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import type { ChangeEvent, FormEvent } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { StatusPill } from "@/app/components/ui";
import { ApiError, apiRequest, toJsonBody } from "@/lib/api";
import type {
  SeriesAlias,
  SeriesAliasListResponse,
  SeriesAuditItem,
  SeriesAuditResponse,
  SeriesImportPreviewResponse,
  SeriesImportStatus,
  SeriesRecord,
  SeriesListResponse,
  SeriesMutationRequest,
  SeriesPlatform,
} from "@/lib/api-types";
import { withBasePath } from "@/lib/paths";

type AdminTab = "seriesId" | "aliases" | "import";
type SeriesForm = {
  query: string;
  platform: SeriesPlatform;
  series_id: string;
  url: string;
  title: string;
  source: string;
  operator: string;
  reason: string;
};
type AliasForm = {
  alias: string;
  canonical_query: string;
};

const emptySeriesForm: SeriesForm = {
  query: "",
  platform: "autohome",
  series_id: "",
  url: "",
  title: "",
  source: "manual",
  operator: "",
  reason: "",
};

const emptyAliasForm: AliasForm = {
  alias: "",
  canonical_query: "",
};

const importStatusTone: Record<SeriesImportStatus, "default" | "success" | "warning" | "danger" | "accent"> = {
  new: "success",
  duplicate: "accent",
  conflict: "danger",
  invalid: "warning",
};
const importStatuses: SeriesImportStatus[] = ["new", "duplicate", "conflict", "invalid"];

function compactDate(value: string | null) {
  if (!value) {
    return "-";
  }
  return value.replace("T", " ").slice(0, 16);
}

function errorCopy(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    return error.status === 409 ? `409 冲突：${error.message}` : error.message;
  }
  return fallback;
}

function trimOptional(value: string) {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function fileKey(file: File | null) {
  return file ? `${file.name}:${file.size}:${file.lastModified}` : "";
}

function seriesPayload(form: SeriesForm): SeriesMutationRequest {
  return {
    query: form.query.trim(),
    platform: form.platform,
    series_id: form.series_id.trim(),
    operator: form.operator.trim(),
    reason: form.reason.trim(),
    url: trimOptional(form.url),
    title: trimOptional(form.title),
    source: trimOptional(form.source),
  };
}

function pathWithQuery(path: string, params: Record<string, string | number | null | undefined>) {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && `${value}` !== "") {
      searchParams.set(key, `${value}`);
    }
  }
  const query = searchParams.toString();
  return query ? `${path}?${query}` : path;
}

async function fetchFormJson<T>(path: string, file: File): Promise<T> {
  const formData = new FormData();
  formData.set("file", file);
  const response = await fetch(withBasePath(path), {
    method: "POST",
    credentials: "include",
    body: formData,
    cache: "no-store",
  });

  if (!response.ok) {
    let message = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        message = payload.detail;
      }
    } catch {
      message = response.statusText;
    }
    throw new ApiError(response.status, message);
  }

  return (await response.json()) as T;
}

function recordToForm(record: SeriesRecord, current: SeriesForm): SeriesForm {
  return {
    query: record.query,
    platform: record.platform,
    series_id: record.series_id,
    url: record.url ?? "",
    title: record.title ?? "",
    source: record.source ?? "",
    operator: current.operator,
    reason: current.reason,
  };
}

function canSubmitSeries(form: SeriesForm) {
  return Boolean(form.query.trim() && form.series_id.trim() && form.operator.trim() && form.reason.trim());
}

function canSubmitAlias(form: AliasForm) {
  return Boolean(form.alias.trim() && form.canonical_query.trim());
}

export default function SeriesAdminPage() {
  const [tab, setTab] = useState<AdminTab>("seriesId");
  const [statusMessage, setStatusMessage] = useState("");
  const [seriesError, setSeriesError] = useState("");
  const [aliasError, setAliasError] = useState("");
  const [importError, setImportError] = useState("");

  const [records, setRecords] = useState<SeriesRecord[]>([]);
  const [seriesTotal, setSeriesTotal] = useState(0);
  const [seriesLoading, setSeriesLoading] = useState(false);
  const [seriesSearch, setSeriesSearch] = useState("");
  const [seriesPlatform, setSeriesPlatform] = useState("");
  const [seriesStatus, setSeriesStatus] = useState("active");
  const [seriesSource, setSeriesSource] = useState("");
  const [selectedRecord, setSelectedRecord] = useState<SeriesRecord | null>(null);
  const [seriesForm, setSeriesForm] = useState<SeriesForm>(emptySeriesForm);
  const [auditItems, setAuditItems] = useState<SeriesAuditItem[]>([]);
  const [auditTotal, setAuditTotal] = useState(0);
  const [auditLoading, setAuditLoading] = useState(false);

  const [aliases, setAliases] = useState<SeriesAlias[]>([]);
  const [aliasTotal, setAliasTotal] = useState(0);
  const [aliasLoading, setAliasLoading] = useState(false);
  const [aliasSearch, setAliasSearch] = useState("");
  const [selectedAlias, setSelectedAlias] = useState<SeriesAlias | null>(null);
  const [aliasForm, setAliasForm] = useState<AliasForm>(emptyAliasForm);

  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<SeriesImportPreviewResponse | null>(null);
  const [importPreviewFileKey, setImportPreviewFileKey] = useState("");
  const [importOperator, setImportOperator] = useState("");
  const [importLoading, setImportLoading] = useState(false);

  const visibleAuditItems = useMemo(() => auditItems.slice().reverse(), [auditItems]);
  const previewRows = importPreview?.rows.slice(0, 40) ?? [];
  const importSummary = importPreview?.summary ?? {};
  const hasCurrentImportPreview = Boolean(importFile && importPreview && importPreviewFileKey === fileKey(importFile));

  const loadSeries = useCallback(async () => {
    setSeriesLoading(true);
    setSeriesError("");
    try {
      const payload = await apiRequest<SeriesListResponse>(
        pathWithQuery("/api/admin/series", {
          search: seriesSearch.trim(),
          platform: seriesPlatform,
          status: seriesStatus,
          source: seriesSource.trim(),
          limit: 100,
          offset: 0,
        }),
      );
      setRecords(payload.items);
      setSeriesTotal(payload.total);
    } catch (error) {
      setSeriesError(errorCopy(error, "车系列表读取失败。"));
    } finally {
      setSeriesLoading(false);
    }
  }, [seriesPlatform, seriesSearch, seriesSource, seriesStatus]);

  const loadAliases = useCallback(async () => {
    setAliasLoading(true);
    setAliasError("");
    try {
      const payload = await apiRequest<SeriesAliasListResponse>(
        pathWithQuery("/api/admin/series/aliases", {
          search: aliasSearch.trim(),
          limit: 100,
          offset: 0,
        }),
      );
      setAliases(payload.items);
      setAliasTotal(payload.total);
    } catch (error) {
      setAliasError(errorCopy(error, "别名列表读取失败。"));
    } finally {
      setAliasLoading(false);
    }
  }, [aliasSearch]);

  const loadAudit = useCallback(async (recordId: number) => {
    setAuditLoading(true);
    try {
      const payload = await apiRequest<SeriesAuditResponse>(
        pathWithQuery("/api/admin/series/audit", { record_id: recordId, limit: 80, offset: 0 }),
      );
      setAuditItems(payload.items);
      setAuditTotal(payload.total);
    } catch (error) {
      setSeriesError(errorCopy(error, "审计记录读取失败。"));
    } finally {
      setAuditLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSeries();
  }, [loadSeries]);

  useEffect(() => {
    void loadAliases();
  }, [loadAliases]);

  useEffect(() => {
    if (selectedRecord) {
      void loadAudit(selectedRecord.id);
    } else {
      setAuditItems([]);
      setAuditTotal(0);
    }
  }, [loadAudit, selectedRecord]);

  useEffect(() => {
    if (!selectedRecord) {
      return;
    }
    const visibleRecord = records.find((record) => record.id === selectedRecord.id);
    if (!visibleRecord) {
      setSelectedRecord(null);
      setSeriesForm((current) => ({ ...emptySeriesForm, operator: current.operator, reason: current.reason }));
      return;
    }
    setSelectedRecord(visibleRecord);
    setSeriesForm((current) => recordToForm(visibleRecord, current));
  }, [records, selectedRecord?.id]);

  useEffect(() => {
    if (!selectedAlias) {
      return;
    }
    const visibleAlias = aliases.find((alias) => alias.id === selectedAlias.id);
    if (!visibleAlias) {
      setSelectedAlias(null);
      setAliasForm(emptyAliasForm);
      return;
    }
    setSelectedAlias(visibleAlias);
    setAliasForm({ alias: visibleAlias.alias, canonical_query: visibleAlias.canonical_query });
  }, [aliases, selectedAlias?.id]);

  function selectRecord(record: SeriesRecord) {
    setSelectedRecord(record);
    setSeriesForm((current) => recordToForm(record, current));
    setStatusMessage(`已选择 ${record.query} / ${record.platform}`);
  }

  function resetSeriesForm() {
    setSelectedRecord(null);
    setSeriesForm((current) => ({ ...emptySeriesForm, operator: current.operator, reason: current.reason }));
    setStatusMessage("已切换为新建模式");
  }

  async function submitSeries(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmitSeries(seriesForm)) {
      setSeriesError("query、series_id、operator、reason 为必填。");
      return;
    }

    setSeriesLoading(true);
    setSeriesError("");
    try {
      const payload = seriesPayload(seriesForm);
      const saved = selectedRecord
        ? await apiRequest<SeriesRecord>(`/api/admin/series/${selectedRecord.id}`, {
            method: "PATCH",
            body: toJsonBody(payload),
          })
        : await apiRequest<SeriesRecord>("/api/admin/series", {
            method: "POST",
            body: toJsonBody(payload),
          });
      setSelectedRecord(saved);
      setSeriesForm((current) => recordToForm(saved, current));
      setStatusMessage(selectedRecord ? "车系记录已更新" : "车系记录已创建");
      await loadSeries();
      await loadAudit(saved.id);
    } catch (error) {
      setSeriesError(errorCopy(error, "车系记录保存失败。"));
    } finally {
      setSeriesLoading(false);
    }
  }

  async function changeRecordStatus(record: SeriesRecord, action: "delete" | "restore") {
    if (selectedRecord?.id !== record.id) {
      setSelectedRecord(record);
      setSeriesForm((current) => ({ ...recordToForm(record, current), reason: "" }));
      setSeriesError("已切换到该行；请确认 operator 并填写 reason 后再次执行软删除/恢复。");
      return;
    }

    const operator = seriesForm.operator.trim();
    const reason = seriesForm.reason.trim();
    if (!operator || !reason) {
      setSeriesError("软删除/恢复需要填写 operator 和 reason。");
      return;
    }

    setSeriesLoading(true);
    setSeriesError("");
    try {
      const saved =
        action === "delete"
          ? await apiRequest<SeriesRecord>(`/api/admin/series/${record.id}`, {
              method: "DELETE",
              body: toJsonBody({ operator, reason }),
            })
          : await apiRequest<SeriesRecord>(`/api/admin/series/${record.id}/restore`, {
              method: "POST",
              body: toJsonBody({ operator, reason }),
            });
      setSelectedRecord(saved);
      setSeriesForm((current) => recordToForm(saved, current));
      setStatusMessage(action === "delete" ? "记录已软删除" : "记录已恢复");
      await loadSeries();
      await loadAudit(saved.id);
    } catch (error) {
      setSeriesError(errorCopy(error, action === "delete" ? "软删除失败。" : "恢复失败。"));
    } finally {
      setSeriesLoading(false);
    }
  }

  async function submitAlias(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmitAlias(aliasForm)) {
      setAliasError("alias 和 canonical_query 为必填。");
      return;
    }

    setAliasLoading(true);
    setAliasError("");
    try {
      const payload = {
        alias: aliasForm.alias.trim(),
        canonical_query: aliasForm.canonical_query.trim(),
      };
      const saved = selectedAlias
        ? await apiRequest<SeriesAlias>(`/api/admin/series/aliases/${selectedAlias.id}`, {
            method: "PATCH",
            body: toJsonBody(payload),
          })
        : await apiRequest<SeriesAlias>("/api/admin/series/aliases", {
            method: "POST",
            body: toJsonBody(payload),
          });
      setSelectedAlias(saved);
      setAliasForm({ alias: saved.alias, canonical_query: saved.canonical_query });
      setStatusMessage(selectedAlias ? "别名已更新" : "别名已创建");
      await loadAliases();
    } catch (error) {
      setAliasError(errorCopy(error, "别名保存失败。"));
    } finally {
      setAliasLoading(false);
    }
  }

  async function deleteAlias(alias: SeriesAlias) {
    setAliasLoading(true);
    setAliasError("");
    try {
      await apiRequest<null>(`/api/admin/series/aliases/${alias.id}`, { method: "DELETE" });
      if (selectedAlias?.id === alias.id) {
        setSelectedAlias(null);
        setAliasForm(emptyAliasForm);
      }
      setStatusMessage("别名已删除");
      await loadAliases();
    } catch (error) {
      setAliasError(errorCopy(error, "别名删除失败。"));
    } finally {
      setAliasLoading(false);
    }
  }

  async function previewImport() {
    if (!importFile) {
      setImportError("请选择 CSV / XLSX 文件。");
      return;
    }
    setImportLoading(true);
    setImportError("");
    try {
      const payload = await fetchFormJson<SeriesImportPreviewResponse>("/api/admin/series/import/preview", importFile);
      setImportPreview(payload);
      setImportPreviewFileKey(fileKey(importFile));
      setStatusMessage("导入预览已生成");
    } catch (error) {
      setImportError(errorCopy(error, "导入预览失败。"));
    } finally {
      setImportLoading(false);
    }
  }

  async function commitImport() {
    if (!importFile) {
      setImportError("请选择 CSV / XLSX 文件。");
      return;
    }
    if (!importOperator.trim()) {
      setImportError("提交导入需要 operator。");
      return;
    }
    if (!hasCurrentImportPreview) {
      setImportError("请先为当前文件生成导入预览，再提交导入。");
      return;
    }
    setImportLoading(true);
    setImportError("");
    try {
      const payload = await fetchFormJson<SeriesImportPreviewResponse>(
        `/api/admin/series/import/commit?operator=${encodeURIComponent(importOperator.trim())}`,
        importFile,
      );
      setImportPreview(payload);
      setImportPreviewFileKey(fileKey(importFile));
      setStatusMessage("导入已提交");
      await loadSeries();
    } catch (error) {
      setImportError(errorCopy(error, "导入提交失败。"));
    } finally {
      setImportLoading(false);
    }
  }

  function updateImportFile(event: ChangeEvent<HTMLInputElement>) {
    setImportFile(event.target.files?.[0] ?? null);
    setImportPreview(null);
    setImportPreviewFileKey("");
    setImportError("");
  }

  return (
    <main className="series-admin stack-lg">
      <section className="flat-panel">
        <div className="page-head">
          <div>
            <p className="eyebrow">SERIES ADMIN</p>
            <h1>
              <FileSpreadsheet size={24} />
              车系 ID 管理
            </h1>
            <p className="helper">维护人工确认的车系 ID、别名映射和批量导入记录。</p>
          </div>
          <a className="button secondary" href={withBasePath("/api/admin/series/export.xlsx")}>
            <Download size={16} />
            导出 Excel
          </a>
        </div>
        <div className="series-admin-toolbar">
          <div className="task-tabs" role="tablist" aria-label="车系管理页签">
            <button className={tab === "seriesId" ? "active" : ""} type="button" onClick={() => setTab("seriesId")}>
              seriesId
            </button>
            <button className={tab === "aliases" ? "active" : ""} type="button" onClick={() => setTab("aliases")}>
              别名
            </button>
            <button className={tab === "import" ? "active" : ""} type="button" onClick={() => setTab("import")}>
              导入预览
            </button>
          </div>
          <div className="series-admin-status">
            <StatusPill tone={statusMessage ? "success" : "accent"}>{statusMessage || "待操作"}</StatusPill>
          </div>
        </div>
      </section>

      {tab === "seriesId" ? (
        <section className="series-admin-grid">
          <div className="stack">
            <section className="flat-panel filter-panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">FILTERS</p>
                  <h2>
                    <Search size={18} />
                    记录筛选
                  </h2>
                </div>
                <button className="icon-text-button" type="button" onClick={() => void loadSeries()} disabled={seriesLoading}>
                  <RefreshCw size={16} />
                  {seriesLoading ? "刷新中" : "刷新"}
                </button>
              </div>
              <div className="series-filter-grid">
                <label className="field">
                  <span>搜索</span>
                  <input value={seriesSearch} onChange={(event) => setSeriesSearch(event.target.value)} placeholder="车型 / series_id / key" />
                </label>
                <label className="field">
                  <span>平台</span>
                  <select value={seriesPlatform} onChange={(event) => setSeriesPlatform(event.target.value)}>
                    <option value="">全部</option>
                    <option value="autohome">autohome</option>
                    <option value="dongchedi">dongchedi</option>
                  </select>
                </label>
                <label className="field">
                  <span>状态</span>
                  <select value={seriesStatus} onChange={(event) => setSeriesStatus(event.target.value)}>
                    <option value="">全部</option>
                    <option value="active">active</option>
                    <option value="deleted">deleted</option>
                  </select>
                </label>
                <label className="field">
                  <span>来源</span>
                  <input value={seriesSource} onChange={(event) => setSeriesSource(event.target.value)} placeholder="manual / import" />
                </label>
              </div>
              {seriesError ? <p className="error admin-inline-error">{seriesError}</p> : null}
            </section>

            <section className="flat-panel">
              <div className="task-table-head">
                <strong>{seriesLoading ? "读取中" : `${records.length}/${seriesTotal} 条车系记录`}</strong>
                <StatusPill tone="accent">hidden route</StatusPill>
              </div>
              <div className="dense-table-wrap">
                <table className="dense-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>车型</th>
                      <th>平台</th>
                      <th>series_id</th>
                      <th>状态</th>
                      <th>来源</th>
                      <th>更新时间</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((record) => (
                      <tr key={record.id} className={selectedRecord?.id === record.id ? "selected-row" : ""}>
                        <td>{record.id}</td>
                        <td>
                          <strong>{record.query}</strong>
                          <span>{record.query_key}</span>
                        </td>
                        <td>{record.platform}</td>
                        <td>{record.series_id}</td>
                        <td>
                          <StatusPill tone={record.status === "active" ? "success" : "warning"}>{record.status}</StatusPill>
                        </td>
                        <td>{record.source ?? "-"}</td>
                        <td>{compactDate(record.updated_at)}</td>
                        <td>
                          <div className="table-action-row">
                            <button className="table-action" type="button" onClick={() => selectRecord(record)} aria-label={`编辑 ${record.query}`}>
                              <Pencil size={14} />
                            </button>
                            {record.status === "deleted" ? (
                              <button className="table-action" type="button" onClick={() => void changeRecordStatus(record, "restore")} aria-label={`恢复 ${record.query}`}>
                                <RotateCcw size={14} />
                              </button>
                            ) : (
                              <button className="table-action table-action-danger" type="button" onClick={() => void changeRecordStatus(record, "delete")} aria-label={`软删除 ${record.query}`}>
                                <Trash2 size={14} />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                    {!records.length ? (
                      <tr>
                        <td colSpan={8}>没有匹配记录。</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>
          </div>

          <aside className="stack">
            <section className="flat-panel admin-editor">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">MUTATION</p>
                  <h2>{selectedRecord ? `编辑 #${selectedRecord.id}` : "新建记录"}</h2>
                </div>
                <button className="icon-text-button" type="button" onClick={resetSeriesForm}>
                  新建
                </button>
              </div>
              <form className="series-form-grid" onSubmit={submitSeries}>
                <label className="field">
                  <span>query</span>
                  <input value={seriesForm.query} onChange={(event) => setSeriesForm((current) => ({ ...current, query: event.target.value }))} />
                </label>
                <label className="field">
                  <span>platform</span>
                  <select value={seriesForm.platform} onChange={(event) => setSeriesForm((current) => ({ ...current, platform: event.target.value as SeriesPlatform }))}>
                    <option value="autohome">autohome</option>
                    <option value="dongchedi">dongchedi</option>
                  </select>
                </label>
                <label className="field">
                  <span>series_id</span>
                  <input value={seriesForm.series_id} onChange={(event) => setSeriesForm((current) => ({ ...current, series_id: event.target.value }))} />
                </label>
                <label className="field">
                  <span>title</span>
                  <input value={seriesForm.title} onChange={(event) => setSeriesForm((current) => ({ ...current, title: event.target.value }))} />
                </label>
                <label className="field series-form-wide">
                  <span>url</span>
                  <input value={seriesForm.url} onChange={(event) => setSeriesForm((current) => ({ ...current, url: event.target.value }))} />
                </label>
                <label className="field">
                  <span>source</span>
                  <input value={seriesForm.source} onChange={(event) => setSeriesForm((current) => ({ ...current, source: event.target.value }))} />
                </label>
                <label className="field">
                  <span>operator</span>
                  <input value={seriesForm.operator} onChange={(event) => setSeriesForm((current) => ({ ...current, operator: event.target.value }))} />
                </label>
                <label className="field series-form-wide">
                  <span>reason</span>
                  <input value={seriesForm.reason} onChange={(event) => setSeriesForm((current) => ({ ...current, reason: event.target.value }))} />
                </label>
                <div className="actions series-form-wide">
                  <button className="button" type="submit" disabled={!canSubmitSeries(seriesForm) || seriesLoading}>
                    <Save size={16} />
                    {selectedRecord ? "保存修改" : "创建记录"}
                  </button>
                </div>
              </form>
            </section>

            <section className="flat-panel audit-panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">AUDIT</p>
                  <h2>
                    <History size={18} />
                    审计记录
                  </h2>
                </div>
                <StatusPill tone="accent">{selectedRecord ? `${auditItems.length}/${auditTotal}` : "未选择"}</StatusPill>
              </div>
              <div className="audit-list">
                {visibleAuditItems.map((item) => (
                  <div className="audit-row" key={item.id}>
                    <strong>{item.action}</strong>
                    <span>{compactDate(item.created_at)}</span>
                    <p>
                      {item.operator} · {item.reason}
                    </p>
                  </div>
                ))}
                {auditLoading ? <p className="empty-copy">读取审计记录中。</p> : null}
                {!auditLoading && !visibleAuditItems.length ? <p className="empty-copy">选择记录后显示审计轨迹。</p> : null}
              </div>
            </section>
          </aside>
        </section>
      ) : null}

      {tab === "aliases" ? (
        <section className="series-admin-grid alias-admin-grid">
          <section className="flat-panel">
            <div className="panel-head">
              <div>
                <p className="eyebrow">ALIASES</p>
                <h2>
                  <Search size={18} />
                  别名列表
                </h2>
              </div>
              <button className="icon-text-button" type="button" onClick={() => void loadAliases()} disabled={aliasLoading}>
                <RefreshCw size={16} />
                {aliasLoading ? "刷新中" : "刷新"}
              </button>
            </div>
            <div className="series-admin-toolbar">
              <label className="field alias-search-field">
                <span>搜索别名</span>
                <input value={aliasSearch} onChange={(event) => setAliasSearch(event.target.value)} placeholder="alias / canonical_query" />
              </label>
              <StatusPill tone="accent">{aliases.length}/{aliasTotal}</StatusPill>
            </div>
            {aliasError ? <p className="error admin-inline-error">{aliasError}</p> : null}
            <div className="dense-table-wrap">
              <table className="dense-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>alias</th>
                    <th>alias_key</th>
                    <th>canonical_query</th>
                    <th>更新时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {aliases.map((alias) => (
                    <tr key={alias.id} className={selectedAlias?.id === alias.id ? "selected-row" : ""}>
                      <td>{alias.id}</td>
                      <td>{alias.alias}</td>
                      <td>{alias.alias_key}</td>
                      <td>{alias.canonical_query}</td>
                      <td>{compactDate(alias.updated_at)}</td>
                      <td>
                        <div className="table-action-row">
                          <button
                            className="table-action"
                            type="button"
                            onClick={() => {
                              setSelectedAlias(alias);
                              setAliasForm({ alias: alias.alias, canonical_query: alias.canonical_query });
                            }}
                            aria-label={`编辑别名 ${alias.alias}`}
                          >
                            <Pencil size={14} />
                          </button>
                          <button className="table-action table-action-danger" type="button" onClick={() => void deleteAlias(alias)} aria-label={`删除别名 ${alias.alias}`}>
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!aliases.length ? (
                    <tr>
                      <td colSpan={6}>没有匹配别名。</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          <aside className="flat-panel admin-editor">
            <div className="panel-head">
              <div>
                <p className="eyebrow">ALIAS FORM</p>
                <h2>{selectedAlias ? `编辑别名 #${selectedAlias.id}` : "新建别名"}</h2>
              </div>
              <button
                className="icon-text-button"
                type="button"
                onClick={() => {
                  setSelectedAlias(null);
                  setAliasForm(emptyAliasForm);
                }}
              >
                新建
              </button>
            </div>
            <form className="stack" onSubmit={submitAlias}>
              <label className="field">
                <span>alias</span>
                <input value={aliasForm.alias} onChange={(event) => setAliasForm((current) => ({ ...current, alias: event.target.value }))} />
              </label>
              <label className="field">
                <span>canonical_query</span>
                <input value={aliasForm.canonical_query} onChange={(event) => setAliasForm((current) => ({ ...current, canonical_query: event.target.value }))} />
              </label>
              <div className="actions">
                <button className="button" type="submit" disabled={!canSubmitAlias(aliasForm) || aliasLoading}>
                  <Save size={16} />
                  {selectedAlias ? "保存别名" : "创建别名"}
                </button>
              </div>
            </form>
          </aside>
        </section>
      ) : null}

      {tab === "import" ? (
        <section className="stack">
          <section className="flat-panel admin-preview">
            <div className="panel-head">
              <div>
                <p className="eyebrow">IMPORT</p>
                <h2>
                  <Upload size={18} />
                  导入预览
                </h2>
              </div>
              <div className="actions">
                <button className="button secondary" type="button" onClick={() => void previewImport()} disabled={importLoading || !importFile}>
                  <FileSpreadsheet size={16} />
                  预览
                </button>
                <button className="button" type="button" onClick={() => void commitImport()} disabled={importLoading || !importFile || !hasCurrentImportPreview || !importOperator.trim()}>
                  <Upload size={16} />
                  提交导入
                </button>
              </div>
            </div>
            <div className="import-control-grid">
              <label className="field">
                <span>文件</span>
                <input type="file" accept=".csv,.xlsx,.xlsm" onChange={updateImportFile} />
              </label>
              <label className="field">
                <span>operator</span>
                <input value={importOperator} onChange={(event) => setImportOperator(event.target.value)} placeholder="提交导入人" />
              </label>
            </div>
            {importError ? <p className="error admin-inline-error">{importError}</p> : null}
            <div className="import-summary-row" aria-label="导入摘要">
              {importStatuses.map((key) => (
                <StatusPill key={key} tone={importStatusTone[key]}>
                  {key} {importSummary[key] ?? 0}
                </StatusPill>
              ))}
              {importFile ? <StatusPill tone="accent">{importFile.name}</StatusPill> : null}
            </div>
          </section>

          <section className="flat-panel">
            <div className="task-table-head">
              <strong>{importPreview ? `${importPreview.rows.length} 行预览` : "等待导入文件"}</strong>
              <StatusPill tone="accent">前 40 行</StatusPill>
            </div>
            <div className="dense-table-wrap">
              <table className="dense-table admin-preview-table">
                <thead>
                  <tr>
                    <th>行</th>
                    <th>状态</th>
                    <th>query</th>
                    <th>platform</th>
                    <th>series_id</th>
                    <th>source</th>
                    <th>错误</th>
                  </tr>
                </thead>
                <tbody>
                  {previewRows.map((row) => (
                    <tr key={`${row.row_number}-${row.status}`}>
                      <td>{row.row_number}</td>
                      <td>
                        <StatusPill tone={importStatusTone[row.status] ?? "default"}>{row.status}</StatusPill>
                      </td>
                      <td>{row.query ?? "-"}</td>
                      <td>{row.platform ?? "-"}</td>
                      <td>{row.series_id ?? "-"}</td>
                      <td>{row.source ?? "-"}</td>
                      <td>{row.error ?? "-"}</td>
                    </tr>
                  ))}
                  {!previewRows.length ? (
                    <tr>
                      <td colSpan={7}>上传文件后先生成导入预览。</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>
        </section>
      ) : null}
    </main>
  );
}
