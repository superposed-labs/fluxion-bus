export type TaskStatus =
  | "RECEIVED"
  | "VALIDATED"
  | "QUEUED"
  | "RUNNING"
  | "RETRYING"
  | "RETURNED"
  | "FAILED"
  | "CANCELED";

export type ExecutorName = "codex" | "claude" | "antigravity";

export type ChannelName =
  | "slack"
  | "local"
  | "wecom"
  | "wechat"
  | "telegram"
  | "line"
  | "qqbot"
  | "feishu"
  | "web";

export interface SlackChannelMeta {
  workspace: string;
  channel: string;
  user: string;
}

export interface LocalChannelMeta {
  host: string;
  cwd: string;
}

export type ChannelMeta = SlackChannelMeta | LocalChannelMeta | Record<string, string>;

export interface TaskTimestamps {
  received_at: string | null;
  validated_at: string | null;
  queued_at: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface ChangedFile {
  op: string;
  path: string;
  additions: number;
  deletions: number;
}

export interface DiffSummary {
  files: number;
  additions: number;
  deletions: number;
}

export interface Artifact {
  name: string;
  kind: string;
  size: string;
  path: string;
}

export interface LogLine {
  t: number;
  lvl: string;
  body: string;
}

export type DiffHunkLineKind = "hunk" | "add" | "del" | "ctx";

export interface DiffHunkLine {
  type: DiffHunkLineKind;
  n1?: number | string;
  n2?: number | string;
  text: string;
}

export type DiffHunks = Record<string, DiffHunkLine[]>;

export interface Task {
  task_id: string;
  executor: ExecutorName | string;
  model: string;
  subagent?: {
    agent?: string;
    thread?: string;
    task_name?: string;
    parent_path?: string;
    agent_path?: string;
    profile?: string;
    mode?: string;
    session_policy?: string;
  };
  channel: ChannelName | string;
  channel_meta: ChannelMeta;
  conversation_key: string;
  status: TaskStatus | string;
  summary: string;
  executor_session_id: string | null;
  log_file: string;
  timestamp: TaskTimestamps;
  changed_files: ChangedFile[];
  diff_summary: DiffSummary;
  artifacts: Artifact[];
  stdout: LogLine[];
  stderr: LogLine[];
  // Optional fields — not currently produced by the backend, kept so future
  // executors can attach richer detail without UI changes.
  exit_code?: number | null;
  attempt?: number;
  max_attempts?: number;
  diff_hunks?: DiffHunks;
}

export interface TasksResponse {
  tasks: Task[];
}

export type RunTaskAgent = "auto" | "codex" | "claude" | "antigravity";
export type RunTaskProfile = "inspect" | "implement" | "verify" | "summarize";
export type RunTaskMode = "read-only" | "workspace-write";
export type RunTaskSessionPolicy = "auto" | "continue" | "new";

export interface RunTaskInput {
  prompt: string;
  agent: RunTaskAgent | string;
  project?: string;
  workspace: string;
  thread: string;
  task_name?: string;
  parent_path?: string;
  profile: RunTaskProfile | string;
  mode: RunTaskMode | string;
  session_policy: RunTaskSessionPolicy | string;
  conversation_key?: string;
  model?: string;
}

export interface RunTaskResponse {
  run_id: string;
  task_id: string;
  agent: string;
  project: string;
  workspace: string;
  thread: string;
  conversation_key: string;
  accepted: boolean;
  status: string;
  summary: string;
  timed_out?: boolean;
  wait_for_result?: boolean;
}

export interface ExecutorInfo {
  name: ExecutorName | string;
  status: string;
  detail: string;
  path: string;
  default: boolean;
  usage_enabled: boolean;
}

export interface ExecutorsResponse {
  generated_at: string;
  default_executor: string;
  enabled_executors: string[];
  usage_providers: string[];
  executors: ExecutorInfo[];
}

export type UsageStatus = "ok" | "unavailable" | "error";

export interface UsageWindow {
  key: string; // "5h" | "7d" | "prompt" | "flow"
  label: string; // "5-hour" | "Weekly" | "Prompt credits"
  used_percent: number | null;
  resets_at: string | null;
  window_minutes: number | null;
  // Credit-style providers (Antigravity): absolute balance instead of a clock.
  remaining: number | null;
  total: number | null;
}

export interface ResetCredits {
  count: number;
  expiries: number[];
}

export interface ProviderUsage {
  provider: string; // "claude" | "codex" | "antigravity"
  status: UsageStatus | string;
  account_label: string;
  plan_monthly_usd?: number | null; // official monthly price of the detected plan
  windows: UsageWindow[];
  fetched_at: string;
  detail: string;
  resets?: ResetCredits | null;
}

export interface UsageResponse {
  enabled: boolean;
  providers: ProviderUsage[];
  generated_at: string;
}

// ── Token-usage analytics (local transcript history) ───────────────
export type UsageWindowKey = "1d" | "7d" | "30d" | "all";

export interface UsageTokenBreakdown {
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
  generated_tokens: number; // input + output + cache_creation (excludes cache reads)
}

export interface ContextTierBreakdown {
  short: number;
  long: number;
}

export interface UsageHistoryTotals extends UsageTokenBreakdown {
  sessions: number;
  messages: number;
  active_days: number;
  span_days: number;
  current_streak: number;
  longest_streak: number;
  peak_hour: number | null;
  top_model: string | null;
  cache_hit: number; // 0..1
  cost: number; // estimated USD
  uncosted_tokens: number; // tokens on models with no known rate (local/unrecognised)
  context_tier_breakdown: ContextTierBreakdown;
  first_day: string | null;
  last_day: string | null;
}

export interface UsageDayStat {
  date: string; // YYYY-MM-DD
  messages: number;
  total_tokens: number;
}

export interface UsageHourStat {
  hour: number; // 0..23
  messages: number;
  total_tokens: number;
}

export interface UsageModelStat extends UsageTokenBreakdown {
  model: string;
  provider: string;
  messages: number;
  sessions: number;
  cost: number; // estimated USD
  context_tier_breakdown: ContextTierBreakdown;
}

export interface CodexReconciliation {
  status: "ok" | "unavailable" | string;
  local_tokens?: number;
  server_tokens?: number;
  unclassified_tokens?: number;
  excess_local_tokens?: number;
  coverage?: number | null;
  fetched_at?: string;
}

export interface UsageHistory {
  enabled: boolean;
  locale?: string;
  window: UsageWindowKey | string;
  providers: string[];
  generated_at: string;
  prices_updated_at?: string | null; // date the price table was last refreshed
  totals: UsageHistoryTotals;
  by_day: UsageDayStat[];
  by_hour: UsageHourStat[];
  by_model: UsageModelStat[];
  codex_reconciliation?: CodexReconciliation;
}

export type TriggerType = "cron" | "quota_refresh";
export type ActionType = "subagent" | "ping";
export type CatchUp = "skip" | "run_once";

export interface ScheduleTrigger {
  type: TriggerType | string;
  cron: string;
  timezone: string;
  provider: string;
  window_key: string;
}

export interface ScheduleAction {
  type: ActionType | string;
  agent: string;
  prompt: string;
  project: string | null;
  workspace: string;
  profile: string;
  mode: string;
  thread: string;
  task_name: string | null;
}

export interface SchedulePolicy {
  cooldown_sec: number;
  catch_up: CatchUp | string;
  max_runs_per_day: number;
  jitter_sec: number;
}

export interface ScheduleRule {
  id: string;
  name: string;
  enabled: boolean;
  trigger: ScheduleTrigger;
  action: ScheduleAction;
  policy: SchedulePolicy;
  managed_by: string | null;
  created_at: string;
  updated_at: string;
}

export type AutoPingMode = "off" | "5h" | "7d" | "both";
export type AutoPingProviders = Record<"claude" | "codex" | "antigravity", AutoPingMode>;

export type NotifyChannel = "slack" | "telegram" | "qqbot" | "feishu" | "wechat" | "line";
export type NotifyChannels = Record<NotifyChannel, boolean>;

export interface ChannelInfo {
  label: string;
  connected: boolean;
  target: string;
}

// Global "on reset, do" actions for the Quota Monitor, shared with the macOS
// app. The per-provider watch scope lives in AutoPingProviders above.
export interface MonitorSettings {
  auto_ping: boolean;
  notify_credit_grant: boolean;
  notify_credit_expiry: boolean;
  notify: NotifyChannels;
  channels: Record<NotifyChannel, ChannelInfo>;
  // "macos" when this gateway runs on macOS (the companion desktop app exists).
  host_os: "macos" | "other";
}

// Partial update — only the fields you want to change.
export interface MonitorSettingsPatch {
  auto_ping?: boolean;
  notify_credit_grant?: boolean;
  notify_credit_expiry?: boolean;
  notify?: Partial<NotifyChannels>;
}

// Create/update payload — no server-owned id/timestamps.
export interface ScheduleInput {
  name: string;
  enabled: boolean;
  trigger: ScheduleTrigger;
  action: ScheduleAction;
  policy: SchedulePolicy;
}

export interface ScheduleRun {
  schedule_id: string;
  name: string;
  fired_at: string;
  trigger_reason: string;
  action_type: string;
  agent: string;
  task_id: string;
  run_id: string;
  accepted: boolean;
  error: string;
}

export interface SchedulesResponse {
  schedules: ScheduleRule[];
}

export interface ScheduleRunsResponse {
  runs: ScheduleRun[];
}
