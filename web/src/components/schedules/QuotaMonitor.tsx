import * as React from "react";

import { useI18n } from "../../i18n";
import { channelLabel, NOTIFY_CHANNEL_ORDER } from "../../lib/channels";
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

import { relFuture, relPast } from "./cron";

export const MONITOR_PROVIDERS: Array<{ id: keyof AutoPingProviders; label: string }> = [
  { id: "claude", label: "Claude" },
  { id: "codex", label: "Codex" },
  { id: "antigravity", label: "Antigravity" },
];
export const WATCH_OPTIONS: Array<{ v: AutoPingMode; l: string }> = [
  { v: "off", l: "monitor.watchOff" },
  { v: "5h", l: "5h" },
  { v: "7d", l: "monitor.weekly" },
  { v: "both", l: "monitor.both" },
];
export const NOTIFY_ORDER: NotifyChannel[] = NOTIFY_CHANNEL_ORDER;
// Approximate keep-alive pings/day per tracked window, for the blast estimate.
export const PER_DAY: Record<"5h" | "7d", number> = { "5h": 24 / 5, "7d": 1 / 7 };

export interface TrackedWindow {
  provider: keyof AutoPingProviders;
  label: string;
  window: "5h" | "7d";
}
export function trackedWindows(modes: AutoPingProviders): TrackedWindow[] {
  const out: TrackedWindow[] = [];
  MONITOR_PROVIDERS.forEach((p) => {
    const m = modes[p.id];
    if (m === "5h" || m === "both") out.push({ provider: p.id, label: p.label, window: "5h" });
    if (m === "7d" || m === "both") out.push({ provider: p.id, label: p.label, window: "7d" });
  });
  return out;
}
export const blastPerDay = (tw: TrackedWindow[]): number => tw.reduce((a, w) => a + PER_DAY[w.window], 0);
// Pull enough run history that today's ping count isn't truncated by the limit.
export const RUN_HISTORY_LIMIT = 100;

export const MI_BELL = (
  <svg viewBox="0 0 16 16" fill="none" style={{ width: 15, height: 15 }}>
    <path d="M8 2a3 3 0 00-3 3c0 3-1.2 4-1.5 4.5h9C12.2 9 11 8 11 5a3 3 0 00-3-3z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    <path d="M6.5 12a1.5 1.5 0 003 0" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
  </svg>
);
export const MI_BOLT = (
  <svg viewBox="0 0 16 16" fill="none" style={{ width: 15, height: 15 }}>
    <path d="M9 1.5L3.5 9H7l-.5 5.5L12.5 7H9l0-5.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
  </svg>
);
export const MI_GIFT = (
  <svg viewBox="0 0 16 16" fill="none" style={{ width: 15, height: 15 }}>
    <path d="M3 7.5h10v5.2a.8.8 0 0 1-.8.8H3.8a.8.8 0 0 1-.8-.8V7.5ZM2.2 5.3h11.6v2.2H2.2V5.3ZM8 5.3v8.2M8 5.3S7.2 2.5 5.6 2.5a1.4 1.4 0 0 0 0 2.8H8Zm0 0S8.8 2.5 10.4 2.5a1.4 1.4 0 0 1 0 2.8H8Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
  </svg>
);

export function windowFor(usage: ProviderUsage[], provider: string, key: "5h" | "7d") {
  const u = usage.find((x) => x.provider === provider);
  if (!u) return null;
  return u.windows.find((w) => w.key === key) ?? null;
}
export function planLabel(usage: ProviderUsage[], provider: string): string {
  const u = usage.find((x) => x.provider === provider);
  const label = (u?.account_label ?? "").trim();
  // Capitalize the first letter only ("pro" → "Pro"; "Google AI Pro" stays).
  return label ? label[0]!.toUpperCase() + label.slice(1) : "";
}

