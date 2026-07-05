import { useMemo } from "react";
import * as React from "react";

import { useI18n } from "../../i18n";
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

import { describeCron, fmtRun, nextRuns, parseCron } from "./cron";
import { SI } from "./icons";

export const CRON_PRESETS = [
  { labelKey: "schedules.everyWeekday", cron: "0 9 * * 1-5" },
  { labelKey: "schedules.everyMonday", cron: "0 9 * * 1" },
  { labelKey: "schedules.nightly", cron: "30 2 * * *" },
  { labelKey: "schedules.every6Hours", cron: "0 */6 * * *" },
  { labelKey: "schedules.every15Min", cron: "*/15 * * * *" },
];

export const PROVIDERS = ["claude", "codex", "antigravity"] as const;
export const WINDOW_KEYS = ["7d", "5h", "prompt", "flow"] as const;
export const AGENTS = ["auto", "codex", "claude", "antigravity"] as const;
export const MODES = ["read-only", "workspace-write"] as const;
const COMMON_TIMEZONES = ["UTC", "Asia/Shanghai", "Asia/Tokyo", "America/New_York", "Europe/London"] as const;

function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function modeLabel(mode: string, t: (key: string) => string): string {
  if (mode === "read-only") return t("schedules.modeReadOnly");
  if (mode === "workspace-write") return t("schedules.modeWorkspaceWrite");
  return mode;
}

function agentLabel(agent: string, t: (key: string) => string): string {
  if (agent === "auto") return t("schedules.agentAuto");
  return agent;
}

function actionLabel(actionType: string, agent: string, t: (key: string) => string): string {
  if (actionType === "ping") return t("schedules.actionPingShort");
  return agentLabel(agent, t);
}

function timezoneLabel(timezone: string, t: (key: string, vars?: Record<string, string | number>) => string): string {
  const local = localTimezone();
  if (timezone === "UTC") return "UTC";
  if (timezone === "Asia/Shanghai") return t("schedules.timezoneBeijing");
  if (timezone === "Asia/Tokyo") return t("schedules.timezoneTokyo");
  if (timezone === "America/New_York") return t("schedules.timezoneNewYork");
  if (timezone === "Europe/London") return t("schedules.timezoneLondon");
  if (timezone === local) return t("schedules.timezoneLocal", { timezone });
  return timezone;
}

function timezoneOptions(selectedTimezone: string): string[] {
  const local = localTimezone();
  return Array.from(new Set([selectedTimezone, local, ...COMMON_TIMEZONES].filter(Boolean)));
}

