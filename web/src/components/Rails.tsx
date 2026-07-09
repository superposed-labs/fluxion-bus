import { useEffect, useMemo, useState } from "react";

import { EXECUTORS } from "../lib/constants";
import { channelLabel } from "../lib/channels";
import { fmtAge, fmtDurBetween } from "../lib/format";
import { useI18n } from "../i18n";
import type { Task, ProviderUsage, UsageWindow } from "../types";

interface SessionRailProps {
  task: Task | null;
  allTasks: Task[];
  onSelect: (taskId: string) => void;
  onContinue?: (task: Task) => void;
}

export function SessionRail({ task, allTasks, onSelect, onContinue }: SessionRailProps): JSX.Element | null {
  const { t } = useI18n();
  const conversationKey = task?.conversation_key ?? null;
  const peers = useMemo(
    () =>
      conversationKey == null
        ? []
        : allTasks
            .filter((t) => t.conversation_key === conversationKey)
            .sort((a, b) => {
              const ta = a.timestamp.received_at ?? "";
              const tb = b.timestamp.received_at ?? "";
              return tb.localeCompare(ta);
            }),
    [allTasks, conversationKey],
  );
  if (!task) return null;

  return (
    <div className="rail-section">
      <div className="rail-h">
        <span>{t("rail.conversation")}</span>
        <span className="meta">
          {t(peers.length === 1 ? "rail.recentTask" : "rail.recentTaskPlural", { count: peers.length })}
        </span>
      </div>
      <div className="session-key">
        <span>{task.conversation_key}</span>
      </div>
      <div className="timeline">
        {peers.map((p) => {
          const isLive = p.status === "RUNNING" || p.status === "RETRYING";
          const dur = p.timestamp.started_at
            ? fmtDurBetween(p.timestamp.started_at, isLive ? null : p.timestamp.ended_at)
            : "—";
          return (
            <div
              key={p.task_id}
              className={"tlrow" + (p.task_id === task.task_id ? " sel" : "")}
              data-st={p.status}
              onClick={() => onSelect(p.task_id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") onSelect(p.task_id);
              }}
            >
              <div className="tlrow-head">
                <span>{p.task_id}</span>
                <span style={{ color: "var(--fg-3)" }}>·</span>
                <span>{p.executor}</span>
                <span style={{ flex: 1 }} />
                <span>{fmtAge(p.timestamp.received_at, t)}</span>
              </div>
              <div className="tlrow-summary">{p.summary || <span className="muted">{t("common.noSummary")}</span>}</div>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "var(--fg-3)",
                  marginTop: 4,
                }}
              >
                {t(`status.${p.status}`)} · {dur}
              </div>
            </div>
          );
        })}
      </div>
      {onContinue && (
        <button
          type="button"
          className="rail-continue"
          onClick={() => onContinue(task)}
          title={t("run.continueTitle")}
        >
          <svg viewBox="0 0 16 16" fill="none"><path d="M3 5.5h7.5M3 8h6M3 10.5h7.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /><path d="M12 6.5 14 8l-2 1.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
          {t("run.continueConversation")}
        </button>
      )}
    </div>
  );
}

interface ExecutorStat {
  ex: string;
  running: number;
  queued: number;
  failed: number;
  total: number;
  state: "idle" | "running" | "error";
  stateText: string;
}

function fmtUntil(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms) || ms <= 0) return "now";
  let s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600);  s -= h * 3600;
  const m = Math.floor(s / 60);
  if (d > 0) return `${d}d${h > 0 ? ` ${h}h` : ""}`;
  if (h > 0) return `${h}h${m > 0 ? ` ${m}m` : ""}`;
  if (m > 0) return `${m}m`;
  return `${s % 60}s`;
}

function clampPct(n: number): number {
  return Math.min(100, Math.max(0, n));
}

function getShortLabel(label: string, key: string): string {
  const lbl = (label || "").toLowerCase();
  const k = (key || "").toLowerCase();
  // Model-scoped sub-limits carry the model name as their label ("Fable").
  if (k.startsWith("scoped_")) return label || key;
  if (lbl.includes("5-hour") || lbl.includes("5h") || k.includes("5h")) return "5h";
  if (
    lbl.includes("weekly") ||
    lbl.includes("1wk") ||
    k.includes("weekly") ||
    k.includes("1wk") ||
    k.includes("7d")
  ) {
    return "1wk";
  }
  if (lbl.includes("credits") || k === "ai_credits") return "credits";
  return key || label || "";
}

