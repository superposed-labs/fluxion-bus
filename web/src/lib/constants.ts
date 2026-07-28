import type { TaskStatus } from "../types";

export const STATUSES: TaskStatus[] = [
  "RECEIVED",
  "VALIDATED",
  "QUEUED",
  "RUNNING",
  "RETRYING",
  "RETURNED",
  "FAILED",
  "CANCELED",
  "INTERRUPTED",
];

// Steady-state subset surfaced as filter chips.
export const FILTER_STATUSES: TaskStatus[] = [
  "RUNNING",
  "QUEUED",
  "RETURNED",
  "FAILED",
  "CANCELED",
  "INTERRUPTED",
];

export const EXECUTORS = ["claude", "codex", "antigravity"] as const;
export type Executor = (typeof EXECUTORS)[number];

export const CHANNELS = ["slack", "local", "telegram", "line", "qqbot", "wechat", "feishu"] as const;
export type Channel = (typeof CHANNELS)[number];

export const STATUS_LABEL: Record<string, string> = {
  RECEIVED: "Received",
  VALIDATED: "Validated",
  QUEUED: "Queued",
  RUNNING: "Running",
  RETRYING: "Retrying",
  RETURNED: "Returned",
  FAILED: "Failed",
  CANCELED: "Canceled",
  // Owning process vanished mid-run; no result was ever recorded.
  INTERRUPTED: "Interrupted",
};

export const STATUS_VAR: Record<string, string> = {
  RECEIVED: "var(--st-received)",
  VALIDATED: "var(--st-validated)",
  QUEUED: "var(--st-queued)",
  RUNNING: "var(--st-running)",
  RETRYING: "var(--st-retrying)",
  RETURNED: "var(--st-returned)",
  FAILED: "var(--st-failed)",
  CANCELED: "var(--st-canceled)",
  INTERRUPTED: "var(--st-interrupted)",
};

// Sort order used by the task list — active states first.
export const STATUS_PRIORITY: Record<string, number> = {
  RUNNING: 0,
  RETRYING: 1,
  QUEUED: 2,
  VALIDATED: 3,
  RECEIVED: 4,
  FAILED: 5,
  RETURNED: 6,
  CANCELED: 7,
  INTERRUPTED: 8,
};

export function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

export function statusVar(status: string): string {
  return STATUS_VAR[status] ?? "var(--st-queued)";
}

export function statusPriority(status: string): number {
  return STATUS_PRIORITY[status] ?? 99;
}

export const EXECUTOR_VAR: Record<string, string> = {
  codex: "var(--ex-codex)",
  claude: "var(--ex-claude)",
  antigravity: "var(--ex-antigravity)",
};

export function executorVar(ex: string): string {
  return EXECUTOR_VAR[ex] ?? "var(--ex-codex)";
}
