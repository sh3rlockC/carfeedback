# Flat Beige Workbench UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the frontend as a flat, beige, high-density business workbench centered on the new task system.

**Architecture:** Keep the existing Next.js App Router and API contracts. Add a reusable workbench shell, design tokens, density state, icon-enhanced components, task display helpers, and a lightweight UI contract verification script. Rework pages incrementally so every task produces a usable route and keeps old routes available.

**Tech Stack:** Next.js 15, React 19, TypeScript, CSS modules via global stylesheet, `lucide-react`, existing REST API helpers.

---

## File Structure

- Modify `apps/web/package.json` and `apps/web/package-lock.json`: add `lucide-react` and a UI contract verification script.
- Create `apps/web/scripts/verify-ui-contracts.mjs`: static regression checks for routing, hidden legacy navigation, copy cleanup, density mode, and icon dependency.
- Create `apps/web/lib/task-display.ts`: shared labels, ETA formatting, status tone, task sorting, and artifact labels.
- Create `apps/web/lib/density.ts`: density constants and localStorage helpers.
- Modify `apps/web/app/globals.css`: replace the dark neon theme with flat beige tokens and dense workbench styles.
- Modify `apps/web/app/components/ui.tsx`: keep generic UI exports, add flat panel/table/status primitives and icon-ready button classes.
- Replace `apps/web/app/components/app-chrome.tsx`: workbench shell with sidebar navigation, top task status bar, density toggle, and no legacy step rail in main navigation.
- Modify `apps/web/app/layout.tsx`: update metadata to "车型口碑工作台".
- Replace `apps/web/app/page.tsx`: make `/` the workbench overview instead of redirecting to `/passphrase`.
- Modify `apps/web/app/tasks/new/page.tsx`: make new task creation the primary query flow.
- Modify `apps/web/app/tasks/page.tsx`: rebuild task center in flat dense table layout.
- Modify `apps/web/app/tasks/[taskId]/page.tsx`: rebuild task detail first screen around ETA, platform status, and user actions.
- Modify `apps/web/app/result/page.tsx`: apply result dashboard layout and remove retention-copy surface.
- Modify legacy pages `apps/web/app/passphrase/page.tsx`, `apps/web/app/vehicle/page.tsx`, `apps/web/app/candidates/page.tsx`, `apps/web/app/progress/page.tsx`: preserve route availability, remove legacy main-flow assumptions, and align visuals/copy.

## Task 1: Add UI Contract Checks And Icon Dependency

**Files:**
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Create: `apps/web/scripts/verify-ui-contracts.mjs`

- [ ] **Step 1: Write failing UI contract script**

Create `apps/web/scripts/verify-ui-contracts.mjs` with this content:

```javascript
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();

function read(path) {
  return readFileSync(join(root, path), "utf8");
}

function assertIncludes(path, expected) {
  const content = read(path);
  if (!content.includes(expected)) {
    throw new Error(`${path} must include ${expected}`);
  }
}

function assertNotIncludes(path, unexpected) {
  const content = read(path);
  if (content.includes(unexpected)) {
    throw new Error(`${path} must not include ${unexpected}`);
  }
}

assertIncludes("apps/web/package.json", "\"lucide-react\"");
assertIncludes("apps/web/package.json", "\"verify:ui\"");
assertIncludes("apps/web/app/layout.tsx", "车型口碑工作台");
assertIncludes("apps/web/app/page.tsx", "WorkbenchOverviewPage");
assertNotIncludes("apps/web/app/page.tsx", "/passphrase");
assertIncludes("apps/web/app/components/app-chrome.tsx", "工作台总览");
assertIncludes("apps/web/app/components/app-chrome.tsx", "新建任务");
assertIncludes("apps/web/app/components/app-chrome.tsx", "任务中心");
assertIncludes("apps/web/app/components/app-chrome.tsx", "结果归档");
assertNotIncludes("apps/web/app/components/app-chrome.tsx", "StepRail");
assertIncludes("apps/web/lib/density.ts", "koubei-density-mode");
assertIncludes("apps/web/app/tasks/[taskId]/page.tsx", "预计完成");
assertIncludes("apps/web/app/tasks/[taskId]/page.tsx", "继续等待");
assertIncludes("apps/web/app/tasks/[taskId]/page.tsx", "生成降级结果");

const copyTargets = [
  "apps/web/app/components/app-chrome.tsx",
  "apps/web/app/tasks/page.tsx",
  "apps/web/app/tasks/new/page.tsx",
  "apps/web/app/tasks/[taskId]/page.tsx",
  "apps/web/app/result/page.tsx",
];

for (const path of copyTargets) {
  assertNotIncludes(path, "三天");
  assertNotIncludes(path, "72 小时");
  assertNotIncludes(path, "72小时");
  assertNotIncludes(path, "每次重新采集");
  assertNotIncludes(path, "情报舱");
}
```