export function CronPreview({ cron, tz }: { cron: string; tz: string }): JSX.Element {
  const { t } = useI18n();
  const human = describeCron(cron, t);
  const valid = parseCron(cron).valid;
  const runs = useMemo(() => {
    if (!valid) return [];
    try {
      return nextRuns(cron, new Date(), 4);
    } catch {
      return [];
    }
  }, [cron, valid]);

  return (
    <div className="cron-preview">
      <div className={"cp-human" + (valid ? "" : " invalid")}>
        <span className="arrow">→</span>
        {valid ? (human ? `${human} (${timezoneLabel(tz, t)})` : t("schedules.customSchedule")) : t("schedules.invalidCron")}
      </div>
      {valid && runs.length > 0 && (
        <div className="cp-next">
          <span className="cp-lbl">{t("schedules.nextRuns")}</span>
          <div className="cp-runs">
            {runs.map((r, i) => (
              <span className="cp-run" key={i}>
                {fmtRun(r)}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function Seg({
  options,
  value,
  onChange,
  accent,
}: {
  options: Array<{ v: string; l: string }>;
  value: string;
  onChange: (v: string) => void;
  accent?: boolean;
}): JSX.Element {
  return (
    <div className={"seg" + (accent ? " accent" : "")}>
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          className={value === o.v ? "on" : ""}
          onClick={() => onChange(o.v)}
        >
          {o.l}
        </button>
      ))}
    </div>
  );
}

export interface FormProps {
  value: ScheduleInput;
  onChange: (next: ScheduleInput) => void;
  onSave: () => void;
  onCancel: () => void;
  busy: boolean;
  isNew: boolean;
  onDelete?: () => void;
}

export function ScheduleForm({ value, onChange, onSave, onCancel, busy, isNew, onDelete }: FormProps): JSX.Element {
  const { t } = useI18n();
  const setTrigger = (patch: Partial<ScheduleInput["trigger"]>) =>
    onChange({ ...value, trigger: { ...value.trigger, ...patch } });
  const setAction = (patch: Partial<ScheduleInput["action"]>) =>
    onChange({ ...value, action: { ...value.action, ...patch } });
  const setPolicy = (patch: Partial<ScheduleInput["policy"]>) =>
    onChange({ ...value, policy: { ...value.policy, ...patch } });

  const isCron = value.trigger.type === "cron";
  const isPing = value.action.type === "ping";

  const triggerType = value.trigger.type;
  const actionType = value.action.type;
  const selectedTimezones = timezoneOptions(value.trigger.timezone);

  const summary = isCron
    ? `${describeCron(value.trigger.cron, t) || value.trigger.cron} → ${actionLabel(value.action.type, value.action.agent, t)} · ${modeLabel(value.action.mode, t)}`
    : `${t("schedules.onRefresh")} ${value.trigger.provider} ${value.trigger.window_key} → ${actionLabel(value.action.type, value.action.agent, t)} · ${modeLabel(value.action.mode, t)}`;

  return (
    <div className="form">
      <div className="form-backbar">
        <button type="button" className="iconbtn" onClick={onCancel} title={t("common.close")}>
          {SI.back}
        </button>
        <span className="ttl">{isNew ? t("schedules.new") : t("schedules.editSchedule")}</span>
      </div>
      <div className="form-body">
        <div className="fsection">
          <div className="field">
            <label>{t("schedules.name")}</label>
            <input
              className="input"
              value={value.name}
              onChange={(e) => onChange({ ...value, name: e.target.value })}
              placeholder={t("schedules.namePlaceholder")}
            />
          </div>
        </div>

        <div className="fsection">
          <div className="fsection-h">{t("schedules.triggerSection")}</div>
          <Seg
            accent
            options={[
              { v: "cron", l: t("schedules.triggerCron") },
              { v: "quota_refresh", l: t("schedules.triggerQuota") },
            ]}
            value={triggerType}
            onChange={(v) => setTrigger({ type: v })}
          />
          {isCron ? (
            <React.Fragment>
              <div className="cron-presets">
                {CRON_PRESETS.map((p) => (
                  <button
                    key={p.cron}
                    type="button"
                    className={"cron-chip" + (value.trigger.cron === p.cron ? " on" : "")}
                    onClick={() => setTrigger({ cron: p.cron })}
                  >
                    {t(p.labelKey)}
                  </button>
                ))}
              </div>
              <div className="frow c2-wide">
                <div className="field">
                  <label>
                    {t("schedules.cronExpression")} <span className="hint">m h dom mon dow</span>
                  </label>
                  <input
                    className="input mono"
                    value={value.trigger.cron}
                    onChange={(e) => setTrigger({ cron: e.target.value })}
                    spellCheck={false}
                  />
                </div>
                <div className="field">
                  <label>{t("schedules.timezone")}</label>
                  <select
                    className="select"
                    value={value.trigger.timezone}
                    onChange={(e) => setTrigger({ timezone: e.target.value })}
                  >
                    {selectedTimezones.map((tz) => (
                      <option key={tz} value={tz}>
                        {timezoneLabel(tz, t)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <CronPreview cron={value.trigger.cron} tz={value.trigger.timezone} />
            </React.Fragment>
          ) : (
            <React.Fragment>
              <div className="frow c2">
                <div className="field">
                  <label>{t("schedules.provider")}</label>
                  <select
                    className="select"
                    value={value.trigger.provider}
                    onChange={(e) => setTrigger({ provider: e.target.value })}
                  >
                    {PROVIDERS.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>{t("schedules.window")}</label>
                  <select
                    className="select"
                    value={value.trigger.window_key}
                    onChange={(e) => setTrigger({ window_key: e.target.value })}
                  >
                    {WINDOW_KEYS.map((w) => (
                      <option key={w} value={w}>
                        {w}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="cron-preview">
                <div className="cp-human">
                  <span className="arrow">→</span>{t("schedules.quotaRefreshPreview", { provider: value.trigger.provider ?? "", window: value.trigger.window_key ?? "" })}
                </div>
              </div>
            </React.Fragment>
          )}
        </div>

        <div className="fsection">
          <div className="fsection-h">{t("schedules.actionSection")}</div>
          <Seg
            accent
            options={[
              { v: "subagent", l: t("schedules.actionSubagent") },
              { v: "ping", l: t("schedules.actionPing") },
            ]}
            value={actionType}
            onChange={(v) => setAction({ type: v })}
          />
          {!isPing && (
            <React.Fragment>
              <div className="frow c2">
                <div className="field">
                  <label>{t("schedules.agent")}</label>
                  <select
                    className="select"
                    value={value.action.agent}
                    onChange={(e) => setAction({ agent: e.target.value })}
                  >
                    {AGENTS.map((a) => (
                      <option key={a} value={a}>
                        {agentLabel(a, t)}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>{t("schedules.mode")}</label>
                  <select
                    className="select"
                    value={value.action.mode}
                    onChange={(e) => setAction({ mode: e.target.value })}
                  >
                    {MODES.map((m) => (
                      <option key={m} value={m}>
                        {modeLabel(m, t)}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="field">
                <label>{t("schedules.prompt")}</label>
                <textarea
                  className="textarea"
                  value={value.action.prompt}
                  onChange={(e) => setAction({ prompt: e.target.value })}
                  placeholder={t("schedules.promptPlaceholder")}
                />
              </div>
              <div className="frow c2">
                <div className="field">
                  <label>{t("schedules.workspace")}</label>
                  <input
                    className="input mono"
                    value={value.action.workspace}
                    onChange={(e) => setAction({ workspace: e.target.value })}
                    placeholder="."
                    spellCheck={false}
                  />
                </div>
                <div className="field">
                  <label>
                    {t("schedules.project")} <span className="hint">{t("schedules.optional")}</span>
                  </label>
                  <input
                    className="input mono"
                    value={value.action.project ?? ""}
                    onChange={(e) => setAction({ project: e.target.value || null })}
                    placeholder={t("schedules.projectPlaceholder")}
                    spellCheck={false}
                  />
                </div>
              </div>
            </React.Fragment>
          )}
          {isPing && (
            <React.Fragment>
              <div className="fnote">
                {t("schedules.pingNote")}
              </div>
              <div className="field">
                <label>{t("schedules.promptOptional")}</label>
                <textarea
                  className="textarea"
                  value={value.action.prompt}
                  onChange={(e) => setAction({ prompt: e.target.value })}
                  placeholder={t("schedules.pingPlaceholder")}
                />
              </div>
            </React.Fragment>
          )}
        </div>

        <div className="fsection">
          <div className="fsection-h">{t("schedules.guardrails")}</div>
          <div className="frow c2">
            <div className="field">
              <label>
                {t("schedules.cooldown")} <span className="hint">sec</span>
              </label>
              <input
                className="input mono"
                type="number"
                min={0}
                value={value.policy.cooldown_sec}
                onChange={(e) => setPolicy({ cooldown_sec: Number(e.target.value) || 0 })}
              />
            </div>
            <div className="field">
              <label>{t("schedules.maxRunsDay")}</label>
              <input
                className="input mono"
                type="number"
                min={1}
                value={value.policy.max_runs_per_day}
                onChange={(e) => setPolicy({ max_runs_per_day: Number(e.target.value) || 1 })}
              />
            </div>
          </div>
          {isCron && (
            <div className="field">
              <label>{t("schedules.catchUp")}</label>
              <Seg
                options={[
                  { v: "skip", l: t("schedules.skipMissed") },
                  { v: "run_once", l: t("schedules.runOnce") },
                ]}
                value={value.policy.catch_up}
                onChange={(v) => setPolicy({ catch_up: v })}
              />
            </div>
          )}
        </div>
      </div>
      <div className="form-foot">
        <span className="summ">{summary}</span>
        <div className="acts">
          {!isNew && onDelete && (
            <button
              className="btn"
              style={{
                background: "var(--st-failed-bg)",
                borderColor: "var(--st-failed)",
                color: "var(--st-failed)",
                marginRight: "auto",
                fontFamily: "var(--font-sans)",
                fontSize: "12.5px",
                fontWeight: 500,
                padding: "6px 13px",
                borderRadius: "7px",
                border: "1px solid transparent",
                cursor: "pointer",
              }}
              type="button"
              onClick={onDelete}
              disabled={busy}
            >
              {t("schedules.delete")}
            </button>
          )}
          <button className="btn" type="button" onClick={onCancel} disabled={busy}>
            {t("schedules.cancel")}
          </button>
          <button className="btn btn-primary" type="button" onClick={onSave} disabled={busy}>
            {busy ? t("schedules.saving") : isNew ? t("schedules.create") : t("schedules.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
