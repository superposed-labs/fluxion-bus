import { useEffect, useMemo, useRef, useState } from "react";

import { fetchExecutors, fetchTask, fetchTasks, fetchUsage, runTask, subscribeToTaskEvents } from "./api";
import { createI18n, I18nProvider, languageName, type LocalePreference } from "./i18n";
import { applyEvent } from "./lib/applyEvent";
import { EXECUTORS, statusPriority } from "./lib/constants";
import { ACCENT_OPTIONS, applyTweaks, useTweaks } from "./lib/useTweaks";
import {
  selectedTaskFromSearch,
  useFilters,
  useSelectedTask,
  useView,
} from "./lib/urlState";
import type { ExecutorInfo, ProviderUsage, Task } from "./types";

import { BusFoot } from "./components/BusFoot";
import { ConversationGroup, type ConversationTaskGroup } from "./components/ConversationGroup";
import { DetailPane } from "./components/Detail";
import { FilterStrip } from "./components/FilterStrip";
import { ExecutorRail, RecentChannels, SessionRail } from "./components/Rails";
import { RunTaskDrawer, type RunTaskPrefill } from "./components/RunTaskDrawer";
import { SchedulesPanel } from "./components/Schedules";
import { TaskRow } from "./components/TaskRow";
import { TopBar } from "./components/TopBar";
import { UsageRail } from "./components/UsageRail";
import { UsageStats } from "./components/UsageStats";
import {
  TweakColor,
  TweakRadio,
  TweakSection,
  TweakToggle,
  TweaksPanel,
} from "./components/Tweaks";

// Fallback polling interval — only used while the SSE connection is down.
// While the stream is live, updates arrive in <1s and this timer stays idle.
const FALLBACK_POLL_INTERVAL_MS = 15000;
const SPARK_TICK_MS = 1400;
const NOW_TICK_MS = 1000;
// Provider quota refresh. Independent of the SSE task stream; the backend
// TTL-caches results so this poll is cheap.
const USAGE_POLL_INTERVAL_MS = 60000;
const INITIAL_SPARK = Array.from({ length: 24 }, (_, i) =>
  3 + Math.round(6 * Math.abs(Math.sin(i * 0.7 + 1))) + (i > 18 ? 2 : 0),
);

interface LoadState {
  status: "idle" | "loading" | "ready" | "error";
  error?: string;
}

function matchesQuery(task: Task, q: string): boolean {
  if (!q) return true;
  if (task.summary.toLowerCase().includes(q)) return true;
  if (task.task_id.toLowerCase().includes(q)) return true;
  if (task.conversation_key.toLowerCase().includes(q)) return true;
  if ((task.executor_session_id ?? "").toLowerCase().includes(q)) return true;
  if (task.changed_files.some((f) => f.path.toLowerCase().includes(q))) return true;
  return false;
}

const TWO_HOURS_MS = 2 * 3600 * 1000;
const ACTIVE_STATUSES = new Set(["RUNNING", "RETRYING", "QUEUED", "VALIDATED", "RECEIVED"]);

function sortTimestamp(task: Task): string {
  return (
    task.timestamp.ended_at ??
    task.timestamp.started_at ??
    task.timestamp.queued_at ??
    task.timestamp.received_at ??
    ""
  );
}

function compareTasks(a: Task, b: Task): number {
  const aActive = ACTIVE_STATUSES.has(a.status);
  const bActive = ACTIVE_STATUSES.has(b.status);
  if (aActive !== bActive) return aActive ? -1 : 1;

  if (aActive && bActive) {
    const pa = statusPriority(a.status);
    const pb = statusPriority(b.status);
    if (pa !== pb) return pa - pb;
  }

  return sortTimestamp(b).localeCompare(sortTimestamp(a));
}

function groupTasksByConversation(tasks: Task[]): ConversationTaskGroup[] {
  const grouped = new Map<string, Task[]>();
  for (const task of tasks) {
    const key = task.conversation_key || task.task_id;
    const items = grouped.get(key);
    if (items) {
      items.push(task);
    } else {
      grouped.set(key, [task]);
    }
  }

  return Array.from(grouped.entries())
    .map(([key, items]) => {
      const sorted = [...items].sort(compareTasks);
      const representative = sorted[0]!;
      const executors = Array.from(new Set(sorted.map((task) => task.executor)));
      return {
        key,
        tasks: sorted,
        status: representative.status,
        executors,
        latestAt: sortTimestamp(representative),
        additions: sorted.reduce((sum, task) => sum + (task.diff_summary.additions || 0), 0),
        deletions: sorted.reduce((sum, task) => sum + (task.diff_summary.deletions || 0), 0),
        liveCount: sorted.filter(
          (task) => task.status === "RUNNING" || task.status === "RETRYING",
        ).length,
      };
    })
    .sort((a, b) => compareTasks(a.tasks[0]!, b.tasks[0]!));
}