- [ ] **Step 2: Run script to verify it fails**

Run:

```bash
node apps/web/scripts/verify-ui-contracts.mjs
```

Expected: FAIL because `lucide-react`, `verify:ui`, `WorkbenchOverviewPage`, and the new shell strings do not exist yet.

- [ ] **Step 3: Add dependency and npm script**

Run:

```bash
npm --prefix apps/web install lucide-react
```

Then ensure `apps/web/package.json` contains:

```json
{
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "typecheck": "tsc --noEmit",
    "verify:ui": "node ../../apps/web/scripts/verify-ui-contracts.mjs"
  },
  "dependencies": {
    "lucide-react": "^0.468.0",
    "next": "^15.3.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0"
  }
}
```

Keep the exact installed `lucide-react` version produced by npm if it differs from the snippet.

- [ ] **Step 4: Run script and confirm remaining failures**

Run:

```bash
npm --prefix apps/web run verify:ui
```

Expected: FAIL only on files that have not been redesigned yet.

- [ ] **Step 5: Commit**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/scripts/verify-ui-contracts.mjs
git commit -m "test: add workbench ui contract checks"
```

## Task 2: Add Shared Task Display Helpers

**Files:**
- Create: `apps/web/lib/task-display.ts`
- Modify: `apps/web/app/tasks/page.tsx`
- Modify: `apps/web/app/tasks/[taskId]/page.tsx`

- [ ] **Step 1: Write helper module**

Create `apps/web/lib/task-display.ts`:

```typescript
import type { TaskArtifact, TaskListItem } from "./api-types";

export const runningStatuses = new Set(["running"]);
export const queuedStatuses = new Set(["queued", "waiting_agent", "retry_wait"]);
export const activeStatuses = new Set(["queued", "running", "waiting_agent", "retry_wait", "retry_paused"]);
export const completedStatuses = new Set(["completed", "completed_degraded"]);

export const taskTypeLabels: Record<TaskListItem["task_type"], string> = {
  single: "单车型",
  comparison: "多车型对比",
};

export const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  waiting_agent: "等待车道",
  retry_wait: "等待重试",
  retry_paused: "重试暂停",
  completed: "已完成",
  completed_degraded: "降级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

export const stageLabels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  checking_incremental: "检查历史语料",
  collecting_autohome: "汽车之家采集",
  collecting_dcd: "懂车帝采集",
  postprocessing: "汇总整理",
  summarizing: "摘要生成",
  rendering_wordcloud: "词云生成",
  generating_ai_report: "生成报告",
  building_qa_corpus: "构建问答索引",
  collecting_models: "补齐车型",
  comparing: "生成对比",
  completed: "已完成",
  completed_degraded: "降级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

export type Tone = "default" | "success" | "warning" | "danger" | "accent";

export function labelFor(value: string, labels: Record<string, string>) {
  return labels[value] ?? value;
}

export function statusTone(status: string): Tone {
  if (status === "completed") {
    return "success";
  }
  if (status === "completed_degraded" || queuedStatuses.has(status) || status === "retry_paused") {
    return "warning";
  }
  if (status === "failed" || status === "cancelled" || status === "expired") {
    return "danger";
  }
  if (status === "running") {
    return "accent";
  }
  return "default";
}

export function formatDateTime(value: string | null) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function formatEtaMinutes(seconds: number | null) {
  if (seconds === null) {
    return "计算中";
  }
  if (seconds <= 0) {
    return "少于 1 分钟";
  }
  return `${Math.max(1, Math.ceil(seconds / 60))} 分钟`;
}

export function formatEtaCell(seconds: number | null) {
  return seconds === null ? "-" : formatEtaMinutes(seconds);
}

