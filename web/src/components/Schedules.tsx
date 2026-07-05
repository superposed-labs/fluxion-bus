import { useCallback, useEffect, useState } from "react";
import * as React from "react";

import { useI18n } from "../i18n";
import {
  createSchedule,
  deleteSchedule,
  fetchAutoPing,
  fetchMonitorSettings,
  fetchScheduleRuns,
  fetchSchedules,
  fetchUsage,
  setScheduleEnabled,
  setAutoPing,
  setMonitorSettings,
  updateSchedule,
  runSchedule,
} from "../api";
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
} from "../types";
import { SI } from "./schedules/icons";
import { AutoPingConfirm, QuotaMonitorPanel, RUN_HISTORY_LIMIT } from "./schedules/QuotaMonitor";
import { HistoryTab, ManagedRow, ScheduleRow } from "./schedules/rows";
import { ScheduleForm } from "./schedules/ScheduleForm";

function defaultTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function blankInput(): ScheduleInput {
  return {
    name: "",
    enabled: true,
    trigger: { type: "cron", cron: "0 9 * * 1", timezone: defaultTimezone(), provider: "codex", window_key: "7d" },
    action: {
      type: "subagent",
      agent: "auto",
      prompt: "",
      project: null,
      workspace: ".",
      profile: "inspect",
      mode: "read-only",
      thread: "scheduler",
      task_name: null,
    },
    policy: { cooldown_sec: 3600, catch_up: "skip", max_runs_per_day: 24, jitter_sec: 0 },
  };
}

function toInput(rule: ScheduleRule): ScheduleInput {
  return {
    name: rule.name,
    enabled: rule.enabled,
    trigger: { ...rule.trigger },
    action: { ...rule.action },
    policy: { ...rule.policy },
  };
}

/* ───────────────────────── SchedulesPanel ─────────────────────────── */
interface SchedulesPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onActiveCountChange: (count: number) => void;
}