export function WatchRow({
  p,
  mode,
  usage,
  now,
  busy,
  onChange,
}: {
  p: { id: keyof AutoPingProviders; label: string };
  mode: AutoPingMode;
  usage: ProviderUsage[];
  now: Date;
  busy: boolean;
  onChange: (mode: AutoPingMode) => void;
}): JSX.Element {
  const { t } = useI18n();
  const w5 = windowFor(usage, p.id, "5h");
  const w7 = windowFor(usage, p.id, "7d");
  const plan = planLabel(usage, p.id);
  const on5 = mode === "5h" || mode === "both";
  const on7 = mode === "7d" || mode === "both";

  const meter = (w: typeof w5, active: boolean, key: string) => (
    <span className={"wm" + (active ? " on" : "")}>
      <span className="wm-k">{key}</span>
      <b>{w && w.used_percent != null ? Math.round(w.used_percent) + "%" : "—"}</b>
      <span className="wm-r">↻ {w && w.resets_at ? relFuture(new Date(w.resets_at), now) : "—"}</span>
    </span>
  );

  return (
    <div className="watch-row" data-off={mode === "off" ? 1 : 0}>
      <div className="watch-id">
        <span className="watch-ex" data-ex={p.id}>
          <span className="dot"></span>
          {p.label}
        </span>
        {plan && <span className="watch-plan">{plan}</span>}
      </div>
      <div className="watch-meters">
        {meter(w5, on5, "5h")}
        {meter(w7, on7, "wk")}
      </div>
      <div className="seg accent wseg">
        {WATCH_OPTIONS.map((o) => (
          <button
            key={o.v}
            type="button"
            className={mode === o.v ? "on" : ""}
            onClick={() => onChange(o.v)}
            disabled={busy}
          >
            {o.v === "5h" ? o.l : t(o.l)}
          </button>
        ))}
      </div>
    </div>
  );
}

