import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");

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