export function SchedulesPanel({
  open,
  onOpenChange,
  onActiveCountChange,
}: SchedulesPanelProps): JSX.Element | null {
  const { t } = useI18n();
  const [rules, setRules] = useState<ScheduleRule[]>([]);
  const [runs, setRuns] = useState<ScheduleRun[]>([]);
  const [editing, setEditing] = useState<ScheduleInput | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [err, setErr] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"monitor" | "schedules" | "history">("monitor");
  const [view, setView] = useState<"list" | "form">("list");
  const [now, setNow] = useState(() => new Date());
  const [autoPing, setAutoPingState] = useState<AutoPingProviders>({
    claude: "off",
    codex: "off",
    antigravity: "off",
  });
  const [monitor, setMonitorState] = useState<MonitorSettings>({
    auto_ping: false,
    notify_credit_grant: false,
    notify_credit_expiry: false,
    notify: { slack: false, telegram: false, qqbot: false, feishu: false, wechat: false, line: false },
    channels: {
      slack: { label: "Slack", connected: false, target: "" },
      telegram: { label: "Telegram", connected: false, target: "" },
      qqbot: { label: "QQ", connected: false, target: "" },
      feishu: { label: "Feishu", connected: false, target: "" },
      wechat: { label: "WeChat", connected: false, target: "" },
      line: { label: "LINE", connected: false, target: "" },
    },
    host_os: "other",
  });
  const [usage, setUsage] = useState<ProviderUsage[]>([]);
  const [pingConfirm, setPingConfirm] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [r, h, a, m, u] = await Promise.all([
        fetchSchedules(),
        fetchScheduleRuns(RUN_HISTORY_LIMIT),
        fetchAutoPing(),
        fetchMonitorSettings(),
        fetchUsage().catch(() => [] as ProviderUsage[]),
      ]);
      setRules(r);
      setRuns(h);
      setAutoPingState(a);
      setMonitorState(m);
      setUsage(u);
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // Hydrate rule count on mount
  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    onActiveCountChange(rules.filter((rule) => rule.enabled).length);
  }, [onActiveCountChange, rules]);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  useEffect(() => {
    if (!open) return;
    const id = setInterval(() => setNow(new Date()), 10000);
    return () => clearInterval(id);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onOpenChange(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onOpenChange, open]);

  const startNew = () => {
    setEditingId(null);
    setEditing(blankInput());
    setView("form");
    setErr("");
  };

  const startEdit = (rule: ScheduleRule) => {
    setEditingId(rule.id);
    setEditing(toInput(rule));
    setView("form");
    setErr("");
  };

  const cancelEdit = () => {
    setEditing(null);
    setEditingId(null);
    setView("list");
    setErr("");
  };

  const save = async () => {
    if (!editing) return;
    setBusy(true);
    setErr("");
    try {
      if (editingId) {
        await updateSchedule(editingId, editing);
      } else {
        await createSchedule(editing);
      }
      await reload();
      cancelEdit();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (rule: ScheduleRule) => {
    try {
      await setScheduleEnabled(rule.id, !rule.enabled);
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const remove = async (rule: ScheduleRule, confirm = true) => {
    if (confirm && !window.confirm(t("schedules.deleteConfirm", { name: rule.name }))) return;
    try {
      await deleteSchedule(rule.id);
      await reload();
      cancelEdit();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const duplicate = async (rule: ScheduleRule) => {
    setBusy(true);
    setErr("");
    try {
      const input = toInput(rule);
      input.name = rule.name ? t("schedules.copySuffix", { name: rule.name }) : t("schedules.untitledCopy");
      input.enabled = false;
      await createSchedule(input);
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runNow = async (rule: ScheduleRule) => {
    setBusy(true);
    setErr("");
    try {
      await runSchedule(rule.id);
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const changeAutoPing = async (provider: keyof AutoPingProviders, mode: AutoPingMode) => {
    setBusy(true);
    setErr("");
    try {
      setAutoPingState(await setAutoPing(provider, mode));
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const setMonitorAutoPing = async (next: boolean) => {
    setPingConfirm(false);
    setBusy(true);
    setErr("");
    try {
      setMonitorState(await setMonitorSettings({ auto_ping: next }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const patchMonitorNotify = async (patch: Partial<Record<NotifyChannel, boolean>>) => {
    setBusy(true);
    setErr("");
    try {
      setMonitorState(await setMonitorSettings({ notify: patch }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleNotifyCreditGrant = async () => {
    setBusy(true);
    setErr("");
    try {
      setMonitorState(await setMonitorSettings({ notify_credit_grant: !monitor.notify_credit_grant }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleNotifyCreditExpiry = async () => {
    setBusy(true);
    setErr("");
    try {
      setMonitorState(await setMonitorSettings({ notify_credit_expiry: !monitor.notify_credit_expiry }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return null;
  }

  return (
    <div className="drawer-wrap" data-screen-label="04 Schedules">
      <div className="drawer-scrim" onClick={() => onOpenChange(false)}></div>
      <div className="drawer">
        {err && <div className="sch-err" style={{ margin: "10px 18px 0" }}>{err}</div>}
        {view === "form" && editing ? (
          <ScheduleForm
            value={editing}
            onChange={setEditing}
            onSave={save}
            onCancel={cancelEdit}
            busy={busy}
            isNew={!editingId}
            onDelete={editingId ? () => {
              const rule = rules.find((r) => r.id === editingId);
              if (rule) void remove(rule);
            } : undefined}
          />
        ) : (
          <React.Fragment>
            <div className="drawer-head">
              <div className="drawer-titlerow">
                <div className="drawer-title">
                  <span className="ico"></span>{t("schedules.title")}
                </div>
                <div className="drawer-spacer"></div>
                {tab === "schedules" && (
                  <button className="btn btn-primary btn-sm" onClick={startNew}>
                    {SI.plus} {t("schedules.new")}
                  </button>
                )}
                <button className="iconbtn" title={t("common.close")} onClick={() => onOpenChange(false)}>
                  {SI.close}
                </button>
              </div>
              <div className="drawer-tabs">
                <button
                  className={"dtab" + (tab === "monitor" ? " on" : "")}
                  onClick={() => setTab("monitor")}
                >
                  {t("schedules.monitor")}
                  {monitor.auto_ping && <span className="dtab-armed" title={t("schedules.autoPingArmed")}></span>}
                </button>
                <button
                  className={"dtab" + (tab === "schedules" ? " on" : "")}
                  onClick={() => setTab("schedules")}
                >
                  {t("schedules.schedules")} <span className="cnt">{rules.length}</span>
                </button>
                <button
                  className={"dtab" + (tab === "history" ? " on" : "")}
                  onClick={() => setTab("history")}
                >
                  {t("schedules.history")} <span className="cnt">{runs.length}</span>
                </button>
              </div>
            </div>
            <div className="drawer-body scroll">
              {tab === "monitor" ? (
                <QuotaMonitorPanel
                  modes={autoPing}
                  monitor={monitor}
                  usage={usage}
                  runs={runs}
                  now={now}
                  busy={busy}
                  onChangeWatch={changeAutoPing}
                  onRequestEnableAutoPing={() => setPingConfirm(true)}
                  onDisableAutoPing={() => void setMonitorAutoPing(false)}
                  onNotifyPatch={patchMonitorNotify}
                  onNotifyCreditGrantToggle={toggleNotifyCreditGrant}
                  onNotifyCreditExpiryToggle={toggleNotifyCreditExpiry}
                />
              ) : tab === "schedules" ? (
                (() => {
                  if (rules.length === 0) {
                    return (
                      <div className="sch-empty">
                        <div className="glyph">{SI.clock}</div>
                        <h3>{t("schedules.emptyTitle")}</h3>
                        <p>
                          {t("schedules.emptyBody")}
                        </p>
                        <button className="btn btn-primary" onClick={startNew}>
                          {SI.plus} {t("schedules.new")}
                        </button>
                      </div>
                    );
                  }

                  const managedRules = rules.filter((r) => r.managed_by !== null);
                  const userRules = rules.filter((r) => r.managed_by === null);

                  return (
                    <div className="sch-list">
                      {/* System-owned jobs generated by the Quota Monitor */}
                      <div className="sch-group-h">
                        <span className="sgh-t">{t("schedules.managedByMonitor")}</span>
                        <span className="sgh-meta">
                          {t(managedRules.length === 1 ? "schedules.jobSystemOwned" : "schedules.jobsSystemOwned", { count: managedRules.length })}
                        </span>
                      </div>
                      {managedRules.length === 0 ? (
                        <div className="sch-managed-empty">
                          {t("schedules.noManaged")}
                        </div>
                      ) : (
                        managedRules.map((rule) => (
                          <ManagedRow
                            key={rule.id}
                            rule={rule}
                            usage={usage}
                            monitor={monitor}
                            now={now}
                            onGoto={() => setTab("monitor")}
                          />
                        ))
                      )}

                      {/* Hand-made cron schedules */}
                      <div className="sch-group-h sch-group-h--gap">
                        <span className="sgh-t">{t("schedules.yourSchedules")}</span>
                        <span className="sgh-meta">
                          {userRules.length} · cron
                        </span>
                      </div>
                      {userRules.length === 0 ? (
                        <div className="sch-managed-empty">
                          {t("schedules.noCron")}{" "}
                          <button type="button" className="inline-link" onClick={startNew}>
                            {t("common.createOne")}
                          </button>
                          .
                        </div>
                      ) : (
                        userRules.map((rule) => {
                          const lastRun = runs.find((r) => r.schedule_id === rule.id);
                          return (
                            <ScheduleRow
                              key={rule.id}
                              rule={rule}
                              lastRun={lastRun}
                              now={now}
                              onToggle={() => void toggle(rule)}
                              onEdit={() => startEdit(rule)}
                              onDelete={() => void remove(rule, false)}
                              onDuplicate={() => void duplicate(rule)}
                              onRunNow={() => void runNow(rule)}
                            />
                          );
                        })
                      )}
                    </div>
                  );
                })()
              ) : (
                <HistoryTab runs={runs} now={now} />
              )}
            </div>
            {pingConfirm && (
              <AutoPingConfirm
                modes={autoPing}
                busy={busy}
                onCancel={() => setPingConfirm(false)}
                onConfirm={() => void setMonitorAutoPing(true)}
              />
            )}
          </React.Fragment>
        )}
      </div>
    </div>
  );
}

/* ───────────────────────── QuotaMonitorPanel ─────────────────────── */
