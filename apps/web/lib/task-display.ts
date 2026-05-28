import type { TaskArtifact, TaskListItem } from "./api-types";

export const runningStatuses = new Set(["running"]);
export const queuedStatuses = new Set(["queued", "waiting_agent", "retry_wait"]);
export const activeStatuses = new Set(["queued", "running", "waiting_agent", "retry_wait", "retry_paused"]);
export const completedStatuses = new Set(["completed", "completed_degraded", "completed_upgraded"]);

export const taskTypeLabels: Record<TaskListItem["task_type"], string> = {
  single: "单车型",
  single_vehicle: "单车型",
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
  completed_upgraded: "升级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

export const stageLabels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  checking_incremental: "检查历史语料",
  resolving_vehicle: "确认车型",
  dispatching_collection: "调度采集",
  collecting_autohome: "汽车之家采集",
  collecting_dcd: "懂车帝采集",
  collecting_models: "并行采集",
  postprocessing: "汇总整理",
  summarizing: "摘要生成",
  rendering_wordcloud: "词云生成",
  generating_ai_report: "生成报告",
  building_qa_corpus: "构建问答索引",
  comparing: "生成对比",
  completed: "已完成",
  completed_degraded: "降级完成",
  completed_upgraded: "升级完成",
  failed: "失败",
  cancelled: "已取消",
  expired: "已过期",
};

export type Tone = "default" | "success" | "warning" | "danger" | "accent";

export function labelFor(value: string, labels: Record<string, string>) {
  return labels[value] ?? value;
}

export function statusTone(status: string): Tone {
  if (status === "completed" || status === "completed_upgraded") {
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

export function taskListStatusTone(status: string): Tone {
  if (status === "completed" || status === "completed_upgraded") {
    return "success";
  }
  if (status === "completed_degraded" || queuedStatuses.has(status)) {
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
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  return `${Math.ceil(seconds / 60)} 分钟`;
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
    excel: "Excel 产物",
    json: "JSON 产物",
    jsonl: "JSONL 产物",
    image_png: "图片产物",
    artifact: "任务产物",
    ai_report: "AI 报告",
    wordcloud: "词云",
  };
  return labels[artifact.artifact_type] ?? artifact.artifact_type;
}