export function ChannelChips({
  channels,
  notify,
  busy,
  hostOs,
  onToggle,
}: {
  channels: MonitorSettings["channels"];
  notify: MonitorSettings["notify"];
  busy: boolean;
  hostOs: MonitorSettings["host_os"];
  onToggle: (channel: NotifyChannel, next: boolean) => void;
}): JSX.Element {
  const { t } = useI18n();
  const connected = NOTIFY_ORDER.filter((id) => channels[id]?.connected);
  if (connected.length === 0) {
    return (
      <span className="chan-empty">
        {t(hostOs === "macos" ? "monitor.noChannelsMac" : "monitor.noChannelsAny")}
      </span>
    );
  }
  return (
    <div className="chan-toggles">
      {connected.map((id) => {
        const info = channels[id]!;
        const label = channelLabel(id, t);
        return (
          <button
            key={id}
            type="button"
            className={"chan-toggle" + (notify[id] ? " on" : "")}
            onClick={() => onToggle(id, !notify[id])}
            disabled={busy}
            title={info.target ? `${label} · ${info.target}` : label}
          >
            <span className="chan-dot" data-ch={id}></span>
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function QuotaMonitorPanel({
  modes,
  monitor,
  usage,
  runs,
  now,
  busy,
  onChangeWatch,
  onRequestEnableAutoPing,
  onDisableAutoPing,
  onNotifyPatch,
  onNotifyCreditGrantToggle,
  onNotifyCreditExpiryToggle,
}: {
  modes: AutoPingProviders;
  monitor: MonitorSettings;
  usage: ProviderUsage[];
  runs: ScheduleRun[];
  now: Date;
  busy: boolean;
  onChangeWatch: (provider: keyof AutoPingProviders, mode: AutoPingMode) => void;
  onRequestEnableAutoPing: () => void;
  onDisableAutoPing: () => void;
  onNotifyPatch: (patch: Partial<Record<NotifyChannel, boolean>>) => void;
  onNotifyCreditGrantToggle: () => void;
  onNotifyCreditExpiryToggle: () => void;
}): JSX.Element {
  const { t } = useI18n();
  const notifyOn = NOTIFY_ORDER.some((id) => monitor.notify[id]);
  const connected = NOTIFY_ORDER.filter((id) => monitor.channels[id]?.connected);

  // Tracked windows → managed-job count + per-day blast estimate.
  const tracked = trackedWindows(modes);
  const blast = blastPerDay(tracked);

  // Auto-Ping run stats: count actual keep-alive pings (anchor bursts + virtual
  // pings all carry action_type "ping"), not the monitor's notify firings.
  const pingRuns = runs.filter((r) => r.action_type === "ping");
  const todayCount = pingRuns.filter((r) => {
    const d = new Date(r.fired_at);
    return d.getDate() === now.getDate() && d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
  }).length;
  const lastPing = pingRuns[0] ?? null;

  const toggleNotifyMaster = () => {
    if (notifyOn) {
      onNotifyPatch(Object.fromEntries(NOTIFY_ORDER.map((id) => [id, false])));
    } else if (connected.length > 0) {
      onNotifyPatch({ [connected[0]!]: true });
    }
  };

  return (
    <div className="mon">
      {/* Source-of-truth banner. The companion app is macOS-only, so the copy
          only claims parity with it when this gateway actually runs on macOS. */}
      <div className="mon-sync">
        <span className="mon-sync-dot"></span>
        <div className="mon-sync-copy">
          {monitor.host_os === "macos" ? (
            <React.Fragment>
              <b>{t("monitor.globalMac")}</b>
              <span>{t("monitor.globalMacDesc")}</span>
            </React.Fragment>
          ) : (
            <React.Fragment>
              <b>{t("monitor.globalAny")}</b>
              <span>{t("monitor.globalAnyDesc")}</span>
            </React.Fragment>
          )}
        </div>
        <span className="mon-sync-badge">{t("monitor.synced")}</span>
      </div>

      {/* Step 1 — Watch which windows to track (per provider) */}
      <div className="mon-block">
        <div className="mon-block-h">
          <span className="mon-step">1</span>
          <div className="mon-block-tt">
            <div className="mon-block-t">{t("monitor.watchTitle")}</div>
            <div className="mon-block-d">{t("monitor.watchDesc")}</div>
          </div>
        </div>
        <div className="watch-list">
          {MONITOR_PROVIDERS.map((p) => (
            <WatchRow
              key={p.id}
              p={p}
              mode={modes[p.id]}
              usage={usage}
              now={now}
              busy={busy}
              onChange={(m) => onChangeWatch(p.id, m)}
            />
          ))}
        </div>
      </div>

      {/* Connector between the two layers */}
      <div className="mon-flow">
        <span className="mfl"></span>
        <span className="mft">{t("monitor.whenResets")}</span>
        <span className="mfa">↓</span>
        <span className="mfl"></span>
      </div>

      {/* Step 2 — Global actions taken on any watched reset */}
      <div className="mon-block">
        <div className="mon-block-h">
          <span className="mon-step">2</span>
          <div className="mon-block-tt">
            <div className="mon-block-t">
              {t("monitor.onReset")} <span className="mon-global-pill">{t("monitor.global")}</span>
            </div>
            <div className="mon-block-d">{t("monitor.onResetDesc")}</div>
          </div>
        </div>

        {/* Notify */}
        <div className="act-card" data-on={notifyOn ? 1 : 0}>
          <div className="act-row">
            <div className="act-lead">
              <span className="act-ico notify">{MI_BELL}</span>
              <div className="act-copy">
                <div className="act-t">
                  {t("monitor.notifyReset")} <span className="act-cost ok">{t("monitor.noQuotaCost")}</span>
                </div>
                <div className="act-d">
                  {t("monitor.notifyResetDesc")}
                </div>
              </div>
            </div>
            <div
              className="toggle"
              data-on={notifyOn ? 1 : 0}
              onClick={() => !busy && connected.length > 0 && toggleNotifyMaster()}
              role="switch"
              aria-checked={notifyOn}
            />
          </div>
          {notifyOn && (
            <div className="act-foot">
              <span className="act-foot-lbl">{t("monitor.sendTo")}</span>
              <ChannelChips
                channels={monitor.channels}
                notify={monitor.notify}
                busy={busy}
                hostOs={monitor.host_os}
                onToggle={(id, next) => onNotifyPatch({ [id]: next })}
              />
              <span className="chan-inherit-note">{t("monitor.deliveryList")}</span>
            </div>
          )}
        </div>

        {/* Auto-Ping */}
        <div className={"act-card autoping" + (monitor.auto_ping ? " armed" : "")} data-on={monitor.auto_ping ? 1 : 0}>
          <div className="act-row">
            <div className="act-lead">
              <span className={"act-ico ping" + (monitor.auto_ping ? " armed" : "")}>{MI_BOLT}</span>
              <div className="act-copy">
                <div className="act-t">
                  {t("monitor.autoPingReset")}
                  {monitor.auto_ping ? (
                    <span className="act-armed"><span className="armed-dot"></span>{t("monitor.armed")}</span>
                  ) : (
                    <span className="act-cost warn">{t("monitor.spendsQuota")}</span>
                  )}
                </div>
                <div className="act-d">
                  {t("monitor.autoPingDesc")}{" "}
                  <b className="warn">{t("monitor.autoPingWarn")}</b>
                </div>
              </div>
            </div>
            {monitor.auto_ping && (
              <button className="btn btn-sm" disabled={busy} onClick={onDisableAutoPing}>{t("monitor.disable")}</button>
            )}
          </div>

          {monitor.auto_ping ? (
            <React.Fragment>
              <div className="ping-stats">
                <div className="ps">
                  <span className="ps-k">{t("monitor.estBlast")}</span>
                  <span className="ps-v warn">~{Math.max(1, Math.round(blast))} / day</span>
                </div>
                <div className="ps">
                  <span className="ps-k">{t("monitor.today")}</span>
                  <span className="ps-v">{t("monitor.sent", { count: todayCount })}</span>
                </div>
                <div className="ps">
                  <span className="ps-k">{t("monitor.lastPing")}</span>
                  <span className="ps-v">
                    {lastPing ? `${relPast(new Date(lastPing.fired_at), now)} · ${lastPing.error ? "fail" : "ok"}` : "—"}
                  </span>
                </div>
                <div className="ps">
                  <span className="ps-k">{t("monitor.managedJobs")}</span>
                  <span className="ps-v">{tracked.length}</span>
                </div>
              </div>
              <div className="ping-managed">
                {tracked.length === 0 ? (
                  <span className="warn">{t("monitor.noTracked")}</span>
                ) : (
                  <span>
                    {t(tracked.length === 1 ? "monitor.generatesJob" : "monitor.generatesJobs", { count: tracked.length })}
                  </span>
                )}
              </div>
            </React.Fragment>
          ) : (
            <div className="act-foot">
              <button className="btn btn-warn" disabled={busy} onClick={onRequestEnableAutoPing}>
                {MI_BOLT} {t("monitor.enableAutoPing")}
              </button>
              <span className="ping-hint">{t("monitor.confirmHint")}</span>
            </div>
          )}
        </div>
      </div>

      {/* Separator between resets and credit grants */}
      <div className="mon-flow">
        <span className="mfl"></span>
        <span className="mft">{t("monitor.creditFlow")}</span>
        <span className="mfa">↓</span>
        <span className="mfl"></span>
      </div>

      {/* Credit Grant Block */}
      <div className="mon-block">
        <div className="mon-block-h">
          <span className="mon-step gift">{MI_GIFT}</span>
          <div className="mon-block-tt">
            <div className="mon-block-t">
              {t("monitor.creditGrantTitle")} <span className="mon-global-pill">{t("monitor.global")}</span>
            </div>
            <div className="mon-block-d">
              {t("monitor.creditGrantDesc")}
            </div>
          </div>
        </div>

        {/* Card */}
        <div className="act-card grant" data-on={monitor.notify_credit_grant ? 1 : 0}>
          <div className="act-row">
            <div className="act-lead">
              <span className="act-ico grant">{MI_GIFT}</span>
              <div className="act-copy">
                <div className="act-t">
                  {t("monitor.notifyCreditGrant")}
                  <span className="act-scope" style={{ marginLeft: 6 }}>{t("monitor.codexOnly")}</span>
                  <span className="act-cost ok" style={{ marginLeft: 6 }}>{t("monitor.noQuotaCost")}</span>
                </div>
                <div className="act-d">
                  {t("monitor.notifyCreditGrantDesc")}
                </div>
              </div>
            </div>
            <div
              className="toggle"
              data-on={monitor.notify_credit_grant ? 1 : 0}
              onClick={() => !busy && onNotifyCreditGrantToggle()}
              role="switch"
              aria-checked={monitor.notify_credit_grant}
            />
          </div>

          {monitor.notify_credit_grant && (
            <React.Fragment>
              {(() => {
                const codexUsage = usage.find((x) => x.provider === "codex");
                const resets = codexUsage?.resets && codexUsage.resets.count > 0 ? codexUsage.resets : null;
                let rc = null;
                if (resets && codexUsage?.fetched_at) {
                  const fetchedTime = new Date(codexUsage.fetched_at).getTime();
                  const nextRemaining = resets.expiries.slice().sort((a, b) => a - b)[0];
                  const next = nextRemaining != null ? fetchedTime + nextRemaining : null;
                  const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
                  const nd = next != null ? new Date(next) : null;
                  rc = {
                    count: resets.count,
                    exp: nd ? MON[nd.getMonth()] + " " + nd.getDate() : null,
                  };
                }
                if (!rc) return null;
                return (
                  <div className="grant-preview">
                    <span className="gp-ico">{MI_GIFT}</span>
                    <span className="gp-txt">
                      {t(rc.count > 1 ? "monitor.creditGranted" : "monitor.creditGrantedOne", { count: rc.count })}
                      {rc.exp && (
                        <React.Fragment>
                          {" "}· <b>{t("monitor.nearestExpires", { date: rc.exp })}</b>
                        </React.Fragment>
                      )}
                    </span>
                  </div>
                );
              })()}
              <div className="act-foot">
                <span className="act-foot-lbl">{t("monitor.sendTo")}</span>
                <div className="chan-inherit">
                  {NOTIFY_ORDER.some((id) => monitor.notify[id]) ? (
                    <React.Fragment>
                      {NOTIFY_ORDER.filter((id) => monitor.notify[id]).map((id) => (
                        <span
                          key={id}
                          className="chan-toggle on static"
                          title={channelLabel(id, t)}
                        >
                          <span className="chan-dot" data-ch={id}></span>
                          {channelLabel(id, t)}
                        </span>
                      ))}
                      <span className="chan-inherit-note">{t("monitor.sameChannels")}</span>
                    </React.Fragment>
                  ) : (
                    <span className="chan-empty">
                      {t("monitor.noChannelsPick")}
                    </span>
                  )}
                </div>
              </div>
              <button
                type="button"
                className="grant-sub"
                data-on={monitor.notify_credit_expiry ? 1 : 0}
                onClick={() => !busy && onNotifyCreditExpiryToggle()}
                role="switch"
                aria-checked={monitor.notify_credit_expiry}
              >
                <div className="toggle" data-on={monitor.notify_credit_expiry ? 1 : 0} />
                <span className="gs-copy">
                  <b>{t("monitor.alertBeforeExpiry")}</b>
                  <span>{t("monitor.alertBeforeExpiryDesc")}</span>
                </span>
              </button>
            </React.Fragment>
          )}
        </div>
      </div>
    </div>
  );
}

/* ──────────────── Auto-Ping confirmation modal ──────────────────────── */
export function AutoPingConfirm({
  modes,
  busy,
  onCancel,
  onConfirm,
}: {
  modes: AutoPingProviders;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}): JSX.Element {
  const { t } = useI18n();
  const tw = trackedWindows(modes);
  const blast = blastPerDay(tw);
  const canArm = tw.length > 0;
  return (
    <div className="apc-wrap">
      <div className="apc-scrim" onClick={onCancel}></div>
      <div className="apc" role="dialog" aria-modal="true">
        <div className="apc-h">
          <span className="apc-ico">{MI_BOLT}</span>
          <div>
            <div className="apc-t">{t("monitor.confirmTitle")}</div>
            <div className="apc-sub">{t("monitor.confirmSub")}</div>
          </div>
        </div>
        <div className="apc-body">
          <p className="apc-p">
            {t("monitor.confirmBody")}
          </p>
          <div className="apc-blast">
            <div className="apc-blast-h">
              <span>{t("monitor.blastRadius")}</span>
              <span className="apc-big warn">
                ~{Math.round(blast)}
                <em>{t("monitor.msgsPerDay")}</em>
              </span>
            </div>
            {tw.length === 0 ? (
              <div className="apc-none">
                {t("monitor.noWindowsConfirm")}
              </div>
            ) : (
              <div className="apc-wins">
                {tw.map((w, i) => (
                  <div className="apc-win" key={i}>
                    <span className="apc-win-ex" data-ex={w.provider}>
                      <span className="dot"></span>
                      {w.label}
                    </span>
                    <span className="apc-win-w">{w.window === "5h" ? t("monitor.fiveHourWindow") : t("monitor.weeklyWindow")}</span>
                    <span className="apc-win-n">~{w.window === "5h" ? "5" : "0.1"} / day</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="apc-foot">
          <button className="btn" onClick={onCancel} disabled={busy}>
            {t("schedules.cancel")}
          </button>
          <button className="btn btn-warn" disabled={busy || !canArm} onClick={onConfirm}>
            {MI_BOLT} {t("monitor.enableAutoPingShort")}
          </button>
        </div>
      </div>
    </div>
  );
}
