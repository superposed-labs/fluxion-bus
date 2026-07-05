import type { Artifact, ChangedFile, Task } from "../types";

// Raw event line shape — mirrors what the Python gateway writes to
// data/tasks.jsonl. Most fields are optional because different lifecycle
// transitions carry different payloads.
export interface RawTaskEvent {
  timestamp?: string | null;
  task_id: string;
  status?: string;
  task?: {
    id?: string;
    channel?: string;
    user_id?: string;
    text?: string;
    workspace?: string;
    metadata?: {
      executor?: string;
      conversation_key?: string;
      [k: string]: unknown;
    };
  };
  result?: {
    summary?: string;
    executor_session_id?: string | null;
    changed_files?: (ChangedFile | string)[];
    artifacts?: (Artifact | string)[];
  };
}

// Maps the Python aggregator's _TIMESTAMP_FIELD_BY_STATUS verbatim — the
// client patches the same per-status timestamp slots the server would.
const TIMESTAMP_FIELD: Record<string, keyof Task["timestamp"]> = {
  RECEIVED: "received_at",
  VALIDATED: "validated_at",
  QUEUED: "queued_at",
  RUNNING: "started_at",
  RETRYING: "started_at",
  RETURNED: "ended_at",
  FAILED: "ended_at",
  CANCELED: "ended_at",
};

function newTask(taskId: string, event: RawTaskEvent): Task {
  const td = event.task ?? {};
  const metadata = td.metadata ?? {};
  const channel = td.channel ?? "local";
  const userId = td.user_id ?? "";
  const executor = (metadata.executor as string | undefined) ?? "codex";
  const conversationKey =
    (metadata.conversation_key as string | undefined) ?? `${channel}:${userId}`;

  const channelMeta =
    channel === "slack"
      ? { workspace: "local-slack", channel: "slack", user: userId }
      : { host: "local", cwd: td.workspace ?? "" };

  return {
    task_id: taskId,
    executor,
    model: (metadata.model as string | undefined) ?? "",
    channel,
    channel_meta: channelMeta,
    conversation_key: conversationKey,
    status: event.status ?? "RECEIVED",
    summary: td.text ?? "",
    executor_session_id: null,
    log_file: `data/logs/task-${taskId}.log`,
    timestamp: {
      received_at: null,
      validated_at: null,
      queued_at: null,
      started_at: null,
      ended_at: null,
    },
    changed_files: [],
    diff_summary: { files: 0, additions: 0, deletions: 0 },
    artifacts: [],
    stdout: [],
    stderr: [],
  };
}

const ARTIFACT_KIND_BY_EXT: Record<string, string> = {
  ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
  ".zip": "archive", ".tar": "archive", ".gz": "archive",
  ".log": "log", ".txt": "log",
  ".json": "data", ".yaml": "data", ".yml": "data",
  ".xlsx": "sheet", ".xls": "sheet", ".csv": "sheet",
};

function normalizeArtifact(art: Artifact | string): Artifact | null {
  if (typeof art !== "string") return art;
  const slash = art.lastIndexOf("/");
  const name = slash >= 0 ? art.slice(slash + 1) : art;
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : "";
  return {
    name,
    kind: ARTIFACT_KIND_BY_EXT[ext] ?? "report",
    size: "0 B",
    path: art,
  };
}

function normalizeChangedFile(item: ChangedFile | string): ChangedFile | null {
  if (typeof item !== "string") return item;
  return { op: "M", path: item, additions: 0, deletions: 0 };
}

function applyResult(task: Task, result: NonNullable<RawTaskEvent["result"]>): void {
  task.summary = result.summary || task.summary;
  task.executor_session_id =
    result.executor_session_id ?? task.executor_session_id;

  const changed = (result.changed_files ?? [])
    .map(normalizeChangedFile)
    .filter((f): f is ChangedFile => f !== null);
  task.changed_files = changed;
  task.diff_summary = {
    files: changed.length,
    additions: changed.reduce((s, f) => s + (f.additions ?? 0), 0),
    deletions: changed.reduce((s, f) => s + (f.deletions ?? 0), 0),
  };
  task.artifacts = (result.artifacts ?? [])
    .map(normalizeArtifact)
    .filter((a): a is Artifact => a !== null);
}

/** Returns a new tasks array with the event applied. Pure — does not mutate input. */
export function applyEvent(tasks: Task[], event: RawTaskEvent): Task[] {
  const taskId = event.task_id;
  if (!taskId) return tasks;

  const idx = tasks.findIndex((t) => t.task_id === taskId);
  // Clone so React sees a new reference; cloning only the affected task is enough.
  const next = idx >= 0 ? [...tasks] : [newTask(taskId, event), ...tasks];
  const targetIdx = idx >= 0 ? idx : 0;
  const target = { ...next[targetIdx]!, timestamp: { ...next[targetIdx]!.timestamp } };

  if (event.status) {
    target.status = event.status;
    const field = TIMESTAMP_FIELD[event.status];
    if (field && event.timestamp) {
      target.timestamp[field] = event.timestamp;
    }
  }
  if (event.task?.text) {
    target.summary = event.task.text;
  }
  if (event.result) {
    applyResult(target, event.result);
  }

  next[targetIdx] = target;
  return next;
}