export function safeTimestamp(value: string | null | undefined) {
  if (!value) {
    return 0;
  }
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

export function newestFirst(a: TaskListItem, b: TaskListItem) {
  return safeTimestamp(b.created_at) - safeTimestamp(a.created_at) || b.task_id.localeCompare(a.task_id);
}

export function artifactLabel(artifact: TaskArtifact) {
  const labels: Record<string, string> = {
    business_zip: "打包结果",
    merged_raw_excel: "合并原始 Excel",
    vehicle_raw_excel: "车型原始 Excel",
    ai_report: "AI 报告",
    wordcloud: "词云",
  };
  return labels[artifact.artifact_type] ?? artifact.artifact_type;
}
```

- [ ] **Step 2: Import helpers in task pages**

In `apps/web/app/tasks/page.tsx` and `apps/web/app/tasks/[taskId]/page.tsx`, remove duplicated label/format helper declarations and import from `@/lib/task-display`:

```typescript
import {
  activeStatuses,
  artifactLabel,
  completedStatuses,
  formatDateTime,
  formatEtaCell,
  formatEtaMinutes,
  labelFor,
  newestFirst,
  queuedStatuses,
  runningStatuses,
  stageLabels,
  statusLabels,
  statusTone,
  taskTypeLabels,
} from "@/lib/task-display";
```

Use only the imported names needed by each page.

- [ ] **Step 3: Run typecheck**

Run:

```bash
npm --prefix apps/web run typecheck
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/web/lib/task-display.ts apps/web/app/tasks/page.tsx apps/web/app/tasks/[taskId]/page.tsx
git commit -m "refactor: share task display helpers"
```

## Task 3: Implement Density State

**Files:**
- Create: `apps/web/lib/density.ts`
- Modify: `apps/web/app/components/app-chrome.tsx`
- Modify: `apps/web/app/globals.css`

- [ ] **Step 1: Create density helper**

Create `apps/web/lib/density.ts`:

```typescript
export type DensityMode = "comfortable" | "compact";

export const DENSITY_STORAGE_KEY = "koubei-density-mode";
export const defaultDensityMode: DensityMode = "comfortable";

export function readDensityMode(): DensityMode {
  if (typeof window === "undefined") {
    return defaultDensityMode;
  }
  const stored = window.localStorage.getItem(DENSITY_STORAGE_KEY);
  return stored === "compact" || stored === "comfortable" ? stored : defaultDensityMode;
}

export function writeDensityMode(mode: DensityMode) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(DENSITY_STORAGE_KEY, mode);
}

export function nextDensityMode(mode: DensityMode): DensityMode {
  return mode === "compact" ? "comfortable" : "compact";
}
```

- [ ] **Step 2: Add density attributes in AppChrome**

In `apps/web/app/components/app-chrome.tsx`, add:

```typescript
import { Columns3, LayoutDashboard, ListChecks, PackageCheck, Plus, Rows3 } from "lucide-react";
import { defaultDensityMode, nextDensityMode, readDensityMode, writeDensityMode, type DensityMode } from "@/lib/density";
```

Add state:

```typescript
const [density, setDensity] = useState<DensityMode>(defaultDensityMode);

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
```

Wrap the shell root with:

```tsx
<div className="workbench-shell" data-density={density}>
```

Add a density button in the top status bar:

```tsx
<button className="icon-text-button" type="button" onClick={toggleDensity}>
  {density === "compact" ? <Rows3 size={16} /> : <Columns3 size={16} />}
  {density === "compact" ? "紧凑" : "标准"}
</button>
```

- [ ] **Step 3: Add density CSS tokens**

In `apps/web/app/globals.css`, define:

```css
.workbench-shell {
  --density-gap: 12px;
  --density-panel-padding: 14px;
  --density-row-padding: 10px 12px;
  --density-control-height: 36px;
}

.workbench-shell[data-density="compact"] {
  --density-gap: 8px;
  --density-panel-padding: 10px;
  --density-row-padding: 7px 10px;
  --density-control-height: 30px;
}
```

Use these variables in panels, fields, buttons, and task tables introduced in later tasks.

- [ ] **Step 4: Verify script still fails on later missing pages only**

Run:

```bash
npm --prefix apps/web run verify:ui
```

Expected: FAIL on overview/task/detail strings not yet implemented, but not on `apps/web/lib/density.ts`.

- [ ] **Step 5: Commit**

```bash
git add apps/web/lib/density.ts apps/web/app/components/app-chrome.tsx apps/web/app/globals.css
git commit -m "feat: add workbench density mode"
```

## Task 4: Replace Global Theme And Workbench Shell

**Files:**
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/app/components/app-chrome.tsx`
- Modify: `apps/web/app/components/ui.tsx`
- Modify: `apps/web/app/layout.tsx`

- [ ] **Step 1: Update metadata**

In `apps/web/app/layout.tsx`, change metadata to:

```typescript
export const metadata: Metadata = {
  title: "车型口碑工作台",
  description: "车型口碑增量采集、任务管理、结果仪表盘和交付物下载工作台。",
};
```

- [ ] **Step 2: Replace CSS tokens**

At the top of `apps/web/app/globals.css`, replace the current `:root` block with:

```css
:root {
  color-scheme: light;
  --bg: #efe8d8;
  --bg-soft: #f5efdf;
  --surface: #fbf7ec;
  --surface-muted: #f0e7d3;
  --border: #d8cfba;
  --border-strong: #b9ab8e;
  --text: #1d211d;
  --text-strong: #111411;
  --muted: #747066;
  --muted-strong: #514d45;
  --primary: #1f3d33;
  --primary-strong: #142b24;
  --primary-soft: #e1eadf;
  --warning: #c9a24d;
  --danger: #b45c4f;
  --success: #2f6f4f;
  --white: #ffffff;
  --radius: 8px;
  --radius-sm: 6px;
}
```

Replace `body` background with a flat workbench background:

```css
body {
  margin: 0;
  color: var(--text);
  background: var(--bg);
  font-family: "Noto Sans SC", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
}

body::before {
  display: none;
}
```