function simplifyAccountLabel(label: string): string {
  if (!label) return "";
  let tier = label.trim();
  const prefixes = ["Google AI ", "Google "];
  for (const prefix of prefixes) {
    if (tier.toLowerCase().startsWith(prefix.toLowerCase())) {
      tier = tier.slice(prefix.length);
      break;
    }
  }
  return tier
    .split(" ")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function QuotaRow({ w }: { w: UsageWindow }): JSX.Element {
  const { t } = useI18n();
  const used = w.used_percent;

  if (used == null && w.total == null && w.remaining != null) {
    const formattedRemaining =
      w.remaining >= 1000
        ? `${(w.remaining / 1000).toFixed(w.remaining % 1000 === 0 ? 0 : 1)}k`
        : `${w.remaining}`;
    return (
      <div className="quota-row" data-level="ok">
        <span className="quota-win">{getShortLabel(w.label, w.key)}</span>
        <span style={{ gridColumn: "span 3", textAlign: "right", color: "var(--fg-1)" }}>
          {formattedRemaining} {t("common.credits")}
        </span>
      </div>
    );
  }

  const usedPct = used ?? 0;
  const remainPct = Math.max(0, 100 - usedPct);
  const level = remainPct <= 10 ? "crit" : remainPct <= 25 ? "warn" : "ok";

  return (
    <div className="quota-row" data-level={level}>
      <span className="quota-win" title={w.label}>
        {getShortLabel(w.label, w.key)}
      </span>
      <span className="quota-track" title={`${Math.round(remainPct)}% ${t("common.remaining")}`}>
        <span className="quota-fill" style={{ width: `${clampPct(remainPct)}%` }} />
      </span>
      <span className="quota-pct">
        {Math.round(remainPct)}%<em> {t("common.left")}</em>
      </span>
      <span className="quota-reset" title={t("time.resetsNow")}>
        {w.total != null ? t("common.monthly") : `↻ ${fmtUntil(w.resets_at)}`}
      </span>
    </div>
  );
}

function QuotaMeters({ quota }: { quota: ProviderUsage | undefined }): JSX.Element | null {
  const { t } = useI18n();
  if (!quota || quota.status !== "ok") return null;

  const windows = quota.windows.filter((w) => {
    return !(quota.provider === "claude" && w.key === "ai_credits");
  });

  // Split Antigravity into Gemini and External sub-groups
  if (quota.provider === "antigravity") {
    const credits = windows.filter((w) => w.key === "ai_credits" || w.label.toLowerCase().includes("credit"));
    const gemini = windows.filter((w) => w.key.toLowerCase().includes("gemini") || w.label.toLowerCase().includes("gemini"));
    const external = windows.filter((w) => !credits.includes(w) && !gemini.includes(w));

    return (
      <div className="quota">
        {credits.map((w) => (
          <QuotaRow key={w.key || w.label} w={w} />
        ))}
        {gemini.length > 0 && (
          <div className="quota-group">
            <div className="quota-group-title">{t("rail.geminiModels")}</div>
            {gemini.map((w) => (
              <QuotaRow key={w.key || w.label} w={w} />
            ))}
          </div>
        )}
        {external.length > 0 && (
          <div className="quota-group">
            <div className="quota-group-title">{t("rail.externalModels")}</div>
            {external.map((w) => (
              <QuotaRow key={w.key || w.label} w={w} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // Read-only resets status for Codex
  const resets = quota.provider === "codex" && quota.resets && quota.resets.count > 0 ? quota.resets : null;
  let rc = null;
  if (resets && quota.fetched_at) {
    const fetchedTime = new Date(quota.fetched_at).getTime();
    const nextRemaining = resets.expiries.slice().sort((a, b) => a - b)[0];
    const next = nextRemaining != null ? fetchedTime + nextRemaining : null;
    const days = next != null ? Math.max(0, Math.round((next - Date.now()) / 86400e3)) : null;
    const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const nd = next != null ? new Date(next) : null;
    rc = {
      count: resets.count,
      exp: nd ? MON[nd.getMonth()] + " " + nd.getDate() : null,
      soon: days != null && days <= 7
    };
  }

  if (windows.length === 0 && !rc) return null;

  return (
    <div className="quota">
      {windows.map((w) => (
        <QuotaRow key={w.key || w.label} w={w} />
      ))}
      {rc && (
        <div
          className={"quota-resets" + (rc.soon ? " soon" : "")}
          title={t("rail.resetCreditsTitle") + (rc.exp ? t("rail.nearestExpires", { date: rc.exp }) : "")}
        >
          <span className="qr-ico">⦾</span>
          <span className="qr-v">
            {rc.count} <em>{t("rail.resets")}</em>
          </span>
          {rc.exp && <span className="qr-exp">{t("rail.exp", { date: rc.exp })}</span>}
        </div>
      )}
    </div>
  );
}

interface ExecutorRailProps {
  tasks: Task[];
  usage?: ProviderUsage[];
  showQuota?: boolean;
  executors?: string[];
}

export function ExecutorRail({
  tasks,
  usage,
  showQuota = false,
  executors,
}: ExecutorRailProps): JSX.Element {
  const { t } = useI18n();
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => (t + 1) % 1000), 800);
    return () => window.clearInterval(id);
  }, []);

  const executorNames = useMemo(
    () => (executors && executors.length > 0 ? executors : [...EXECUTORS]),
    [executors],
  );
  const stats: ExecutorStat[] = useMemo(
    () =>
      executorNames.map((ex) => {
        const own = tasks.filter((t) => t.executor === ex);
        const running = own.filter(
          (t) => t.status === "RUNNING" || t.status === "RETRYING",
        ).length;
        const queued = own.filter(
          (t) => t.status === "QUEUED" || t.status === "VALIDATED" || t.status === "RECEIVED",
        ).length;
        const oneHourAgo = Date.now() - 3600 * 1000;
        const failed = own.filter((t) => {
          if (t.status !== "FAILED") return false;
          const timeStr = t.timestamp.ended_at || t.timestamp.received_at;
          if (!timeStr) return false;
          return new Date(timeStr).getTime() > oneHourAgo;
        }).length;
        let state: ExecutorStat["state"] = "idle";
        let stateText = "";
        if (running > 0) {
          state = "running";
          stateText = `${t("status.RUNNING")} · ${running}`;
        } else if (failed > 0) {
          state = "error";
          stateText = t("status.FAILED");
        }
        return { ex, running, queued, failed, total: own.length, state, stateText };
      }),
    [executorNames, tasks, t],
  );

  return (
    <div className="rail-section">
      <div className="rail-h">
        <span>{t("rail.executors")}</span>
        <span className="meta">{executorNames.length}</span>
      </div>
      <div className="exgrid">
        {stats.map((s) => {
          const cells = 22;
          const seed = s.ex.charCodeAt(0) + tick;
          const onCount =
            s.state === "running"
              ? Math.max(4, Math.round(cells * (0.45 + 0.4 * Math.sin(seed * 0.7))))
              : s.state === "error"
                ? 2
                : Math.max(1, Math.round(2 + 2 * Math.abs(Math.sin(seed * 0.3))));
          const providerUsage = usage?.find((p) => p.provider === s.ex);

          return (
            <div key={s.ex} className="excard" data-ex={s.ex}>
              <div className="excard-head">
                <div className="excard-name">
                  <span className="swatch" />
                  {s.ex}
                  {showQuota && providerUsage?.account_label ? (
                    <span className="quota-tag" style={{ marginLeft: 6 }}>
                      {simplifyAccountLabel(providerUsage.account_label)}
                    </span>
                  ) : null}
                </div>
                {s.stateText ? (
                  <span className={"excard-state " + s.state}>{s.stateText}</span>
                ) : null}
              </div>
              <div className="excard-load">
                {Array.from({ length: cells }).map((_, i) => {
                  const phase = ((i + tick) * 1.3 + seed) % cells;
                  const on =
                    s.state === "running"
                      ? i < onCount && phase > 2
                      : s.state === "error"
                        ? i === 4 || i === 9
                        : i < onCount;
                  return <i key={i} className={on ? "on" : ""} />;
                })}
              </div>
              <div className="excard-stats">
                <span>
                  {t("topbar.active")} <b>{s.running}</b>
                </span>
                <span>
                  {t("topbar.queued")} <b>{s.queued}</b>
                </span>
                <span>
                  {t("topbar.failed24h")}{" "}
                  <b style={s.failed ? { color: "var(--st-failed)" } : undefined}>{s.failed}</b>
                </span>
              </div>
              {showQuota && <QuotaMeters quota={providerUsage} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface ChannelSummaryProps {
  tasks: Task[];
}

export function RecentChannels({ tasks }: ChannelSummaryProps): JSX.Element {
  const { t } = useI18n();
  const summary = useMemo(() => {
    const map = new Map<string, number>();
    for (const task of tasks) {
      const key =
        task.channel === "slack"
          ? `${channelLabel(task.channel, t)} · ${(task.channel_meta as { channel?: string }).channel ?? "?"}`
          : task.channel === "wechat"
          ? `${channelLabel(task.channel, t)} · ${(task.channel_meta as { user?: string }).user?.split("@")[0] ?? "?"}`
          : task.channel === "telegram"
          ? `${channelLabel(task.channel, t)} · ${(task.channel_meta as { user?: string }).user ?? "?"}`
          : task.channel === "qqbot"
          ? `${channelLabel(task.channel, t)} · ${(task.channel_meta as { user?: string; openid?: string }).user ?? (task.channel_meta as { openid?: string }).openid ?? "?"}`
          : task.channel === "feishu"
          ? `${channelLabel(task.channel, t)} · ${(task.channel_meta as { user?: string; chat_id?: string }).user ?? (task.channel_meta as { chat_id?: string }).chat_id ?? "?"}`
          : `${channelLabel(task.channel, t)} · ${(task.channel_meta as { host?: string }).host ?? "?"}`;
      map.set(key, (map.get(key) ?? 0) + 1);
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [tasks, t]);

  return (
    <div className="rail-section">
      <div className="rail-h">
        <span>{t("rail.recentChannels")}</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {summary.map(([k, n]) => (
          <div
            key={k}
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              fontSize: 12,
            }}
          >
            <span style={{ color: "var(--fg-1)" }}>{k}</span>
            <span className="mono muted">{n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
