import type { RawTaskEvent } from "./lib/applyEvent";
import type {
  ExecutorInfo,
  ExecutorsResponse,
  ProviderUsage,
  RunTaskInput,
  RunTaskResponse,
  AutoPingMode,
  AutoPingProviders,
  MonitorSettings,
  MonitorSettingsPatch,
  ScheduleInput,
  ScheduleRule,
  ScheduleRun,
  ScheduleRunsResponse,
  SchedulesResponse,
  Task,
  TasksResponse,
  UsageHistory,
  UsageResponse,
  UsageWindowKey,
} from "./types";

const TOKEN_KEY = "fluxion.uiToken";

function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

function authHeaders(): HeadersInit {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function jsonFetch<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) {
    throw new Error(`${path} → ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export async function fetchTasks(): Promise<Task[]> {
  const data = await jsonFetch<TasksResponse>("/api/tasks");
  return data.tasks ?? [];
}

export async function fetchTask(taskId: string): Promise<Task> {
  return jsonFetch<Task>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export async function runTask(input: RunTaskInput): Promise<RunTaskResponse> {
  return jsonSend<RunTaskResponse>("/api/tasks", "POST", input);
}

export async function fetchUsage(): Promise<ProviderUsage[]> {
  const data = await jsonFetch<UsageResponse>("/api/usage");
  return data.providers ?? [];
}

export async function fetchExecutors(): Promise<ExecutorInfo[]> {
  const data = await jsonFetch<ExecutorsResponse>("/api/executors");
  return data.executors ?? [];
}

export async function fetchUsageHistory(window: UsageWindowKey): Promise<UsageHistory> {
  return jsonFetch<UsageHistory>(`/api/usage/history?window=${encodeURIComponent(window)}`);
}

async function jsonSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: {
      ...authHeaders(),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const parsed = (await res.json()) as { detail?: string };
      if (parsed?.detail) detail = parsed.detail;
    } catch {
      // Non-JSON error body — keep the status line.
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export async function fetchSchedules(): Promise<ScheduleRule[]> {
  const data = await jsonFetch<SchedulesResponse>("/api/schedules");
  return data.schedules ?? [];
}

export async function fetchAutoPing(): Promise<AutoPingProviders> {
  const data = await jsonFetch<{ providers: AutoPingProviders }>("/api/autoping");
  return data.providers;
}

export async function setAutoPing(provider: keyof AutoPingProviders, mode: AutoPingMode): Promise<AutoPingProviders> {
  const data = await jsonSend<{ providers: AutoPingProviders }>("/api/autoping", "PUT", { provider, mode });
  return data.providers;
}

export async function fetchMonitorSettings(): Promise<MonitorSettings> {
  return jsonFetch<MonitorSettings>("/api/monitor");
}

export async function setMonitorSettings(patch: MonitorSettingsPatch): Promise<MonitorSettings> {
  return jsonSend<MonitorSettings>("/api/monitor", "PUT", patch);
}

export async function fetchScheduleRuns(limit = 50): Promise<ScheduleRun[]> {
  const data = await jsonFetch<ScheduleRunsResponse>(`/api/schedule_runs?limit=${limit}`);
  return data.runs ?? [];
}

export async function createSchedule(input: ScheduleInput): Promise<ScheduleRule> {
  return jsonSend<ScheduleRule>("/api/schedules", "POST", input);
}

export async function updateSchedule(id: string, input: ScheduleInput): Promise<ScheduleRule> {
  return jsonSend<ScheduleRule>(`/api/schedules/${encodeURIComponent(id)}`, "PUT", input);
}

export async function setScheduleEnabled(id: string, enabled: boolean): Promise<ScheduleRule> {
  return jsonSend<ScheduleRule>(
    `/api/schedules/${encodeURIComponent(id)}/enable`,
    "POST",
    { enabled },
  );
}

export async function deleteSchedule(id: string): Promise<void> {
  await jsonSend<{ deleted: boolean }>(`/api/schedules/${encodeURIComponent(id)}`, "DELETE");
}

export async function runSchedule(id: string): Promise<ScheduleRule> {
  return jsonSend<ScheduleRule>(`/api/schedules/${encodeURIComponent(id)}/run`, "POST");
}

export function setToken(token: string): void {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
  }
}

/**
 * Picks up `?token=` from the page URL (the desktop app's WKWebView passes
 * FLUXION_UI_TOKEN this way) and persists it to localStorage so every
 * subsequent /api/* call — including ones on later page loads — is
 * authenticated. No-op when the param is absent.
 */
export function initTokenFromLocation(): void {
  if (typeof window === "undefined") return;
  const fromUrl = new URLSearchParams(window.location.search).get("token");
  if (fromUrl) setToken(fromUrl);
}

export interface TaskEventSubscription {
  close: () => void;
}

interface SubscribeOptions {
  onEvent: (event: RawTaskEvent) => void;
  onError?: (err: Event) => void;
  onOpen?: () => void;
}

/**
 * Subscribe to the server's task lifecycle event stream.
 *
 * EventSource cannot set custom headers, so when a token is configured we
 * forward it as `?token=` instead — the FastAPI auth middleware accepts
 * both transport modes.
 */
export function subscribeToTaskEvents(opts: SubscribeOptions): TaskEventSubscription {
  const token = getToken();
  const url = token
    ? `/api/stream?token=${encodeURIComponent(token)}`
    : "/api/stream";
  const es = new EventSource(url);
  if (opts.onOpen) es.addEventListener("open", () => opts.onOpen?.());
  es.addEventListener("task.event", (raw) => {
    try {
      const parsed = JSON.parse((raw as MessageEvent).data) as RawTaskEvent;
      opts.onEvent(parsed);
    } catch {
      // Drop malformed frames; the next valid frame will resync state.
    }
  });
  if (opts.onError) {
    es.addEventListener("error", opts.onError);
  }
  return { close: () => es.close() };
}