function sanitizeTask(t: Task, timeoutSummary: string): Task {
  const isActive =
    t.status === "RECEIVED" ||
    t.status === "VALIDATED" ||
    t.status === "QUEUED" ||
    t.status === "RUNNING" ||
    t.status === "RETRYING";

  if (!isActive) return t;

  const timeStr = t.timestamp.started_at || t.timestamp.received_at;
  if (!timeStr) return t;

  const ageMs = Date.now() - new Date(timeStr).getTime();
  if (ageMs > TWO_HOURS_MS) {
    return {
      ...t,
      status: "FAILED",
      summary: t.summary || timeoutSummary,
      timestamp: {
        ...t.timestamp,
        ended_at: t.timestamp.started_at || t.timestamp.received_at,
      },
    };
  }

  return t;
}

export function App(): JSX.Element {
  const [tweaks, setTweak] = useTweaks();
  const i18n = useMemo(() => createI18n(tweaks.locale), [tweaks.locale]);
  const { t } = i18n;
  useEffect(() => {
    applyTweaks(tweaks);
  }, [tweaks]);

  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useSelectedTask();
  const [load, setLoad] = useState<LoadState>({ status: "idle" });
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useFilters();
  const [expandedConversations, setExpandedConversations] = useState<Set<string>>(
    () => new Set(),
  );
  const [spark, setSpark] = useState<number[]>(INITIAL_SPARK);
  const [usage, setUsage] = useState<ProviderUsage[]>([]);
  const [executors, setExecutors] = useState<ExecutorInfo[]>([]);
  // View, filters and selection all live in the URL — see lib/urlState.ts.
  const [view, setView] = useView();
  const [schedulesOpen, setSchedulesOpen] = useState(false);
  const [runPrefill, setRunPrefill] = useState<RunTaskPrefill | null>(null);
  const [schedulesActive, setSchedulesActive] = useState(0);
  // Bump every second so RUNNING durations re-render.
  const [nowTick, setNowTick] = useState(0);

  // SSE connection health — drives the fallback poll loop. When the stream
  // is live, the fallback timer is parked at FALLBACK_POLL_INTERVAL_MS so
  // it acts purely as a safety net.
  const streamLiveRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const hydrate = async () => {
      try {
        const next = await fetchTasks();
        if (cancelled) return;
        setTasks((prev) =>
          next.map((task) => {
            const existing = prev.find((item) => item.task_id === task.task_id);
            if (!existing?.diff_hunks) return task;
            return { ...task, diff_hunks: existing.diff_hunks };
          }),
        );
        setLoad({ status: "ready" });
        // Keep whatever the URL points at as long as it still exists — a
        // shared `?task=` link, or the user's own selection across the 15s
        // fallback poll. Only fall back to the newest task otherwise.
        const current = selectedTaskFromSearch(window.location.search);
        if (!current || !next.some((t) => t.task_id === current)) {
          setSelectedId(next[0]?.task_id ?? null);
        }
      } catch (err) {
        if (cancelled) return;
        setLoad({
          status: "error",
          error: err instanceof Error ? err.message : String(err),
        });
      }
    };

    setLoad({ status: "loading" });
    void hydrate();

    const subscription = subscribeToTaskEvents({
      onOpen: () => {
        streamLiveRef.current = true;
      },
      onEvent: (event) => {
        setTasks((prev) => applyEvent(prev, event));
      },
      onError: () => {
        // EventSource auto-reconnects on its own; we just flip the flag so
        // the fallback poll covers the gap.
        streamLiveRef.current = false;
      },
    });

    // Safety-net poll: if SSE is dead (proxy stripped it, server crashed,
    // etc.) we still see fresh data every 15s instead of going stale.
    const fallbackId = window.setInterval(() => {
      if (!streamLiveRef.current) void hydrate();
    }, FALLBACK_POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      subscription.close();
      window.clearInterval(fallbackId);
    };
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => {
      setSpark((prev) => {
        const last = prev[prev.length - 1] ?? 6;
        const next = Math.max(2, Math.min(14, last + (Math.random() - 0.5) * 4));
        return [...prev.slice(1), Math.round(next)];
      });
    }, SPARK_TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => setNowTick((n) => (n + 1) % 1000), NOW_TICK_MS);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await fetchExecutors();
        if (!cancelled) setExecutors(next);
      } catch {
        if (!cancelled) setExecutors([]);
      }
    };
    void refresh();
    const id = window.setInterval(() => void refresh(), USAGE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!tweaks.showQuota) {
      setUsage([]);
      return;
    }
    let cancelled = false;
    const refresh = async () => {
      try {
        const next = await fetchUsage();
        if (!cancelled) setUsage(next);
      } catch {
        // Leave the last snapshot in place; the panel hides on persistent failure.
      }
    };
    void refresh();
    const id = window.setInterval(() => void refresh(), USAGE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [tweaks.showQuota]);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    const hydrateDetail = async () => {
      try {
        const detail = await fetchTask(selectedId);
        if (cancelled) return;
        setTasks((prev) => {
          const idx = prev.findIndex((task) => task.task_id === detail.task_id);
          if (idx < 0) return prev;
          const next = [...prev];
          next[idx] = detail;
          return next;
        });
      } catch {
        // Keep the list snapshot; the fallback poll will recover transient misses.
      }
    };
    void hydrateDetail();
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const sanitizedTasks = useMemo(() => {
    return tasks.map((task) => sanitizeTask(task, t("app.taskTimedOut")));
  }, [tasks, nowTick, t]);

  const railExecutors = useMemo(() => {
    const names = new Set<string>();
    executors.forEach((executor) => {
      // Only surface executors that are actually installed; an enabled-but-
      // uninstalled one (status !== "available") would just fail if picked.
      if (executor.name && executor.status === "available") names.add(executor.name);
    });
    sanitizedTasks.forEach((task) => {
      if (ACTIVE_STATUSES.has(task.status) && task.executor) names.add(task.executor);
    });
    if (names.size === 0) EXECUTORS.forEach((executor) => names.add(executor));
    return Array.from(names);
  }, [executors, sanitizedTasks]);

  const enabledExecutorNames = useMemo(
    () =>
      executors
        .filter((executor) => executor.status === "available")
        .map((executor) => executor.name)
        .filter(Boolean),
    [executors],
  );

  const visibleTasks = useMemo(() => {
    const q = search.trim().toLowerCase();
    return sanitizedTasks
      .filter((t) => filters.status.length === 0 || filters.status.includes(t.status))
      .filter((t) => filters.executor.length === 0 || filters.executor.includes(t.executor))
      .filter((t) => filters.channel.length === 0 || filters.channel.includes(t.channel))
      .filter((t) => matchesQuery(t, q))
      .sort(compareTasks);
  }, [sanitizedTasks, filters, search]);

  const selectedTask = useMemo(
    () =>
      visibleTasks.find((t) => t.task_id === selectedId) ??
      sanitizedTasks.find((t) => t.task_id === selectedId) ??
      null,
    [visibleTasks, sanitizedTasks, selectedId],
  );

  const openRunTask = () => setRunPrefill({ mode: "new" });
  const openContinue = (task: Task) => setRunPrefill({ mode: "continue", task });

  const conversationGroups = useMemo(
    () => groupTasksByConversation(visibleTasks),
    [visibleTasks],
  );

  useEffect(() => {
    if (!tweaks.groupByConversation || !selectedTask?.conversation_key) return;
    setExpandedConversations((prev) => {
      if (prev.has(selectedTask.conversation_key)) return prev;
      const next = new Set(prev);
      next.add(selectedTask.conversation_key);
      return next;
    });
  }, [selectedTask?.conversation_key, tweaks.groupByConversation]);

  const toggleConversation = (key: string) => {
    setExpandedConversations((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField = target?.tagName === "INPUT" || target?.tagName === "TEXTAREA";

      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const input = document.querySelector<HTMLInputElement>(".tb-search input");
        input?.focus();
        input?.select();
        return;
      }

      if (inField) {
        if (e.key === "Escape") (target as HTMLInputElement).blur();
        return;
      }

      const idx = visibleTasks.findIndex((t) => t.task_id === selectedId);
      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        const next = visibleTasks[Math.min(visibleTasks.length - 1, idx + 1)];
        if (next) setSelectedId(next.task_id);
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        const next = visibleTasks[Math.max(0, idx - 1)];
        if (next) setSelectedId(next.task_id);
      } else if (e.key === "Escape") {
        setSearch("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visibleTasks, selectedId]);

  return (
    <I18nProvider preference={tweaks.locale}>
    <div className="shell">
      <TopBar
        tasks={sanitizedTasks}
        search={search}
        setSearch={setSearch}
        spark={spark}
        view={view}
        setView={setView}
        onOpenSchedules={() => setSchedulesOpen(true)}
        onRunTask={openRunTask}
        schedulesActive={schedulesActive}
      />

      {view === "stats" ? (
        <UsageStats billing={tweaks.billing} setTweak={setTweak} />
      ) : (
      <div className="shell-cols">
        <div className="pane">
          <FilterStrip
            tasks={sanitizedTasks}
            filters={filters}
            setFilters={setFilters}
            grouped={tweaks.groupByConversation}
            onToggleGroup={() => setTweak("groupByConversation", !tweaks.groupByConversation)}
          />
          <div className="tasklist scroll">
            {load.status === "error" && (
              <div className="empty" style={{ color: "var(--st-failed)" }}>
                {t("app.apiError")}
                <div className="mark">{load.error}</div>
              </div>
            )}
            {load.status !== "error" && visibleTasks.length === 0 && (
              <div className="empty">
                {sanitizedTasks.length === 0 ? t("app.empty.noTasks") : t("app.empty.noMatch")}
                <div className="mark">
                  {sanitizedTasks.length === 0
                    ? t("app.empty.noTasksHint")
                    : t("app.empty.noMatchHint")}
                </div>
              </div>
            )}
            {tweaks.groupByConversation
              ? conversationGroups.map((group) => (
                  <ConversationGroup
                    key={group.key}
                    group={group}
                    expanded={expandedConversations.has(group.key)}
                    selectedId={selectedId}
                    onToggle={toggleConversation}
                    onSelect={setSelectedId}
                  />
                ))
              : visibleTasks.map((task) => (
                  <TaskRow
                    key={task.task_id}
                    task={task}
                    selected={task.task_id === selectedId}
                    onSelect={setSelectedId}
                  />
                ))}
          </div>
          <BusFoot tasks={sanitizedTasks} executorCount={railExecutors.length} />
        </div>

        <DetailPane task={selectedTask} onContinue={openContinue} />

        {tweaks.sideRail && (
          <div className="pane pane-side scroll" style={{ overflowY: "auto" }}>
            <ExecutorRail
              tasks={sanitizedTasks}
              usage={usage}
              showQuota={tweaks.showQuota}
              executors={railExecutors}
            />
            <SessionRail
              task={selectedTask}
              allTasks={sanitizedTasks}
              onSelect={setSelectedId}
              onContinue={openContinue}
            />
            <RecentChannels tasks={sanitizedTasks} />
          </div>
        )}
      </div>
      )}

      <TweaksPanel title={t("tweaks.title")}>
        <TweakSection label={t("tweaks.language")} />
        <TweakRadio
          label={t("tweaks.displayLanguage")}
          value={tweaks.locale}
          options={["auto", "en", "zh", "ja"] as readonly LocalePreference[]}
          formatOption={(v) => v === "auto" ? t("tweaks.followSystem") : languageName(v)}
          onChange={(v) => setTweak("locale", v)}
        />
        <TweakSection label={t("tweaks.density")} />
        <TweakRadio
          label={t("tweaks.rowDensity")}
          value={tweaks.density}
          options={["compact", "regular", "comfy"] as const}
          formatOption={(v) => t(`tweaks.${v}`)}
          onChange={(v) => setTweak("density", v)}
        />
        <TweakSection label={t("tweaks.theme")} />
        <TweakRadio
          label={t("tweaks.mode")}
          value={tweaks.mode}
          options={["dark", "light"] as const}
          formatOption={(v) => t(`tweaks.${v}`)}
          onChange={(v) => setTweak("mode", v)}
        />
        <TweakColor
          label={t("tweaks.accent")}
          value={tweaks.accent}
          options={ACCENT_OPTIONS}
          onChange={(v) => setTweak("accent", v)}
        />
        <TweakSection label={t("tweaks.layout")} />
        <TweakToggle
          label={t("tweaks.rightRail")}
          value={tweaks.sideRail}
          onChange={(v) => setTweak("sideRail", v)}
        />
        <TweakToggle
          label={t("tweaks.groupByConversation")}
          value={tweaks.groupByConversation}
          onChange={(v) => setTweak("groupByConversation", v)}
        />
        <TweakSection label={t("tweaks.quotaDisplay")} />
        <TweakToggle
          label={t("tweaks.modelQuota")}
          value={tweaks.showQuota}
          onChange={(v) => setTweak("showQuota", v)}
        />
      </TweaksPanel>

      <SchedulesPanel
        open={schedulesOpen}
        onOpenChange={setSchedulesOpen}
        onActiveCountChange={setSchedulesActive}
      />
      {runPrefill && (
        <RunTaskDrawer
          prefill={runPrefill}
          onClose={() => setRunPrefill(null)}
          onSubmit={runTask}
          onAccepted={setSelectedId}
          executors={enabledExecutorNames}
        />
      )}
    </div>
    </I18nProvider>
  );
}