- [ ] **Step 3: Replace AppChrome shell**

Rewrite `apps/web/app/components/app-chrome.tsx` so the main JSX shape is:

```tsx
return (
  <div className="workbench-shell" data-density={density}>
    <aside className="workbench-sidebar">
      <Link className="workbench-brand" href="/">
        <LayoutDashboard size={20} />
        <span>车型口碑工作台</span>
      </Link>
      <nav className="workbench-nav" aria-label="主导航">
        <Link className={navClass("/")} href="/">
          <LayoutDashboard size={17} />
          工作台总览
        </Link>
        <Link className={navClass("/tasks/new")} href="/tasks/new">
          <Plus size={17} />
          新建任务
        </Link>
        <Link className={navClass("/tasks")} href="/tasks">
          <ListChecks size={17} />
          任务中心
        </Link>
        <Link className={navClass("/result")} href="/result">
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
        <button className="icon-text-button" type="button" onClick={toggleDensity}>
          {density === "compact" ? <Rows3 size={16} /> : <Columns3 size={16} />}
          {density === "compact" ? "紧凑" : "标准"}
        </button>
      </header>
      <main className="workbench-content">{children}</main>
    </div>
  </div>
);
```

Implement `navClass(href: string)` using the current pathname so `/tasks/new` is active only on that route and `/tasks` is active for task list/detail routes.

- [ ] **Step 4: Add shell CSS**

Add this CSS after base element rules:

```css
.workbench-shell {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  min-height: 100vh;
  gap: 0;
}

.workbench-sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  border-right: 1px solid var(--border);
  background: var(--surface);
  padding: 14px;
}

.workbench-brand,
.workbench-nav a,
.icon-text-button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.workbench-brand {
  width: 100%;
  border-bottom: 1px solid var(--border);
  padding: 0 0 14px;
  color: var(--text-strong);
  font-weight: 900;
}

.workbench-nav {
  display: grid;
  gap: 6px;
  margin-top: 14px;
}

.workbench-nav a {
  min-height: 34px;
  border-radius: var(--radius-sm);
  padding: 0 10px;
  color: var(--muted-strong);
  font-size: 14px;
  font-weight: 800;
}

.workbench-nav a.active {
  background: var(--primary);
  color: var(--surface);
}

.workbench-main {
  min-width: 0;
}

.workbench-topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  min-height: 58px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--border);
  background: rgba(251, 247, 236, 0.96);
  padding: 10px 18px;
}

.workbench-content {
  width: min(1440px, 100%);
  padding: 16px;
}

.topbar-label {
  margin: 0 0 2px;
  color: var(--muted);
  font-size: 12px;
}

.topbar-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  color: var(--muted-strong);
  font-size: 13px;
}

.icon-text-button {
  min-height: var(--density-control-height);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0 10px;
  cursor: pointer;
  background: var(--surface);
  color: var(--text);
  font-weight: 800;
}

@media (max-width: 980px) {
  .workbench-shell {
    grid-template-columns: 64px minmax(0, 1fr);
  }

  .workbench-brand span,
  .workbench-nav a {
    font-size: 0;
  }

  .workbench-nav a {
    justify-content: center;
  }
}

@media (max-width: 640px) {
  .workbench-shell {
    display: block;
  }

  .workbench-sidebar {
    position: static;
    height: auto;
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }

  .workbench-nav {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
```

- [ ] **Step 5: Run checks**

Run:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run verify:ui
```

Expected: `typecheck` PASS; `verify:ui` may still fail on page strings not yet migrated.

- [ ] **Step 6: Commit**

```bash
git add apps/web/app/layout.tsx apps/web/app/components/app-chrome.tsx apps/web/app/components/ui.tsx apps/web/app/globals.css
git commit -m "feat: add flat workbench shell"
```

## Task 5: Build Workbench Overview At `/`

**Files:**
- Replace: `apps/web/app/page.tsx`

- [ ] **Step 1: Replace redirect with client overview page**

Replace `apps/web/app/page.tsx` with:

```tsx
"use client";

