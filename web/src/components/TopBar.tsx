import { useMemo, type ChangeEvent } from "react";

import { STATUSES } from "../lib/constants";
import { useI18n } from "../i18n";
import type { Task } from "../types";

export type AppView = "tasks" | "stats";

interface TopBarProps {
  tasks: Task[];
  search: string;
  setSearch: (v: string) => void;
  spark: number[];
  view: AppView;
  setView: (v: AppView) => void;
  onOpenSchedules: () => void;
  onRunTask: () => void;
  schedulesActive: number;
}

export function TopBar({
  tasks,
  search,
  setSearch,
  spark,
  view,
  setView,
  onOpenSchedules,
  onRunTask,
  schedulesActive,
}: TopBarProps): JSX.Element {
  const { t } = useI18n();
  const counts = useMemo(() => {
    const acc: Record<string, number> = Object.fromEntries(STATUSES.map((s) => [s, 0]));
    for (const t of tasks) {
      acc[t.status] = (acc[t.status] ?? 0) + 1;
    }
    return acc;
  }, [tasks]);

  const running = counts.RUNNING ?? 0;
  const retrying = counts.RETRYING ?? 0;
  const queued = (counts.QUEUED ?? 0) + (counts.VALIDATED ?? 0) + (counts.RECEIVED ?? 0);
  const failed = counts.FAILED ?? 0;
  const active = running + retrying;

  const sparkBars = spark.slice(-24);
  const maxSpark = Math.max(1, ...sparkBars);

  return (
    <div className="topbar">
      <div className="brand">
        <img className="brand-mark" src="/fluxion-logo.svg" alt="" aria-hidden="true" />
        Fluxion
      </div>

      <div className="tb-views" role="tablist">
        <button
          className={`tb-view ${view === "tasks" ? "on" : ""}`}
          onClick={() => setView("tasks")}
          role="tab"
          aria-selected={view === "tasks"}
        >
          {t("topbar.tasks")}
        </button>
        <button
          className={`tb-view ${view === "stats" ? "on" : ""}`}
          onClick={() => setView("stats")}
          role="tab"
          aria-selected={view === "stats"}
        >
          {t("topbar.stats")}
        </button>
      </div>

      <div className="tb-stat tb-stat-active">
        <div className="tb-stat-lbl">{t("topbar.active")}</div>
        <div className="tb-stat-val">
          {active}
          <em>/{tasks.length}</em>
        </div>
      </div>
      <div className="tb-stat tb-stat-queued">
        <div className="tb-stat-lbl">{t("topbar.queued")}</div>
        <div className="tb-stat-val">{queued}</div>
      </div>
      <div className="tb-stat tb-stat-failed">
        <div className="tb-stat-lbl">{t("topbar.failed24h")}</div>
        <div className="tb-stat-val">{failed}</div>
      </div>

      <div className="tb-stat tb-stat-spark" style={{ borderRight: 0 }}>
        <div className="tb-stat-lbl">{t("topbar.inbound24m")}</div>
        <div className="tb-spark" aria-label={t("topbar.inboundSparkline")}>
          {sparkBars.map((v, i) => (
            <i
              key={i}
              style={{
                height: `${4 + (v / maxSpark) * 22}px`,
                opacity: i === sparkBars.length - 1 ? 1 : 0.4 + 0.5 * (i / sparkBars.length),
              }}
            />
          ))}
        </div>
      </div>

      <div className="tb-spacer" />

      <button
        type="button"
        className="tb-run"
        onClick={onRunTask}
        title={t("topbar.runTaskTitle")}
      >
        <svg viewBox="0 0 16 16" fill="none">
          <path d="M5 3.5l7 4.5-7 4.5z" fill="currentColor" />
        </svg>
        <span>{t("topbar.runTask")}</span>
      </button>

      <button
        type="button"
        className="tb-sched"
        onClick={onOpenSchedules}
        title={t("topbar.schedulesTitle")}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.6" />
          <path d="M12 7.5V12l3 2" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>{t("topbar.schedules")}</span>
        {schedulesActive > 0 && <span className="tb-sched-badge">{schedulesActive}</span>}
      </button>

      <div className="tb-bus" title={t("topbar.gatewayConnection")}>
        <span className="dot" />
        {t("topbar.busAlive")}
      </div>

      <div className="tb-search">
        <svg width="13" height="13" viewBox="0 0 14 14" fill="none" style={{ color: "var(--fg-3)" }}>
          <circle cx="6" cy="6" r="4.2" stroke="currentColor" strokeWidth="1.2" />
          <path d="M9.5 9.5l2.5 2.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
        </svg>
        <input
          value={search}
          onChange={(e: ChangeEvent<HTMLInputElement>) => setSearch(e.target.value)}
          placeholder={t("topbar.searchPlaceholder")}
          spellCheck={false}
        />
        <kbd>⌘K</kbd>
      </div>
    </div>
  );
}
