import { useEffect, useMemo, useRef, useState } from "react";
import * as React from "react";

import { useI18n } from "../../i18n";
import { channelLabel } from "../../lib/channels";
import type {
  AutoPingMode,
  AutoPingProviders,
  ChannelInfo,
  MonitorSettings,
  NotifyChannel,
  ProviderUsage,
  ScheduleInput,
  ScheduleRule,
  ScheduleRun,
} from "../../types";

import { describeCron, nextRuns, pad, relFuture, relPast } from "./cron";
import { SI } from "./icons";
import { NOTIFY_ORDER, windowFor } from "./QuotaMonitor";

function providerDisplayName(provider: string | null | undefined): string {
  if (provider === "claude") return "Claude";
  if (provider === "codex") return "Codex";
  if (provider === "antigravity") return "Antigravity";
  return provider ?? "";
}

function windowDisplayName(windowKey: string | null | undefined, t: (key: string) => string): string {
  if (!windowKey) return "";
  return t(windowKey === "5h" ? "schedules.fiveHourWindowShort" : "schedules.weeklyWindowShort");
}

export interface RowMenuProps {
  rule: ScheduleRule;
  onEdit: () => void;
  onRun: () => void;
  onToggle: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

export function RowMenu({ rule, onEdit, onRun, onToggle, onDuplicate, onDelete }: RowMenuProps): JSX.Element {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [alignUp, setAlignUp] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const close = () => {
    setOpen(false);
    setConfirm(false);
  };

  useEffect(() => {
    if (!open || !buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const menuHeight = confirm ? 120 : 220; // confirm dialog is shorter, normal menu is taller
    setAlignUp(spaceBelow < menuHeight);
  }, [open, confirm]);

  return (
    <div className="row-menu-wrap" onClick={(e) => e.stopPropagation()}>
      <button
        ref={buttonRef}
        type="button"
        className="iconbtn"
        data-active={open ? 1 : 0}
        title={t("schedules.more")}
        onClick={() => (open ? close() : setOpen(true))}
      >
        {SI.dots}
      </button>
      {open && (
        <React.Fragment>
          <div className="menu-backdrop" onClick={close}></div>
          <div className={`row-menu${alignUp ? " open-up" : ""}`} role="menu">
            {!confirm ? (
              <React.Fragment>
                <button
                  type="button"
                  className="mi"
                  onClick={() => {
                    onRun();
                    close();
                  }}
                >
                  {SI.play}
                  <span>{t("schedules.runNow")}</span>
                </button>
                <button
                  type="button"
                  className="mi"
                  onClick={() => {
                    onEdit();
                    close();
                  }}
                >
                  {SI.edit}
                  <span>{t("schedules.edit")}</span>
                </button>
                <button
                  type="button"
                  className="mi"
                  onClick={() => {
                    onToggle();
                    close();
                  }}
                >
                  {rule.enabled ? SI.pause : SI.play}
                  <span>{rule.enabled ? t("schedules.pause") : t("schedules.enable")}</span>
                </button>
                <button
                  type="button"
                  className="mi"
                  onClick={() => {
                    onDuplicate();
                    close();
                  }}
                >
                  {SI.copy}
                  <span>{t("schedules.duplicate")}</span>
                </button>
                <div className="mi-div"></div>
                <button
                  type="button"
                  className="mi danger"
                  onClick={() => setConfirm(true)}
                >
                  {SI.trash}
                  <span>{t("schedules.deleteEllipsis")}</span>
                </button>
              </React.Fragment>
            ) : (
              <div className="mi-confirm">
                <div className="mc-q">
                  {t("schedules.deleteInlineConfirm", { name: rule.name })}
                </div>
                <div className="mc-acts">
                  <button type="button" className="btn btn-sm" onClick={() => setConfirm(false)}>
                    {t("schedules.cancel")}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-danger"
                    onClick={() => {
                      onDelete();
                      close();
                    }}
                  >
                    {SI.trash} {t("schedules.delete")}
                  </button>
                </div>
              </div>
            )}
          </div>
        </React.Fragment>
      )}
    </div>
  );
}

export interface ScheduleRowProps {
  rule: ScheduleRule;
  lastRun?: ScheduleRun;
  now: Date;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onDuplicate: () => void;
  onRunNow: () => void;
}

export function ScheduleRow({
  rule,
  lastRun,
  now,
  onToggle,
  onEdit,
  onDelete,
  onDuplicate,
  onRunNow,
}: ScheduleRowProps): JSX.Element {
  const { t } = useI18n();
  const human = rule.trigger.type === "cron" ? describeCron(rule.trigger.cron, t) : null;
  const isCron = rule.trigger.type === "cron";
  const quotaProviderLabel = providerDisplayName(rule.trigger.provider);
  const quotaWindowLabel = windowDisplayName(rule.trigger.window_key, t);

  let nextRun: Date | null = null;
  if (rule.enabled) {
    if (isCron) {
      const runsList = nextRuns(rule.trigger.cron, now, 1);
      nextRun = runsList[0] || null;
    }
  }

  const visualMode = rule.action.mode === "workspace-write" ? "write" : rule.action.mode;

  return (
    <div className="sch-card" data-paused={rule.enabled ? 0 : 1}>
      <div className="sch-led"></div>
      <div className="sch-main">
        <div className="sch-namerow">
          <span className="sch-name">{rule.name || t("schedules.untitled")}</span>
          {!rule.enabled && <span className="sch-tag paused">{t("schedules.paused")}</span>}
          {rule.managed_by && <span className="sch-tag managed">{t("schedules.autoPing")}</span>}
        </div>
        <div className="sch-trigger">
          {isCron ? (
            <React.Fragment>
              <span className="tkind">cron</span>
              <span className="thuman">{human || t("schedules.customSchedule")}</span>
              <span className="tcron">
                {rule.trigger.cron} · {rule.trigger.timezone}
              </span>
            </React.Fragment>
          ) : (
            <React.Fragment>
              <span className="tkind quota">{t("schedules.quotaTag")}</span>
              <span className="thuman">
                {t("schedules.quotaRefreshPreview", { provider: quotaProviderLabel, window: quotaWindowLabel })}
              </span>
            </React.Fragment>
          )}
        </div>
        <div className="sch-action">
          <span className="ex" data-ex={rule.action.agent}>
            <span className="dot"></span>
            {rule.action.agent}
          </span>
          <span className="sep">·</span>
          <span>{visualMode}</span>
          {rule.action.prompt && (
            <React.Fragment>
              <span className="sep">·</span>
              <span className="prompt">{rule.action.prompt}</span>
            </React.Fragment>
          )}
        </div>
      </div>
      <div className="sch-side">
        <div className="sch-next">
          <div className="nlbl">{rule.enabled ? t("schedules.nextRun") : t("schedules.paused")}</div>
          <div className={"nval" + (rule.enabled ? "" : " paused")}>
            {rule.enabled ? (
              isCron && nextRun ? (
                t("schedules.inTime", { time: relFuture(nextRun, now) })
              ) : !isCron ? (
                t("schedules.onRefresh")
              ) : (
                "—"
              )
            ) : (
              "—"
            )}
          </div>
        </div>
        {lastRun ? (
          <div className="sch-last">
            <span className={"res " + (lastRun.error ? "fail" : "ok")}></span>
            {t("schedules.ran", { time: relPast(new Date(lastRun.fired_at), now) })}
          </div>
        ) : (
          <div className="sch-last">
            <span className="res"></span>
            {t("schedules.neverRan")}
          </div>
        )}
        {!rule.managed_by && (
          <div className="sch-ctl">
            <div
              className="toggle"
              data-on={rule.enabled ? 1 : 0}
              onClick={onToggle}
              role="switch"
              aria-checked={rule.enabled}
            />
            <button type="button" className="iconbtn" title={t("schedules.edit")} onClick={onEdit}>
              {SI.edit}
            </button>
            <button type="button" className="iconbtn" title={t("schedules.runNow")} onClick={onRunNow}>
              {SI.play}
            </button>
            <RowMenu
              rule={rule}
              onEdit={onEdit}
              onRun={onRunNow}
              onToggle={onToggle}
              onDuplicate={onDuplicate}
              onDelete={onDelete}
            />
          </div>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────── ManagedRow ────────────────────────────── */
export interface ManagedRowProps {
  rule: ScheduleRule;
  usage: ProviderUsage[];
  monitor: MonitorSettings;
  now: Date;
  onGoto: () => void;
}

export function ManagedRow({ rule, usage, monitor, now, onGoto }: ManagedRowProps): JSX.Element {
  const { t } = useI18n();
  const providerId = rule.trigger.provider as keyof AutoPingProviders;
  const providerLabel = providerDisplayName(providerId);
  const windowKey = (rule.trigger.window_key === "5h" ? "5h" : "7d") as "5h" | "7d";
  const windowLabel = windowDisplayName(windowKey, t);
  
  const w = windowFor(usage, providerId, windowKey);
  const nextFire = w && w.resets_at ? new Date(w.resets_at) : null;

  const connectedNotifyChannels = NOTIFY_ORDER.filter(
    (id) => monitor.notify[id] && monitor.channels[id]?.connected
  ).map((id) => channelLabel(id, t));
  const destLabel = connectedNotifyChannels.length > 0 ? connectedNotifyChannels.join(", ") : "—";

  return (
    <div className="sch-card managed">
      <div className="sch-led managed"></div>
      <div className="sch-main">
        <div className="sch-namerow">
          <span className="sch-name">{t("schedules.managedAutoPingTitle", { provider: providerLabel, window: windowLabel })}</span>
          <span className="sch-tag managed">{t("schedules.managedTag")}</span>
        </div>
        <div className="sch-trigger">
          <span className="tkind quota">{t("schedules.quotaTag")}</span>
          <span className="thuman">{t("schedules.managedRefreshes", { provider: providerLabel, window: windowLabel })}</span>
        </div>
        <div className="sch-action">
          <span className="ex" data-ex={providerId}>
            <span className="dot"></span>
            {providerId}
          </span>
          <span className="sep">·</span><span>{t("schedules.autoPing")}</span>
          <span className="sep">·</span>
          <span className="prompt">{t("schedules.keepAlive")} → {destLabel}</span>
        </div>
      </div>
      <div className="sch-side">
        <div className="sch-next">
          <div className="nlbl">{t("schedules.nextFire")}</div>
          <div className="nval">{nextFire ? t("schedules.inTime", { time: relFuture(nextFire, now) }) : "—"}</div>
        </div>
        <button type="button" className="managed-link" onClick={onGoto}>
          {t("schedules.manageInMonitor")} →
        </button>
      </div>
    </div>
  );
}

/* ───────────────────────── HistoryTab ────────────────────────────── */
export interface HistoryTabProps {
  runs: ScheduleRun[];
  now: Date;
}

export function HistoryTab({ runs, now }: HistoryTabProps): JSX.Element {
  const { locale, t } = useI18n();
  const byDay = useMemo(() => {
    const groups: Array<{ key: string; date: Date; rows: ScheduleRun[] }> = [];
    let cur: { key: string; date: Date; rows: ScheduleRun[] } | null = null;
    runs.forEach((r) => {
      const d = new Date(r.fired_at);
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      if (!cur || cur.key !== key) {
        cur = { key, date: d, rows: [] };
        groups.push(cur);
      }
      cur.rows.push(r);
    });
    return groups;
  }, [runs]);

  const dayLabel = (d: Date) => {
    const startOfDay = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
    const dayMs = 24 * 60 * 60 * 1000;
    const dayDelta = Math.round((startOfDay(now) - startOfDay(d)) / dayMs);
    const base = new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : locale === "ja" ? "ja-JP" : "en-US", {
      month: "short",
      day: "numeric",
    }).format(d);
    if (dayDelta === 0) return t("schedules.historyToday", { date: base });
    if (dayDelta === 1) return t("schedules.historyYesterday", { date: base });
    return base;
  };

  const runName = (r: ScheduleRun) => {
    const raw = r.name || r.schedule_id;
    const match = raw.match(/^Auto Ping anchor \(([^)]+)\)$/i);
    if (!match) return raw;
    const provider = match[1] ? match[1][0]!.toUpperCase() + match[1]!.slice(1) : "";
    return t("schedules.historyAutoPingWarmup", { provider });
  };

  const triggerReason = (reason: string) => {
    const match = reason.match(/^anchoring burst \(event ([^)]+)\)$/i);
    if (!match) return reason;
    return t("schedules.historyAnchoringBurst", { event: match[1] ?? "" });
  };

  return (
    <div className="hist">
      {byDay.map((g) => {
        const ok = g.rows.filter((r) => r.accepted && !r.error).length;
        const fail = g.rows.filter((r) => !!r.error).length;
        const skip = g.rows.filter((r) => !r.accepted).length;
        return (
          <React.Fragment key={g.key}>
            <div className="hist-day">
              <span>{dayLabel(g.date)}</span>
              <span className="agg">
                <span className="ok">{t("schedules.historyOk", { count: ok })}</span>
                {fail > 0 && <span className="fail"> · {t("schedules.historyFailed", { count: fail })}</span>}
                {skip > 0 && <span> · {t("schedules.historySkipped", { count: skip })}</span>}
              </span>
            </div>
            {g.rows.map((r, i) => {
              const firedDate = new Date(r.fired_at);
              const statusClass = r.error ? "fail" : !r.accepted ? "skip" : "ok";
              return (
                <div className="hist-row" key={`${r.run_id}-${i}`}>
                  <span className={"hist-res " + statusClass}></span>
                  <span className="hist-time">
                    {pad(firedDate.getHours())}:{pad(firedDate.getMinutes())}
                  </span>
                  <div className="hist-name">
                    <div className="nm">{runName(r)}</div>
                    <div className="hist-reason">
                      <span className="rk" title={r.trigger_reason}>{triggerReason(r.trigger_reason)}</span>
                    </div>
                  </div>
                  <span className="hist-dur">—</span>
                  <span className="hist-link">{t("schedules.task")}</span>
                </div>
              );
            })}
          </React.Fragment>
        );
      })}
    </div>
  );
}

/* ───────────────────────── ScheduleForm ──────────────────────────── */