import Link from "next/link";
import { Activity, Clock3, Database, Plus, Rows3 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";
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
import { StatusPill } from "./components/ui";

function fallbackLoad(tasks: TaskListItem[]): TaskLoadResponse {
  return {
    running_task_count: tasks.filter((task) => runningStatuses.has(task.status)).length,
    queued_task_count: tasks.filter((task) => queuedStatuses.has(task.status)).length,
    platforms: {},
  };
}

export default function WorkbenchOverviewPage() {
  const [tasks, setTasks] = useState<TaskListItem[]>([]);
  const [load, setLoad] = useState<TaskLoadResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function run() {
      try {
        const [taskPayload, loadPayload] = await Promise.all([
          apiRequest<TaskListItem[]>("/api/tasks"),
          apiRequest<TaskLoadResponse>("/api/tasks/load").catch(() => null),
        ]);
        if (!cancelled) {
          setTasks(taskPayload);
          setLoad(loadPayload);
        }
      } catch {
        if (!cancelled) {
          setError("暂时无法读取任务总览。");
        }
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  const effectiveLoad = load ?? fallbackLoad(tasks);
  const activeTasks = useMemo(
    () => tasks.filter((task) => runningStatuses.has(task.status) || queuedStatuses.has(task.status)).sort(newestFirst).slice(0, 8),
    [tasks],
  );
  const recentCompleted = useMemo(() => tasks.filter((task) => task.completed_at).sort(newestFirst).slice(0, 4), [tasks]);
  const platformText = Object.entries(effectiveLoad.platforms)
    .map(([platform, value]) => `${platform} ${value.available}/${value.total}`)
    .join(" / ");

  return (
    <main className="overview-page">
      <section className="overview-hero">
        <div>
          <p className="eyebrow">WORKBENCH OVERVIEW</p>
          <h1>车型口碑工作台</h1>
          <p className="helper">查看运行中的采集任务、排队状态和可用车道，或直接创建新的单车型/多车型口碑分析。</p>
        </div>
        <Link className="button" href="/tasks/new">
          <Plus size={16} />
          新建查询
        </Link>
      </section>

      {error ? <p className="error">{error}</p> : null}

      <section className="metric-grid">
        <div className="metric-tile"><Activity size={18} /><span>运行任务</span><strong>{effectiveLoad.running_task_count}</strong></div>
        <div className="metric-tile"><Clock3 size={18} /><span>排队任务</span><strong>{effectiveLoad.queued_task_count}</strong></div>
        <div className="metric-tile"><Rows3 size={18} /><span>可用车道</span><strong>{platformText || "暂无数据"}</strong></div>
        <div className="metric-tile"><Database size={18} /><span>最近完成</span><strong>{recentCompleted.length}</strong></div>
      </section>

      <section className="overview-grid">
        <div className="flat-panel">
          <div className="panel-head">
            <h2>运行中任务与队列</h2>
            <StatusPill tone="accent">{activeTasks.length} 个任务</StatusPill>
          </div>
          <div className="task-table-wrap">
            <table className="task-table">
              <thead>
                <tr><th>任务</th><th>类型</th><th>状态</th><th>阶段</th><th>预计</th><th>操作</th></tr>
              </thead>
              <tbody>
                {activeTasks.map((task) => (
                  <tr key={task.task_id}>
                    <td><strong>{task.display_name}</strong><span>{task.task_id}</span></td>
                    <td>{taskTypeLabels[task.task_type]}</td>
                    <td><StatusPill tone={statusTone(task.status)}>{labelFor(task.status, statusLabels)}</StatusPill></td>
                    <td>{labelFor(task.current_stage, stageLabels)}</td>
                    <td>{formatEtaCell(task.eta_seconds)}</td>
                    <td><Link className="table-action" href={`/tasks/${task.task_id}`}>查看</Link></td>
                  </tr>
                ))}
                {!activeTasks.length ? <tr><td colSpan={6}>当前没有运行或排队任务。</td></tr> : null}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="quick-create-panel">
          <h2>快速新建查询</h2>
          <p>默认使用历史语料库做增量采集，支持单车型和 2-5 车型对比。</p>
          <Link className="button" href="/tasks/new"><Plus size={16} /> 创建任务</Link>
          <Link className="button secondary" href="/tasks">查看任务中心</Link>
        </aside>
      </section>

      <section className="flat-panel">
        <div className="panel-head"><h2>最近完成</h2><Link className="table-action" href="/tasks">全部任务</Link></div>
        <div className="recent-result-grid">
          {recentCompleted.map((task) => (
            <Link className="result-card" key={task.task_id} href={`/tasks/${task.task_id}`}>
              <strong>{task.display_name}</strong>
              <span>{formatDateTime(task.completed_at)}</span>
            </Link>
          ))}
          {!recentCompleted.length ? <p className="status-copy">暂无完成任务。</p> : null}
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 2: Add overview CSS**

Add `.overview-page`, `.overview-hero`, `.metric-grid`, `.metric-tile`, `.overview-grid`, `.flat-panel`, `.panel-head`, `.quick-create-panel`, `.table-action`, `.recent-result-grid`, and `.result-card` styles using beige surfaces and density variables.

- [ ] **Step 3: Run checks**

Run:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run verify:ui
```

Expected: `typecheck` PASS. `verify:ui` progresses past `/` checks.

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/page.tsx apps/web/app/globals.css
git commit -m "feat: add workbench overview"
```

## Task 6: Redesign New Task Page

**Files:**
- Modify: `apps/web/app/tasks/new/page.tsx`
- Modify: `apps/web/app/globals.css`

- [ ] **Step 1: Add icon imports**

Add:

```typescript
import { GitCompareArrows, Plus, Search, Trash2 } from "lucide-react";
```

- [ ] **Step 2: Replace mode cards with flat segmented cards**

Use this structure inside the first panel:

```tsx
<div className="task-mode-grid">
  <button className={`mode-card ${mode === "single" ? "selected" : ""}`} type="button" onClick={() => setMode("single")}>
    <Search size={18} />
    <span>单车型查询</span>
    <small>一个车型的增量采集、报告和交付物。</small>
  </button>
  <button className={`mode-card ${mode === "comparison" ? "selected" : ""}`} type="button" onClick={() => setMode("comparison")}>
    <GitCompareArrows size={18} />
    <span>多车型对比</span>
    <small>2 到 5 个车型并行采集并生成对比结果。</small>
  </button>
</div>
```

- [ ] **Step 3: Rewrite submit button copy**

Use:

```tsx
<button className="button" type="submit" disabled={!canSubmit || submitting}>
  <Plus size={16} />
  {submitting ? "正在创建" : "创建增量采集任务"}
</button>
```

- [ ] **Step 4: Add helper copy**

Add this helper text below the form:

```tsx
<p className="field-hint">系统会先对照历史语料库，再采集新增评论。多车型任务会进入可用车道排队。</p>
```

- [ ] **Step 5: Add CSS**

Add `.mode-card`, `.mode-card.selected`, `.task-create-layout`, and `.task-vehicle-row` styles using flat borders, 8px radius, and density variables.

- [ ] **Step 6: Run checks and commit**

Run:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

Expected: both PASS.

Commit:

```bash
git add apps/web/app/tasks/new/page.tsx apps/web/app/globals.css
git commit -m "feat: redesign new task flow"
```

## Task 7: Redesign Task Center

**Files:**
- Modify: `apps/web/app/tasks/page.tsx`
- Modify: `apps/web/app/globals.css`

- [ ] **Step 1: Add icon imports**

Add:

```typescript
import { Filter, ListChecks, Plus, RotateCw } from "lucide-react";
```

- [ ] **Step 2: Replace header**

Use:

```tsx
<section className="flat-panel">
  <div className="page-head">
    <div>
      <p className="eyebrow">TASK CENTER</p>
      <h1>任务中心</h1>
      <p className="helper">筛选历史任务、查看运行状态，并进入结果交付页。</p>
    </div>
    <Link className="button" href="/tasks/new">
      <Plus size={16} />
      新建任务
    </Link>
  </div>
</section>
```

- [ ] **Step 3: Use load metrics**

Replace old `task-load-row` cards with `.metric-grid` tiles for running count, queued count, and platform availability.

- [ ] **Step 4: Make filters compact**

Keep all existing filters but wrap them in:

```tsx
<section className="flat-panel filter-panel">
  <div className="panel-head">
    <h2><Filter size={16} /> 筛选</h2>
    <button className="icon-text-button" type="button" onClick={() => window.location.reload()}>
      <RotateCw size={15} />
      刷新
    </button>
  </div>
  <div className="task-filter-grid">
    <label className="field">
      <span>状态</span>
      <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
        <option value="all">全部</option>
        {statusOptions.map((status) => (
          <option key={status} value={status}>
            {labelFor(status, statusLabels)}
          </option>
        ))}
      </select>
    </label>
    <label className="field">
      <span>类型</span>
      <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
        <option value="all">全部</option>
        <option value="single">单车型</option>
        <option value="comparison">多车型对比</option>
      </select>
    </label>
    <label className="field">
      <span>关键词</span>
      <input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="任务 ID / 车型" />
    </label>
    <label className="field">
      <span>开始日期</span>
      <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
    </label>
    <label className="field">
      <span>结束日期</span>
      <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
    </label>
    <label className="field">
      <span>平台状态</span>
      <select value={platformStatus} onChange={(event) => setPlatformStatus(event.target.value as PlatformStatusFilter)}>
        <option value="all">全部</option>
        <option value="available">有空闲车道</option>
        <option value="crowded">车道占满</option>
      </select>
    </label>
  </div>
</section>
```

- [ ] **Step 5: Update table columns**

Ensure the table headers are:

```tsx
<tr>
  <th>任务</th>
  <th>类型</th>
  <th>状态</th>
  <th>阶段</th>
  <th>预计</th>
  <th>标记</th>
  <th>创建</th>
  <th>完成</th>
  <th>操作</th>
</tr>
```

Add a final cell:

```tsx
<td>
  <Link className="table-action" href={`/tasks/${task.task_id}`}>
    查看
  </Link>
</td>
```

- [ ] **Step 6: Run checks and commit**

Run:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

Expected: both PASS.

Commit:

```bash
git add apps/web/app/tasks/page.tsx apps/web/app/globals.css
git commit -m "feat: redesign task center"
```

## Task 8: Redesign Task Detail

**Files:**
- Modify: `apps/web/app/tasks/[taskId]/page.tsx`
- Modify: `apps/web/app/globals.css`

- [ ] **Step 1: Add action copy and imports**

Add:

```typescript
import { Clock3, Download, Hourglass, Play, RefreshCw, ShieldAlert, XCircle } from "lucide-react";
```

- [ ] **Step 2: Replace progress first screen**

The first progress panel must begin with:

```tsx
<section className="task-detail-grid">
  <div className="eta-card">
    <span>预计完成</span>
    <strong>{formatEtaMinutes(task.eta_seconds)}</strong>
    <p>{task.eta_reason || "系统正在根据队列、平台车道和当前阶段估算剩余时间。"}</p>
  </div>
  <div className="action-card">
    <span>可操作选项</span>
    <div className="actions vertical-actions">
      <button className="button secondary" type="button" onClick={() => void loadTask()}>
        <RefreshCw size={16} />
        继续等待
      </button>
      <button className="button secondary" type="button" disabled={!manageToken || Boolean(actionLoading)}>
        <ShieldAlert size={16} />
        生成降级结果
      </button>
      <button className="button danger" type="button" disabled={!manageToken || Boolean(actionLoading)} onClick={() => void runManagementAction("cancel")}>
        <XCircle size={16} />
        取消任务
      </button>
    </div>
  </div>
</section>
```

If no backend endpoint exists for generating degraded result, keep the button disabled and show the title `后端暂未开放该动作`.

- [ ] **Step 3: Add platform status panels**

Replace the vehicle table header area with:

```tsx
<div className="platform-progress-grid">
  <div className="platform-progress-card">
    <strong>汽车之家</strong>
    <p>车系编号会在任务车辆表中展示；平台进度由当前阶段和事件推导。</p>
  </div>
  <div className="platform-progress-card">
    <strong>懂车帝</strong>
    <p>等待车道、重试、完成或失败状态会在事件流和任务状态中同步。</p>
  </div>
</div>
```

Keep the vehicle table below this section.

- [ ] **Step 4: Redesign result tab**

In the result view, show:

```tsx
<div className="result-delivery-grid">
  {resultArtifacts.map((artifact) => (
    <div className="delivery-card" key={artifact.artifact_id}>
      <Download size={16} />
      <strong>{artifactLabel(artifact)}</strong>
      <span>{artifact.downloadable ? "可下载" : "仅归档"}</span>
      <p>{artifact.path}</p>
    </div>
  ))}
</div>
```

- [ ] **Step 5: Run checks and commit**

Run:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run verify:ui
npm --prefix apps/web run build
```

Expected: all PASS after this task or fail only on result/legacy copy still pending.

Commit:

```bash
git add apps/web/app/tasks/[taskId]/page.tsx apps/web/app/globals.css
git commit -m "feat: redesign task detail"
```

## Task 9: Redesign Result Dashboard And Clean Copy

**Files:**
- Modify: `apps/web/app/result/page.tsx`
- Modify: `apps/web/app/globals.css`

- [ ] **Step 1: Remove retention copy from visible UI**

Search:

```bash
rg -n "三天|72 小时|72小时|每次重新采集|retention_days" apps/web/app/result/page.tsx
```

Remove visible copy that describes deletion/retention. Keep API fields if still needed for compatibility.

- [ ] **Step 2: Replace result cover with dashboard grid**

Use this top-level structure for the single-task result view:

```tsx
<main className="result-dashboard-page">
  <section className="result-dashboard-hero">
    <div className="result-conclusion-card">
      <p className="eyebrow">RESULT DASHBOARD</p>
      <h1>{result.model_name}</h1>
      <p>{primaryConclusion}</p>
    </div>
    <div className="result-delivery-panel">
      <h2>交付物</h2>
      <div className="download-list">
        {resultBundleUrl ? (
          <a className="download-link primary-download" href={resultBundleUrl}>
            下载打包结果
          </a>
        ) : (
          <p className="status-copy">暂无可打包下载的结果文件。</p>
        )}
      </div>
    </div>
  </section>
  <section className="metric-grid">
    <div className="metric-tile"><span>累计评论</span><strong>{result.sample_summary.autohome_count + result.sample_summary.dcd_count}</strong></div>
    <div className="metric-tile"><span>汽车之家</span><strong>{result.sample_summary.autohome_count}</strong></div>
    <div className="metric-tile"><span>懂车帝</span><strong>{result.sample_summary.dcd_count}</strong></div>
    <div className="metric-tile"><span>报告状态</span><strong>{result.ai_available ? "已生成" : "待生成"}</strong></div>
  </section>
  <section className="result-dashboard-grid">
    <div className="flat-panel"><h2>维度矩阵摘要</h2></div>
    <div className="flat-panel"><h2>关键词榜单</h2></div>
    <div className="flat-panel"><h2>评论样本</h2></div>
  </section>
</main>
```

Derive `primaryConclusion` from the first available `template_report.highlights[0]`, otherwise use `本次分析结果已生成，可查看指标摘要和下载交付物。`.

- [ ] **Step 3: Keep detailed report lower on page**

Move the existing report sections below the dashboard top, preserving current data rendering and QA/time-report behavior.

- [ ] **Step 4: Run copy check**

Run:

```bash
npm --prefix apps/web run verify:ui
```

Expected: PASS for result page copy cleanup.

- [ ] **Step 5: Commit**

```bash
git add apps/web/app/result/page.tsx apps/web/app/globals.css
git commit -m "feat: redesign result dashboard"
```

## Task 10: Preserve And Restyle Legacy Routes

**Files:**
- Modify: `apps/web/app/passphrase/page.tsx`
- Modify: `apps/web/app/vehicle/page.tsx`
- Modify: `apps/web/app/candidates/page.tsx`
- Modify: `apps/web/app/progress/page.tsx`
- Modify: `apps/web/app/globals.css`

- [ ] **Step 1: Keep `/passphrase` route accessible**

Change the passphrase page title/copy to make it a compatibility route:

```tsx
<SectionHeader
  eyebrow="LEGACY ACCESS"
  title="兼容访问入口"
  copy="当前工作台默认直接进入；此页面保留用于未来重新启用访问口令。"
/>
```

Change the button text to:

```tsx
{loading ? "正在校验" : "进入工作台"}
```

After success, route to `/` instead of `/vehicle`.

- [ ] **Step 2: Remove old main-flow language from legacy pages**

In `vehicle`, `candidates`, and `progress` pages, replace copy that implies these are the default route with compatibility wording. Example:

```tsx
<p className="field-hint">这是旧流程兼容页面。新查询建议从任务中心创建。</p>
```

Add a visible link to `/tasks/new` near the top:

```tsx
<Link className="button secondary" href="/tasks/new">
  前往新建任务
</Link>
```

- [ ] **Step 3: Run route availability check**

Run:

```bash
npm --prefix apps/web run build
```

Expected: PASS, proving legacy routes still compile.

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/passphrase/page.tsx apps/web/app/vehicle/page.tsx apps/web/app/candidates/page.tsx apps/web/app/progress/page.tsx apps/web/app/globals.css
git commit -m "feat: preserve legacy flow routes"
```

## Task 11: Final Verification With Browser

**Files:**
- Modify only if verification reveals defects in earlier files.

- [ ] **Step 1: Run full frontend checks**

Run:

```bash
npm --prefix apps/web run typecheck
npm --prefix apps/web run verify:ui
npm --prefix apps/web run build
```

Expected: all PASS.

- [ ] **Step 2: Start local app**

Run:

```bash
npm --prefix apps/web run dev -- --hostname 127.0.0.1 --port 3000
```

Expected: Next.js dev server prints that it is ready on `http://127.0.0.1:3000`.

- [ ] **Step 3: Browser-check desktop viewport**

Open `http://127.0.0.1:3000` and verify:

- `/` shows 工作台总览, not the passphrase page.
- Sidebar shows 工作台总览, 新建任务, 任务中心, 结果归档.
- Sidebar does not show old five-step navigation.
- Density toggle changes the shell to compact mode.
- Text does not overflow in sidebar, topbar, metric tiles, or task table.

- [ ] **Step 4: Browser-check tablet viewport**

Set viewport around 1024x768 and verify:

- Sidebar compresses without hiding the main content.
- Task table scrolls horizontally if needed.
- Quick create panel remains accessible.

- [ ] **Step 5: Browser-check legacy routes**

Open:

```text
http://127.0.0.1:3000/passphrase
http://127.0.0.1:3000/vehicle
http://127.0.0.1:3000/candidates
http://127.0.0.1:3000/progress
http://127.0.0.1:3000/result
```

Expected: routes render or show their existing guard states, and none are deleted.

- [ ] **Step 6: Commit verification fixes**

If fixes were required:

```bash
git add apps/web
git commit -m "fix: polish workbench responsive states"
```

If no fixes were required, do not create an empty commit.

## Self-Review

- Spec coverage: visual system, density, left navigation, top task bar, direct overview entry, task-center primary flow, legacy route preservation, task detail ETA/action priority, result dashboard, business copy cleanup, `lucide-react`, desktop/tablet priority, and tests are covered by Tasks 1-11.
- Placeholder scan: no placeholder markers or undefined future tasks remain in this plan.
- Type consistency: task helper names are introduced in Task 2 before page tasks use them. Density helper names are introduced in Task 3 before shell tasks use them. `StatusPill` tone values match the existing component contract.
- Risk: `generate degraded result` has no confirmed backend endpoint. The plan explicitly renders it disabled if no endpoint exists, preserving the user-facing option without inventing API behavior.
