import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const failures = [];

function read(path) {
  try {
    return readFileSync(join(root, path), "utf8");
  } catch (error) {
    failures.push(`${path} must be readable (${error.message})`);
    return null;
  }
}

function readJson(path) {
  const content = read(path);
  if (content === null) {
    return null;
  }

  try {
    return JSON.parse(content);
  } catch (error) {
    failures.push(`${path} must contain valid JSON (${error.message})`);
    return null;
  }
}

function assertIncludes(path, expected) {
  const content = read(path);
  if (content === null) {
    return;
  }

  if (!content.includes(expected)) {
    failures.push(`${path} must include ${expected}`);
  }
}

function assertNotIncludes(path, unexpected) {
  const content = read(path);
  if (content === null) {
    return;
  }

  if (content.includes(unexpected)) {
    failures.push(`${path} must not include ${unexpected}`);
  }
}

function assertPackageJsonContracts() {
  const packageJson = readJson("apps/web/package.json");
  if (packageJson === null) {
    return;
  }

  if (!Object.hasOwn(packageJson.dependencies ?? {}, "lucide-react")) {
    failures.push("apps/web/package.json dependencies must include lucide-react");
  }

  if (!Object.hasOwn(packageJson.scripts ?? {}, "verify:ui")) {
    failures.push("apps/web/package.json scripts must include verify:ui");
  }
}

assertPackageJsonContracts();
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
assertIncludes("apps/web/app/series-admin/page.tsx", "SeriesAdminPage");
assertIncludes("apps/web/app/series-admin/page.tsx", "series-admin");
assertIncludes("apps/web/app/series-admin/page.tsx", "别名");
assertIncludes("apps/web/app/series-admin/page.tsx", "导入预览");
assertNotIncludes("apps/web/app/components/app-chrome.tsx", "/series-admin");

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

const directAccessTargets = [
  "apps/web/app/vehicle/page.tsx",
  "apps/web/app/candidates/page.tsx",
  "apps/web/app/progress/page.tsx",
  "apps/web/app/result/page.tsx",
];

for (const path of directAccessTargets) {
  assertNotIncludes(path, "!flowState.accessVersion");
  assertNotIncludes(path, "!state.accessVersion");
}

if (failures.length > 0) {
  console.error("UI contract verification failed:");
  for (const failure of failures) {
    console.error(`- ${failure}`);
  }
  process.exitCode = 1;
} else {
  console.log("UI contract verification passed.");
}
