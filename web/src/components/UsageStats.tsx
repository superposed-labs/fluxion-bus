import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  Fragment,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";

import { fetchUsage, fetchUsageHistory } from "../api";
import { useI18n, type Locale } from "../i18n";
import { useBreakdown, useUsageRange } from "../lib/urlState";
import type { SetTweak } from "../lib/useTweaks";
import type {
  ProviderUsage,
  UsageHistory,
  UsageHistoryTotals,
  UsageHourStat,
  UsageModelStat,
  UsageWindowKey,
} from "../types";

const WINDOWS: UsageWindowKey[] = ["1d", "7d", "30d", "all"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// zh and ja render months identically; en gets the short English names.
const MONTHS_CJK = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

type Billing = "sub" | "metered";

function fmtMoney(n: number): string {
  if (n >= 1000) return "$" + (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k";
  return "$" + n.toFixed(n >= 100 ? 0 : 2);
}

function fmtCost(n: number, lowerBound = false): string {
  return `${lowerBound ? "≥ " : ""}${fmtMoney(n)}`;
}

// ── formatters ──────────────────────────────────────────────────────
function fmtTok(n: number | null | undefined): string {
  if (n == null) return "—";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(a >= 1e10 ? 1 : 2).replace(/\.?0+$/, "") + "B";
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 100e6 ? 0 : 1).replace(/\.0$/, "") + "M";
  if (a >= 1e3) return (n / 1e3).toFixed(a >= 100e3 ? 0 : 1).replace(/\.0$/, "") + "k";
  return String(Math.round(n));
}
function fmtInt(n: number): string {
  return n.toLocaleString("en-US");
}
function pad2(n: number): string {
  return String(n).padStart(2, "0");
}
function titleCase(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

// "claude-opus-4-8" → "Opus 4.8"; "gpt-5.3-codex" → "GPT-5.3 Codex".
function prettyModel(model: string | null): string {
  if (!model) return "—";
  if (model.startsWith("claude-")) {
    const rest = model.slice("claude-".length).replace(/-\d{6,}$/, "");
    const parts = rest.split("-");
    const head = parts[0] ?? rest;
    const name = titleCase(head || rest);
    const version = parts.slice(1).join(".");
    return version ? `${name} ${version}` : name;
  }
  if (model.startsWith("gpt-")) {
    const segs = model.slice("gpt-".length).split("-");
    const head = segs[0] ?? "";
    const tail = segs.slice(1).map(titleCase).join(" ");
    const base = /^\d/.test(head) ? `GPT-${head}` : `GPT ${head}`.trim();
    return tail ? `${base} ${tail}` : base;
  }
  return titleCase(model);
}

function hasUnreportedGpt56CacheWrites(model: UsageModelStat): boolean {
  return model.provider === "codex" && /^gpt-5\.6(?:-|$)/i.test(model.model);
}

// ── component ───────────────────────────────────────────────────────
interface UsageStatsProps {
  billing: Billing;
  setTweak: SetTweak;
}

/**
 * `billing` arrives as a prop rather than from a local useTweaks() call:
 * that hook keeps a private snapshot and persists the whole object, so a
 * second instance would clobber whatever the first one changed after mount.
 */
export function UsageStats({ billing, setTweak }: UsageStatsProps): JSX.Element {
  const { t } = useI18n();
  const [view, setView] = useBreakdown();
  // Named `range`, not `window`: the old name shadowed the global inside this
  // component, which is a trap now that URL state reads window.location.
  const [range, setRange] = useUsageRange();
  const setBilling = (next: Billing) => setTweak("billing", next);
  const [data, setData] = useState<UsageHistory | null>(null);
  const [quota, setQuota] = useState<ProviderUsage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchUsageHistory(range)
      .then((next) => !cancelled && setData(next))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [range]);

  // The weekly-quota strip reuses the live quota probe (read-only, TTL-cached).
  useEffect(() => {
    let cancelled = false;
    fetchUsage()
      .then((next) => !cancelled && setQuota(next))
      .catch(() => {
        /* strip hides itself when there's nothing to show */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Instant custom tooltip via event delegation — the native `title` attribute
  // has an OS-controlled ~1.5s delay that can't be tuned. One listener on the
  // container reads the hovered element's data-tip and anchors a floating chip
  // above it, re-rendering only when the hovered target changes.
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const showTip = (e: ReactMouseEvent) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-tip]");
    if (!el || !el.dataset.tip) return;
    const r = el.getBoundingClientRect();
    setTip({ x: r.left + r.width / 2, y: r.top, text: el.dataset.tip });
  };
  const hideTip = (e: ReactMouseEvent) => {
    if ((e.target as HTMLElement).closest("[data-tip]")) setTip(null);
  };

  const sub = billing === "sub";
  // Sum the official monthly price of each subscription present in the live
  // quota. The backend resolves the price from the detected plan name and tags
  // each provider with `plan_monthly_usd` (sourced from plan_prices.json).
  const planMonthly = useMemo(
    () =>
      quota
        .filter((p) => p.status === "ok")
        .reduce((acc, p) => acc + (p.plan_monthly_usd ?? 0), 0),
    [quota],
  );
  const totals = data?.totals;
  const hasData = !!totals && totals.messages > 0;
  const hasUnreportedCacheWrites = data?.by_model.some(hasUnreportedGpt56CacheWrites) ?? false;

  return (
    <div className="stats-main scroll" onMouseOver={showTip} onMouseOut={hideTip}>
      {tip && (
        <div className="stat-tip" style={{ left: tip.x, top: tip.y }}>
          {tip.text}
        </div>
      )}
      <div className="stats-wrap">
        <div className="stats-subhead">
          <div className="seg">
            <button className={view === "overview" ? "on" : ""} onClick={() => setView("overview")}>
              {t("usage.overview")}
            </button>
            <button className={view === "models" ? "on" : ""} onClick={() => setView("models")}>
              {t("usage.models")}
            </button>
          </div>
          <div className="subhead-right">
            <div className="seg">
              <button className={sub ? "on" : ""} onClick={() => setBilling("sub")}>
                {t("usage.subscription")}
              </button>
              <button className={!sub ? "on" : ""} onClick={() => setBilling("metered")}>
                {t("usage.metered")}
              </button>
            </div>
            <div className="seg mono">
              {WINDOWS.map((w) => (
                <button key={w} className={range === w ? "on" : ""} onClick={() => setRange(w)}>
                  {w === "1d" ? t("usage.today") : w === "all" ? t("usage.all") : w}
                </button>
              ))}
            </div>
          </div>
        </div>

        {error && <div className="stats-empty">API error · {error}</div>}
        {!error && !hasData && <div className="stats-empty">{loading ? "…" : t("usage.noData")}</div>}

        {hasData && totals && view === "overview" && (
          <>
            <Hero totals={totals} sub={sub} lowerBound={hasUnreportedCacheWrites} />
            <StatStrip
              data={data!}
              sub={sub}
              planMonthly={planMonthly}
              lowerBound={hasUnreportedCacheWrites}
            />
            <ReconciliationStrip data={data!} />
            {sub && (
              <PlansStrip
                quota={quota}
                planMonthly={planMonthly}
                cost={totals.cost}
                lowerBound={hasUnreportedCacheWrites}
              />
            )}
            <Heatmap data={data!} />
          </>
        )}
        {hasData && view === "models" && (
          <ModelsView models={data!.by_model} sub={sub} />
        )}
        {hasData && (
          <CostNote
            data={data!}
            sub={sub}
            lowerBound={hasUnreportedCacheWrites}
          />
        )}
      </div>
    </div>
  );
}

// ── cost disclaimer / provenance footnote ───────────────────────────
function CostNote({
  data,
  sub,
  lowerBound,
}: {
  data: UsageHistory;
  sub: boolean;
  lowerBound: boolean;
}): JSX.Element {
  const { t } = useI18n();
  const updated = data.prices_updated_at;
  const uncosted = data.totals.uncosted_tokens ?? 0;
  return (
    <div className="cost-note">
      <span>
        {t(sub ? "usage.disclaimerSub" : "usage.disclaimerMetered")}
        {updated ? ` · ${t("usage.pricesAsOf")} ${updated}` : ""}
      </span>
      {uncosted > 0 && (
        <span className="warn">
          {fmtTok(uncosted)} {t("usage.uncostedNote")}
        </span>
      )}
      {lowerBound && <span className="warn">{t("usage.cacheWriteUnreported")}</span>}
    </div>
  );
}

function ReconciliationStrip({
  data,
}: {
  data: UsageHistory;
}): JSX.Element | null {
  const { t } = useI18n();
  const r = data.codex_reconciliation;
  if (!r || r.status !== "ok" || r.server_tokens == null || r.local_tokens == null) return null;
  const pct = Math.round((r.coverage ?? 0) * 100);
  const excess = r.excess_local_tokens ?? 0;
  const note = excess > 0
    ? t("usage.localExceeds", { tokens: fmtTok(excess) })
    : t("usage.unclassified", { tokens: fmtTok(r.unclassified_tokens) });
  return (
    <div className="recon">
      <div className="recon-head">
        <span>{t("usage.codexCoverage")}</span>
        <b>{pct}%</b>
      </div>
      <div className="recon-track">
        <span style={{ width: `${pct}%` }} />
      </div>
      <div className="recon-meta">
        <span>
          {t("usage.localDetail")} <b>{fmtTok(r.local_tokens)}</b>
        </span>
        <span>
          {t("usage.serverTotal")} <b>{fmtTok(r.server_tokens)}</b>
        </span>
        <span className={excess > 0 ? "warn" : ""}>{note}</span>
      </div>
    </div>
  );
}

// ── hero ────────────────────────────────────────────────────────────
function Hero({
  totals,
  sub,
  lowerBound,
}: {
  totals: UsageHistoryTotals;
  sub: boolean;
  lowerBound: boolean;
}): JSX.Element {
  const { t } = useI18n();
  // The bar decomposes the headline number, so it carries all four components
  // — cache reads included. At a healthy hit rate they dominate it; that is the
  // point, and the legend keeps the small segments legible.
  const parts = [
    { k: t("usage.input"), v: totals.input_tokens, cls: "seg-input", sw: "var(--tk-input)" },
    { k: t("usage.output"), v: totals.output_tokens, cls: "seg-output", sw: "var(--tk-output)" },
    { k: t("usage.cacheWrite"), v: totals.cache_creation_tokens, cls: "seg-cw", sw: "var(--tk-cw)" },
    { k: t("usage.cacheRead"), v: totals.cache_read_tokens, cls: "seg-cr", sw: "var(--tk-cr)" },
  ];
  const hitPct = Math.round(totals.cache_hit * 100);

  return (
    <div className="hero">
      <div className="hero-card hero-main">
        <div className="hero-label">{t("usage.total")}</div>
        <div className="hero-num">
          {fmtTok(totals.total_tokens)}
          <span className="u">{t("usage.tokens")}</span>
        </div>
        <div className="hero-sub">
          {lowerBound ? "≥ " : "≈ "}<b>{fmtMoney(totals.cost)}</b>{" "}
          {lowerBound ? t("usage.lowerBound") : (sub ? t("usage.apiValueNote") : t("usage.spendNote"))}
        </div>
        <div className="hero-fresh">
          <b>{fmtTok(totals.generated_tokens)}</b> {t("usage.freshLabel")}
        </div>
        <div className="compbar">
          {parts.map((p) => (
            <i
              key={p.k}
              className={p.cls}
              style={{ flex: p.v }}
              data-tip={`${p.k}: ${p.cls === "seg-cw" && lowerBound ? t("usage.notReported") : fmtTok(p.v)}`}
            />
          ))}
        </div>
        <div className="complegend">
          {parts.map((p) => (
            <span className="lg" key={p.k}>
              <span className="sw" style={{ background: p.sw }} />
              {p.k}
              <span className="v">
                {p.cls === "seg-cw" && lowerBound
                  ? (p.v > 0 ? `≥ ${fmtTok(p.v)}` : t("usage.notReported"))
                  : fmtTok(p.v)}
              </span>
            </span>
          ))}
        </div>
      </div>

      <div className="hero-card hero-cache">
        <div className="hero-label">{t("usage.cacheServed")}</div>
        <div className="hero-num">
          {fmtTok(totals.cache_read_tokens)}
          <span className="u">{t("usage.tokens")}</span>
        </div>
        <div className="hitring">
          <div className="ring" style={{ "--p": hitPct } as CSSProperties}>
            <b>{hitPct}%</b>
          </div>
          <div className="note">
            {t("usage.cacheNote").split("{pct}").map((part, index) => (
              <Fragment key={index}>
                {index > 0 && <b>{hitPct}%</b>}
                {part}
              </Fragment>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── dense stat strip ────────────────────────────────────────────────
function StatStrip({
  data,
  sub,
  planMonthly,
  lowerBound,
}: {
  data: UsageHistory;
  sub: boolean;
  planMonthly: number;
  lowerBound: boolean;
}): JSX.Element {
  const { t } = useI18n();
  const totals = data.totals;
  const sessions = Math.max(1, totals.sessions);
  const top = data.by_model[0];
  const peak = totals.peak_hour;
  const costPer1M = totals.generated_tokens > 0 ? totals.cost / (totals.generated_tokens / 1e6) : 0;
  const roi = planMonthly > 0 ? totals.cost / planMonthly : 0;
  const costTile = sub
    ? {
        k: t("usage.apiValue"),
        val: fmtCost(totals.cost, lowerBound),
        ctx: <span>{lowerBound ? t("usage.lowerBound") : roi >= 1 ? `≈ ${roi.toFixed(roi >= 10 ? 0 : 1)}× ${t("usage.planPrice")}` : t("usage.inPlan")}</span>,
      }
    : {
        k: t("usage.estSpend"),
        val: fmtCost(totals.cost, lowerBound),
        ctx: (
          <span>
            {fmtCost(costPer1M, lowerBound)} {t("usage.per1MGen")}
          </span>
        ),
      };
  const tiles: { k: string; val: ReactNode; ctx: ReactNode; sm?: boolean }[] = [
    costTile,
    {
      k: t("usage.sessions"),
      val: fmtInt(totals.sessions),
      ctx: <span>{fmtTok(Math.round(totals.total_tokens / sessions))} {t("usage.perSession")}</span>,
    },
    {
      k: t("usage.messages"),
      val: fmtInt(totals.messages),
      ctx: <span>{Math.round(totals.messages / sessions)} {t("usage.perSession")}</span>,
    },
    {
      k: t("usage.cacheHit"),
      val: (
        <>
          {Math.round(totals.cache_hit * 100)}
          <span className="u">%</span>
        </>
      ),
      ctx: <span>{fmtTok(totals.cache_read_tokens)} {t("usage.reused")}</span>,
    },
    {
      k: t("usage.activeDays"),
      val: (
        <>
          {totals.active_days}
          <span className="u">/{totals.span_days}</span>
        </>
      ),
      ctx: <span>{peak == null ? "—" : `${t("usage.peak")} ${pad2(peak)}:00`}</span>,
    },
    {
      k: t("usage.topModel"),
      sm: true,
      val: prettyModel(totals.top_model),
      ctx: top ? (
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span className="ex-sw" style={{ background: `var(--ex-${top.provider})` }} />
          {top.provider} · {fmtTok(top.total_tokens)}
        </span>
      ) : null,
    },
  ];
  return (
    <div className="statstrip">
      {tiles.map((tt) => (
        <div className="stat" key={tt.k}>
          <span className="k">{tt.k}</span>
          <span className={"val" + (tt.sm ? " sm" : "")}>{tt.val}</span>
          <span className="ctx">{tt.ctx}</span>
        </div>
      ))}
    </div>
  );
}

// ── plan / weekly-quota strip (real /api/usage data) ────────────────
function PlansStrip({
  quota,
  planMonthly,
  cost,
  lowerBound,
}: {
  quota: ProviderUsage[];
  planMonthly: number;
  cost: number;
  lowerBound: boolean;
}): JSX.Element | null {
  const { t } = useI18n();
  const rows = quota.flatMap((p) => {
    if (p.status !== "ok") return [];
    const w = p.windows.find((win) => win.key === "7d" || /week/i.test(win.label));
    if (!w || w.used_percent == null) return [];
    return [{ ex: p.provider, name: p.account_label, usedPct: Math.round(w.used_percent) }];
  });
  if (rows.length === 0) return null;
  const mult = planMonthly > 0 ? (cost / planMonthly).toFixed(0) : null;
  return (
    <div className="plans">
      <span className="lead">
        {t("usage.plansCovered").split("{price}").map((part, index) => (
          <Fragment key={index}>
            {index > 0 && <b>{fmtMoney(planMonthly)}{t("usage.monthly")}</b>}
            {part}
          </Fragment>
        ))}
      </span>
      {rows.map((r) => (
        <span className="plan" data-ex={r.ex} key={r.ex}>
          <span className="sw" />
          <span className="nm">
            {r.ex}
            {r.name ? ` · ${r.name}` : ""}
          </span>
          <span className="track">
            <span className="fill" style={{ width: `${r.usedPct}%` }} />
          </span>
          <span className="pct">{r.usedPct}%</span>
        </span>
      ))}
      <span className="spacer" />
      {mult && (
        <span className="note">
          {fmtCost(cost, lowerBound)} {t("usage.apiEquiv")} {lowerBound ? "≥" : "≈"} {mult}× {t("usage.planPrice")}
        </span>
      )}
    </div>
  );
}

// ── calendar heatmap ────────────────────────────────────────────────
interface HeatCell {
  date: string;
  tokens: number;
  level: number;
  daysAgo: number;
}

function buildCalendar(
  byDay: UsageHistory["by_day"],
  locale: Locale,
): { cells: HeatCell[]; marks: { ci: number; label: string }[]; cols: number } {
  const map = new Map(byDay.map((d) => [d.date, d.total_tokens]));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const capDays = 371; // Always show a full year (53 weeks) to match design density
  const start = new Date(today);
  start.setDate(start.getDate() - (capDays - 1));
  // Pull the start back to a Monday so each 7-cell column is one Mon..Sun week.
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));

  const cells: HeatCell[] = [];
  let max = 1;
  for (const d = new Date(start); d <= today; d.setDate(d.getDate() + 1)) {
    const iso = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
    const tokens = map.get(iso) ?? 0;
    if (tokens > max) max = tokens;
    const diffTime = today.getTime() - d.getTime();
    const daysAgo = Math.floor(diffTime / (1000 * 60 * 60 * 24));
    cells.push({ date: iso, tokens, level: 0, daysAgo });
  }
  for (const c of cells) {
    if (c.tokens <= 0) continue;
    const f = c.tokens / max;
    c.level = f > 0.72 ? 4 : f > 0.48 ? 3 : f > 0.24 ? 2 : 1;
  }

  const marks: { ci: number; label: string }[] = [];
  let lastMonth = -1;
  const cols = Math.ceil(cells.length / 7);
  for (let ci = 0; ci < cols; ci++) {
    const first = cells[ci * 7];
    if (!first) break;
    const m = new Date(first.date + "T00:00:00").getMonth();
    if (m !== lastMonth) {
      const monthLabels = locale === "en" ? MONTHS : MONTHS_CJK;
      marks.push({ ci, label: monthLabels[m] ?? "" });
      lastMonth = m;
    }
  }
  return { cells, marks, cols };
}

const COL_W = 16; // 13px cell + 3px gap

function Heatmap({ data }: { data: UsageHistory }): JSX.Element {
  const { t, locale } = useI18n();
  const { cells, marks, cols } = useMemo(
    () => buildCalendar(data.by_day, locale),
    [data.by_day, locale],
  );
  const peak = data.totals.peak_hour;
  const maxHour = Math.max(1, ...data.by_hour.map((h) => h.messages));
  const winDays = data.window === "1d" ? 1 : data.window === "7d" ? 7 : data.window === "30d" ? 30 : 9999;

  return (
    <div className="panel">
      <div className="panel-h">
        <span className="t">{t("usage.throughput")}</span>
        <span className="meta">
          {data.totals.span_days}d · {t("usage.perDay")}
        </span>
      </div>
      <div className="cal">
        <div className="cal-dows">
          <span>{t("usage.dowMon")}</span>
          <span />
          <span>{t("usage.dowWed")}</span>
          <span />
          <span>{t("usage.dowFri")}</span>
          <span />
          <span>{t("usage.dowSun")}</span>
        </div>
        <div className="cal-right scroll">
          <div className="cal-months" style={{ width: cols * COL_W }}>
            {marks.map((m) => (
              <span key={m.ci} style={{ left: m.ci * COL_W }}>
                {m.label}
              </span>
            ))}
          </div>
          <div className="cal-grid">
            {cells.map((c) => {
              const isDim = c.daysAgo >= winDays;
              return (
                <span
                  key={c.date}
                  className={`cal-cell${isDim ? " dim" : " in-win"}`}
                  data-l={c.level}
                  data-tip={`${c.date} · ${fmtTok(c.tokens)} ${t("usage.tokens")}`}
                />
              );
            })}
          </div>
        </div>
      </div>
      <div className="cal-foot">
        <div className="cal-legend">
          {t("usage.less")}
          <i style={{ background: "var(--hm-0)" }} />
          <i style={{ background: "var(--hm-1)" }} />
          <i style={{ background: "var(--hm-2)" }} />
          <i style={{ background: "var(--hm-3)" }} />
          <i style={{ background: "var(--hm-4)" }} />
          {t("usage.more")}
        </div>
        <PeakHours hours={data.by_hour} peak={peak} maxHour={maxHour} />
      </div>
    </div>
  );
}

function PeakHours({
  hours,
  peak,
  maxHour,
}: {
  hours: UsageHourStat[];
  peak: number | null;
  maxHour: number;
}): JSX.Element {
  const { t } = useI18n();
  return (
    <div className="cal-peak">
      <span>{t("usage.byHour")}</span>
      <div className="bars">
        {hours.map((h) => (
          <i
            key={h.hour}
            className={h.hour === peak ? "peak" : ""}
            style={{ height: `${4 + (h.messages / maxHour) * 14}px` }}
            data-tip={`${pad2(h.hour)}:00 · ${h.messages} ${t("usage.msgs")}`}
          />
        ))}
      </div>
      {peak != null && (
        <span>
          {t("usage.peak")}{" "}
          <b style={{ color: "var(--fg-0)", fontFamily: "var(--font-mono)" }}>{pad2(peak)}:00</b>
        </span>
      )}
    </div>
  );
}

// ── models view ─────────────────────────────────────────────────────
function ModelsView({
  models,
  sub,
}: {
  models: UsageModelStat[];
  sub: boolean;
}): JSX.Element {
  const { t } = useI18n();
  const sorted = useMemo(() => [...models].sort((a, b) => b.cost - a.cost), [models]);
  const maxGen = Math.max(1, ...sorted.map((m) => m.generated_tokens));
  const maxReused = Math.max(1, ...sorted.map((m) => m.cache_read_tokens));
  return (
    <div className="panel modelview">
      <div className="panel-h">
        <span className="t">{t("usage.byModel")}</span>
        <div className="mlegend">
          <span className="lg"><i style={{ background: "var(--tk-input)" }} />{t("usage.input")}</span>
          <span className="lg"><i style={{ background: "var(--tk-output)" }} />{t("usage.output")}</span>
          <span className="lg"><i style={{ background: "var(--tk-cw)" }} />{t("usage.cacheWrite")}</span>
          <span className="lg"><i style={{ background: "var(--tk-cr)" }} />{t("usage.cacheRead")}</span>
        </div>
      </div>
      <div className="mnote">
        {t("usage.modelsNote")} {t(sub ? "usage.sortedByValue" : "usage.sortedBySpend")}.
      </div>
      <div className="mrows">
        {sorted.map((m) => {
          const cacheWriteUnreported = hasUnreportedGpt56CacheWrites(m);
          const segs = [
            {
              v: m.input_tokens,
              c: "var(--tk-input)",
              k: cacheWriteUnreported
                ? t("usage.inputMayIncludeWrites")
                : m.provider === "codex"
                  ? t("usage.uncachedInput")
                  : t("usage.inputShort"),
            },
            { v: m.output_tokens, c: "var(--tk-output)", k: t("usage.outputShort") },
            ...(m.cache_creation_tokens > 0
              ? [{ v: m.cache_creation_tokens, c: "var(--tk-cw)", k: t("usage.cacheWriteShort") }]
              : []),
          ].filter((s) => s.v > 0);
          const generatedScale = (m.generated_tokens / maxGen) * 100;
          const reusedScale = (m.cache_read_tokens / maxReused) * 100;
          const cacheDenom = m.input_tokens + m.cache_creation_tokens + m.cache_read_tokens;
          const hitPct = cacheDenom > 0 ? Math.round((m.cache_read_tokens / cacheDenom) * 100) : 0;
          const health = hitPct >= 90 ? "good" : hitPct >= 75 ? "ok" : "low";
          const cw = m.cache_creation_tokens;
          const ctx = m.context_tier_breakdown;
          const hasContextTier = ctx.short + ctx.long > 0;
          return (
            <div className="mrow2" data-ex={m.provider} key={m.model}>
              <div className="m-id">
                <span className="sw" />
                <span className="m-name">
                  <span className="nm">{prettyModel(m.model)}</span>
                  <span className="ex">{m.provider}</span>
                </span>
              </div>

              <div className="m-bars">
                <div className="barline">
                  <span className="bl-label">{t("usage.generatedTrack")}</span>
                  <div className="bar2">
                    <div className="bar2-fill" style={{ width: `${Math.max(6, generatedScale)}%` }}>
                      {segs.map((s) => (
                        <i key={s.k} style={{ background: s.c, flex: s.v }} data-tip={`${s.k}: ${fmtTok(s.v)}`} />
                      ))}
                    </div>
                  </div>
                  <span className="bl-side">{fmtTok(m.generated_tokens)}</span>
                </div>

                <div className="barline">
                  <span className="bl-label">{t("usage.reusedTrack")}</span>
                  <div className="bar2">
                    {m.cache_read_tokens > 0 && (
                      <div className="bar2-fill" style={{ width: `${Math.max(6, reusedScale)}%` }}>
                        <i style={{ background: "var(--tk-cr)", flex: 1 }} data-tip={`${t("usage.cacheReadTip")}: ${fmtTok(m.cache_read_tokens)}`} />
                      </div>
                    )}
                  </div>
                  <span
                    className={"bl-hit " + health}
                    data-tip={t("usage.cacheServedTip", {
                      cached: fmtTok(m.cache_read_tokens),
                      total: fmtTok(cacheDenom),
                    })}
                  >
                    <i className="hdot" />
                    {hitPct}% {t("usage.hitShort")}
                  </span>
                </div>

                <div className="m-meta2">
                  <span><b>{fmtInt(m.messages)}</b> {t("usage.msgs")}</span>
                  <span className="sep">·</span>
                  <span><b>{m.sessions}</b> {t("usage.sess")}</span>
                  <span className="sep">·</span>
                  <span>
                    <b>{fmtTok(m.input_tokens)}</b>{" "}
                    {cacheWriteUnreported ? t("usage.inputMayIncludeWrites") : t("usage.inputShort")}
                  </span>
                  <span className="sep">·</span>
                  <span><b>{fmtTok(m.output_tokens)}</b> {t("usage.outputShort")}</span>
                  {cw > 0 && (
                    <>
                      <span className="sep">·</span>
                      <span><b>{fmtTok(cw)}</b> {t("usage.cacheWriteShort")}</span>
                    </>
                  )}
                  {cacheWriteUnreported && (
                    <>
                      <span className="sep">·</span>
                      <span className="warn">{t("usage.cacheWriteShort")} {t("usage.notReported")}</span>
                    </>
                  )}
                  <span className="sep">·</span>
                  <span><b>{fmtTok(m.cache_read_tokens)}</b> {t("usage.cacheReadShort")}</span>
                  {hasContextTier && (
                    <>
                      <span className="sep">·</span>
                      <span
                        data-tip={t("usage.contextTierTip", {
                          long: ctx.long,
                          short: ctx.short,
                        })}
                      >
                        <b>{ctx.long}</b> {t("usage.longContext")}
                      </span>
                    </>
                  )}
                </div>
              </div>

              <div className="m-right">
                <div className="m-num">
                  <div className="big">{fmtTok(m.total_tokens)}</div>
                  <div className="lbl">{t("usage.totalShort")}</div>
                </div>
                <div className="m-num m-cost">
                  <div className="big">{fmtCost(m.cost, cacheWriteUnreported)}</div>
                  <div className="lbl">{t(sub ? "usage.apiValue" : "usage.estSpend")}</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
