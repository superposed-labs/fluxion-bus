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
import { useI18n } from "../i18n";
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
const MONTHS_ZH = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];
const MONTHS_JA = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

type Billing = "sub" | "metered";

type Lang = "zh" | "ja" | "en";
const L = {
  overview: { zh: "概览", ja: "概要", en: "Overview" },
  models: { zh: "模型", ja: "モデル", en: "Models" },
  generated: { zh: "生成 Token", ja: "生成トークン", en: "Generated tokens" },
  genNote: {
    zh: "输入 + 输出 + 缓存写入 · 缓存读取单独计算 →",
    ja: "入力 + 出力 + キャッシュ書込 · キャッシュ読込は別枠 →",
    en: "input + output + cache writes · cache reads counted separately →",
  },
  cacheServed: { zh: "由缓存提供的上下文", ja: "キャッシュから供給", en: "Context served from cache" },
  input: { zh: "输入", ja: "入力", en: "Input" },
  output: { zh: "输出", ja: "出力", en: "Output" },
  cacheWrite: { zh: "缓存写入", ja: "キャッシュ書込", en: "Cache write" },
  totalTokens: { zh: "总 Token", ja: "合計トークン", en: "Total tokens" },
  sessions: { zh: "会话", ja: "セッション", en: "Sessions" },
  messages: { zh: "消息", ja: "メッセージ", en: "Messages" },
  cacheHit: { zh: "缓存命中", ja: "キャッシュ命中", en: "Cache hit" },
  contextTier: { zh: "上下文分档", ja: "コンテキスト階層", en: "Context tier" },
  longContext: { zh: "长上下文", ja: "長コンテキスト", en: "Long context" },
  shortContext: { zh: "短上下文", ja: "短コンテキスト", en: "Short context" },
  activeDays: { zh: "活跃天数", ja: "アクティブ日数", en: "Active days" },
  topModel: { zh: "最常用模型", ja: "よく使うモデル", en: "Top model" },
  perSession: { zh: "/ 会话", ja: "/ セッション", en: "/ session" },
  reused: { zh: "复用", ja: "再利用", en: "reused" },
  gen: { zh: "生成", ja: "生成", en: "gen" },
  peak: { zh: "峰值", ja: "ピーク", en: "peak" },
  weeklyQuota: { zh: "周配额使用", ja: "週次クォータ消費", en: "Weekly quota used" },
  byModel: { zh: "按模型", ja: "モデル別", en: "By model" },
  byModelMeta: {
    zh: "按 Token 排序 · 条形 = 生成 Token（输入 / 输出 / 缓存写入）",
    ja: "トークン量順 · バー = 生成トークン(入力/出力/キャッシュ書込)",
    en: "by tokens · bar = generated tokens (input / output / cache-write)",
  },
  throughput: { zh: "每日 Token 吞吐", ja: "日次トークン量", en: "Daily token throughput" },
  perDay: { zh: "生成 Token / 天", ja: "生成トークン / 日", en: "generated tokens / day" },
  byHour: { zh: "按小时", ja: "時間帯", en: "by hour" },
  msgs: { zh: "消息", ja: "msgs", en: "msgs" },
  sess: { zh: "会话", ja: "sessions", en: "sessions" },
  generatedShort: { zh: "生成", ja: "生成", en: "generated" },
  totalShort: { zh: "合计", ja: "合計", en: "total" },
  less: { zh: "少", ja: "少", en: "less" },
  more: { zh: "多", ja: "多", en: "more" },
  subscription: { zh: "订阅", ja: "サブスク", en: "Subscription" },
  metered: { zh: "按量", ja: "従量", en: "Metered" },
  apiValue: { zh: "API 等价值", ja: "API 等価", en: "API value" },
  estSpend: { zh: "预估成本", ja: "推定コスト", en: "Est. spend" },
  apiValueNote: {
    zh: "API 等价值 · 已包含在你的订阅内，不是实际扣费",
    ja: "の API 等価値 · プラン内のため非課金",
    en: "in API-equivalent value · covered by your plans, not billed",
  },
  spendNote: {
    zh: "预估花费 · 缓存读取按约 1/10 输入价格计算",
    ja: "の推定コスト · キャッシュ読込は入力の約1/10で算入",
    en: "estimated spend · cache reads billed at ~1/10 input",
  },
  inPlan: { zh: "计划内", ja: "プラン内", en: "in-plan" },
  planPrice: { zh: "计划价格", ja: "プラン価格", en: "plan price" },
  per1MGen: { zh: "/ 100万生成", ja: "/ 100万生成", en: "/ 1M gen" },
  apiEquiv: { zh: "API 等价", ja: "API 等価", en: "API-equivalent" },
  generatedTrack: { zh: "生成", ja: "生成", en: "Generated" },
  reusedTrack: { zh: "复用", ja: "再利用", en: "Reused" },
  sortedByValue: { zh: "按 API 等价值", ja: "API 等価値順", en: "by API value" },
  sortedBySpend: { zh: "按成本", ja: "コスト順", en: "by spend" },
  estimated: { zh: "预估", ja: "※コストは推定値", en: "estimated" },
  disclaimerSub: {
    zh: "成本基于手动维护的价格表估算。订阅不是按量计费，因此这里是 API 等价值，不是账单金额。计划总额使用检测到的订阅层级官方月价计算。",
    ja: "コストは手動メンテの価格表による推定。サブスクは従量課金ではないため、これは請求額ではなく API 換算値です。プラン合計は検出した契約プランの公式月額で算出。",
    en: "Cost is an estimate from a hand-maintained price table. Your subscription isn't usage-billed, so this is API-equivalent value — not a charge. Plan totals use the official monthly price of your detected subscription tier.",
  },
  disclaimerMetered: {
    zh: "成本基于手动维护的价格表估算，已按可识别的长短上下文阶梯价计算，但仍未包含批处理/优先级折扣。不是实际账单金额。",
    ja: "コストは手動メンテの価格表による推定で、判別できる短/長コンテキスト段階は反映済みです。バッチ/優先割引は未反映で、実際の請求額ではありません。",
    en: "Cost is an estimate from a hand-maintained price table. Recognisable short/long-context tiers are included, but batch/priority discounts are still excluded. Not your actual bill.",
  },
  pricesAsOf: { zh: "价格基准日", ja: "価格基準日", en: "prices as of" },
  uncostedNote: {
    zh: "未识别/本地模型未计价",
    ja: "は未対応/ローカルモデルのため未計上",
    en: "on unrecognised / local models aren't priced",
  },
  noData: { zh: "没有找到本地使用日志。", ja: "ローカルの利用ログが見つかりません。", en: "No local usage logs found." },
  codexCoverage: { zh: "Codex 服务器对账", ja: "Codex サーバー照合", en: "Codex server reconciliation" },
  cacheRead: { zh: "缓存读取", ja: "キャッシュ読込", en: "Cache read" },
  modelsNote: {
    zh: "每个模型两条柱：生成（输入 → 输出 → 缓存写入）和复用（缓存读取）。OpenAI/Codex 与 Gemini 使用隐式缓存，因此不会显示独立的缓存写入段。",
    ja: "モデルごとに2本のバー — 生成（入力 → 出力 → キャッシュ書込）と再利用（キャッシュ読込）。OpenAI/Codex・Gemini は暗黙キャッシュのためキャッシュ書込の区切りは出ません。",
    en: "Two bars per model — Generated (input → output → cache write) and Reused (cache read). OpenAI/Codex & Gemini cache implicitly, so they show no separate cache-write segment.",
  },
  cacheNote: {
    zh: "所有上下文中有 {pct}% 来自 prompt cache，属于复用而非重新生成。",
    ja: "全コンテキストの {pct}% はキャッシュから読込 — 再生成ではなく再利用です。",
    en: "{pct}% of all context was served from prompt cache — reused, not regenerated.",
  },
  localDetail: { zh: "本地明细", ja: "ローカル明細", en: "Local detail" },
  serverTotal: { zh: "服务器合计", ja: "サーバー合計", en: "Server total" },
  localExceeds: {
    zh: "本地明细超过服务器值 {tokens} · 请检查指标口径",
    ja: "ローカル明細がサーバー値を {tokens} 超過 · 口径を要確認",
    en: "Local detail exceeds server total by {tokens} · check metric semantics",
  },
  unclassified: {
    zh: "{tokens} 来自非本地、其他设备或未分类",
    ja: "{tokens} は非ローカル、他端末、または未分類",
    en: "{tokens} is non-local, from other devices, or unclassified",
  },
  today: { zh: "今日", ja: "今日", en: "Today" },
  all: { zh: "全部", ja: "すべて", en: "All" },
  tokens: { zh: "tokens", ja: "tokens", en: "tokens" },
  monthly: { zh: "/月", ja: "/月", en: "/mo" },
  plansCovered: {
    zh: "你的订阅计划已覆盖，总计 {price}/月。真实限制是周配额:",
    ja: "プラン合計 {price}/月 でカバー — 実際の上限は週次クォータ:",
    en: "Covered by your plans — {price}/mo total. The real meter is weekly quota:",
  },
  dowMon: { zh: "周一", ja: "月", en: "Mon" },
  dowWed: { zh: "周三", ja: "水", en: "Wed" },
  dowFri: { zh: "周五", ja: "金", en: "Fri" },
  dowSun: { zh: "周日", ja: "日", en: "Sun" },
  hitShort: { zh: "命中", ja: "命中", en: "hit" },
  inputShort: { zh: "输入", ja: "入力", en: "input" },
  outputShort: { zh: "输出", ja: "出力", en: "output" },
  cacheWriteShort: { zh: "写入", ja: "書込", en: "cache write" },
  cacheReadShort: { zh: "读取", ja: "読込", en: "cache read" },
  uncachedInput: { zh: "未缓存输入", ja: "非キャッシュ入力", en: "uncached input" },
  cacheReadTip: { zh: "缓存读取", ja: "キャッシュ読込", en: "cache read" },
  cacheServedTip: {
    zh: "{cached} / {total} 上下文来自缓存",
    ja: "{cached} / {total} のコンテキストをキャッシュから供給",
    en: "{cached} of {total} context served from cache",
  },
  contextTierTip: {
    zh: "{long} 次长上下文，{short} 次短上下文",
    ja: "長 {long} 回、短 {short} 回",
    en: "{long} long-context, {short} short-context requests",
  },
} as const;

function fmtMoney(n: number): string {
  if (n >= 1000) return "$" + (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k";
  return "$" + n.toFixed(n >= 100 ? 0 : 2);
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

// ── component ───────────────────────────────────────────────────────
export function UsageStats(): JSX.Element {
  const { locale } = useI18n();
  const [view, setView] = useState<"overview" | "models">("overview");
  const [window, setWindow] = useState<UsageWindowKey>("7d");
  const [billing, setBilling] = useState<Billing>("sub");
  const [data, setData] = useState<UsageHistory | null>(null);
  const [quota, setQuota] = useState<ProviderUsage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchUsageHistory(window)
      .then((next) => !cancelled && setData(next))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [window]);

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

  const dataLocale = (data?.locale ?? "").toLowerCase();
  const lang: Lang = locale === "zh" || dataLocale.startsWith("zh")
    ? "zh"
    : locale === "ja" || dataLocale.startsWith("ja")
      ? "ja"
      : "en";
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
  const t = data?.totals;
  const hasData = !!t && t.messages > 0;

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
              {L.overview[lang]}
            </button>
            <button className={view === "models" ? "on" : ""} onClick={() => setView("models")}>
              {L.models[lang]}
            </button>
          </div>
          <div className="subhead-right">
            <div className="seg">
              <button className={sub ? "on" : ""} onClick={() => setBilling("sub")}>
                {L.subscription[lang]}
              </button>
              <button className={!sub ? "on" : ""} onClick={() => setBilling("metered")}>
                {L.metered[lang]}
              </button>
            </div>
            <div className="seg mono">
              {WINDOWS.map((w) => (
                <button key={w} className={window === w ? "on" : ""} onClick={() => setWindow(w)}>
                  {w === "1d" ? L.today[lang] : w === "all" ? L.all[lang] : w}
                </button>
              ))}
            </div>
          </div>
        </div>

        {error && <div className="stats-empty">API error · {error}</div>}
        {!error && !hasData && <div className="stats-empty">{loading ? "…" : L.noData[lang]}</div>}

        {hasData && t && view === "overview" && (
          <>
            <Hero t={t} sub={sub} lang={lang} />
            <StatStrip data={data!} sub={sub} planMonthly={planMonthly} lang={lang} />
            <ReconciliationStrip data={data!} lang={lang} />
            {sub && (
              <PlansStrip quota={quota} planMonthly={planMonthly} cost={t.cost} lang={lang} />
            )}
            <Heatmap data={data!} lang={lang} />
          </>
        )}
        {hasData && view === "models" && (
          <ModelsView models={data!.by_model} sub={sub} lang={lang} />
        )}
        {hasData && <CostNote data={data!} sub={sub} lang={lang} />}
      </div>
    </div>
  );
}

// ── cost disclaimer / provenance footnote ───────────────────────────
function CostNote({ data, sub, lang }: { data: UsageHistory; sub: boolean; lang: Lang }): JSX.Element {
  const updated = data.prices_updated_at;
  const uncosted = data.totals.uncosted_tokens ?? 0;
  return (
    <div className="cost-note">
      <span>
        {(sub ? L.disclaimerSub : L.disclaimerMetered)[lang]}
        {updated ? ` · ${L.pricesAsOf[lang]} ${updated}` : ""}
      </span>
      {uncosted > 0 && (
        <span className="warn">
          {fmtTok(uncosted)} {L.uncostedNote[lang]}
        </span>
      )}
    </div>
  );
}

function ReconciliationStrip({
  data,
  lang,
}: {
  data: UsageHistory;
  lang: Lang;
}): JSX.Element | null {
  const r = data.codex_reconciliation;
  if (!r || r.status !== "ok" || r.server_tokens == null || r.local_tokens == null) return null;
  const pct = Math.round((r.coverage ?? 0) * 100);
  const excess = r.excess_local_tokens ?? 0;
  const note = excess > 0
    ? L.localExceeds[lang].replace("{tokens}", fmtTok(excess))
    : L.unclassified[lang].replace("{tokens}", fmtTok(r.unclassified_tokens));
  return (
    <div className="recon">
      <div className="recon-head">
        <span>{L.codexCoverage[lang]}</span>
        <b>{pct}%</b>
      </div>
      <div className="recon-track">
        <span style={{ width: `${pct}%` }} />
      </div>
      <div className="recon-meta">
        <span>
          {L.localDetail[lang]} <b>{fmtTok(r.local_tokens)}</b>
        </span>
        <span>
          {L.serverTotal[lang]} <b>{fmtTok(r.server_tokens)}</b>
        </span>
        <span className={excess > 0 ? "warn" : ""}>{note}</span>
      </div>
    </div>
  );
}

// ── hero ────────────────────────────────────────────────────────────
function Hero({ t, sub, lang }: { t: UsageHistoryTotals; sub: boolean; lang: Lang }): JSX.Element {
  const parts = [
    { k: L.input[lang], v: t.input_tokens, cls: "seg-input", sw: "var(--tk-input)" },
    { k: L.output[lang], v: t.output_tokens, cls: "seg-output", sw: "var(--tk-output)" },
    { k: L.cacheWrite[lang], v: t.cache_creation_tokens, cls: "seg-cw", sw: "var(--tk-cw)" },
  ];
  const hitPct = Math.round(t.cache_hit * 100);

  return (
    <div className="hero">
      <div className="hero-card hero-main">
        <div className="hero-label">{L.generated[lang]}</div>
        <div className="hero-num">
          {fmtTok(t.generated_tokens)}
          <span className="u">{L.tokens[lang]}</span>
        </div>
        <div className="hero-sub">
          ≈ <b>{fmtMoney(t.cost)}</b> {sub ? L.apiValueNote[lang] : L.spendNote[lang]}
        </div>
        <div className="compbar">
          {parts.map((p) => (
            <i key={p.k} className={p.cls} style={{ flex: p.v }} data-tip={`${p.k}: ${fmtTok(p.v)}`} />
          ))}
        </div>
        <div className="complegend">
          {parts.map((p) => (
            <span className="lg" key={p.k}>
              <span className="sw" style={{ background: p.sw }} />
              {p.k}
              <span className="v">{fmtTok(p.v)}</span>
            </span>
          ))}
        </div>
      </div>

      <div className="hero-card hero-cache">
        <div className="hero-label">{L.cacheServed[lang]}</div>
        <div className="hero-num">
          {fmtTok(t.cache_read_tokens)}
          <span className="u">{L.tokens[lang]}</span>
        </div>
        <div className="hitring">
          <div className="ring" style={{ "--p": hitPct } as CSSProperties}>
            <b>{hitPct}%</b>
          </div>
          <div className="note">
            {L.cacheNote[lang].split("{pct}").map((part, index) => (
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
  lang,
}: {
  data: UsageHistory;
  sub: boolean;
  planMonthly: number;
  lang: Lang;
}): JSX.Element {
  const t = data.totals;
  const sessions = Math.max(1, t.sessions);
  const top = data.by_model[0];
  const peak = t.peak_hour;
  const costPer1M = t.generated_tokens > 0 ? t.cost / (t.generated_tokens / 1e6) : 0;
  const roi = planMonthly > 0 ? t.cost / planMonthly : 0;
  const costTile = sub
    ? {
        k: L.apiValue[lang],
        val: fmtMoney(t.cost),
        ctx: <span>{roi >= 1 ? `≈ ${roi.toFixed(roi >= 10 ? 0 : 1)}× ${L.planPrice[lang]}` : L.inPlan[lang]}</span>,
      }
    : {
        k: L.estSpend[lang],
        val: fmtMoney(t.cost),
        ctx: (
          <span>
            {fmtMoney(costPer1M)} {L.per1MGen[lang]}
          </span>
        ),
      };
  const tiles: { k: string; val: ReactNode; ctx: ReactNode; sm?: boolean }[] = [
    costTile,
    {
      k: L.sessions[lang],
      val: fmtInt(t.sessions),
      ctx: <span>{fmtTok(Math.round(t.generated_tokens / sessions))} {L.perSession[lang]}</span>,
    },
    {
      k: L.messages[lang],
      val: fmtInt(t.messages),
      ctx: <span>{Math.round(t.messages / sessions)} {L.perSession[lang]}</span>,
    },
    {
      k: L.cacheHit[lang],
      val: (
        <>
          {Math.round(t.cache_hit * 100)}
          <span className="u">%</span>
        </>
      ),
      ctx: <span>{fmtTok(t.cache_read_tokens)} {L.reused[lang]}</span>,
    },
    {
      k: L.activeDays[lang],
      val: (
        <>
          {t.active_days}
          <span className="u">/{t.span_days}</span>
        </>
      ),
      ctx: <span>{peak == null ? "—" : `${L.peak[lang]} ${pad2(peak)}:00`}</span>,
    },
    {
      k: L.topModel[lang],
      sm: true,
      val: prettyModel(t.top_model),
      ctx: top ? (
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span className="ex-sw" style={{ background: `var(--ex-${top.provider})` }} />
          {top.provider} · {fmtTok(top.generated_tokens)}
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
  lang,
}: {
  quota: ProviderUsage[];
  planMonthly: number;
  cost: number;
  lang: Lang;
}): JSX.Element | null {
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
        {L.plansCovered[lang].split("{price}").map((part, index) => (
          <Fragment key={index}>
            {index > 0 && <b>{fmtMoney(planMonthly)}{L.monthly[lang]}</b>}
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
          {fmtMoney(cost)} {L.apiEquiv[lang]} ≈ {mult}× {L.planPrice[lang]}
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
  lang: Lang,
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
      const monthLabels = lang === "zh" ? MONTHS_ZH : lang === "ja" ? MONTHS_JA : MONTHS;
      marks.push({ ci, label: monthLabels[m] ?? "" });
      lastMonth = m;
    }
  }
  return { cells, marks, cols };
}

const COL_W = 16; // 13px cell + 3px gap

function Heatmap({ data, lang }: { data: UsageHistory; lang: Lang }): JSX.Element {
  const { cells, marks, cols } = useMemo(
    () => buildCalendar(data.by_day, lang),
    [data.by_day, lang],
  );
  const peak = data.totals.peak_hour;
  const maxHour = Math.max(1, ...data.by_hour.map((h) => h.messages));
  const winDays = data.window === "1d" ? 1 : data.window === "7d" ? 7 : data.window === "30d" ? 30 : 9999;

  return (
    <div className="panel">
      <div className="panel-h">
        <span className="t">{L.throughput[lang]}</span>
        <span className="meta">
          {data.totals.span_days}d · {L.perDay[lang]}
        </span>
      </div>
      <div className="cal">
        <div className="cal-dows">
          <span>{L.dowMon[lang]}</span>
          <span />
          <span>{L.dowWed[lang]}</span>
          <span />
          <span>{L.dowFri[lang]}</span>
          <span />
          <span>{L.dowSun[lang]}</span>
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
                  data-tip={`${c.date} · ${fmtTok(c.tokens)} ${L.tokens[lang]}`}
                />
              );
            })}
          </div>
        </div>
      </div>
      <div className="cal-foot">
        <div className="cal-legend">
          {L.less[lang]}
          <i style={{ background: "var(--hm-0)" }} />
          <i style={{ background: "var(--hm-1)" }} />
          <i style={{ background: "var(--hm-2)" }} />
          <i style={{ background: "var(--hm-3)" }} />
          <i style={{ background: "var(--hm-4)" }} />
          {L.more[lang]}
        </div>
        <PeakHours hours={data.by_hour} peak={peak} maxHour={maxHour} lang={lang} />
      </div>
    </div>
  );
}

function PeakHours({
  hours,
  peak,
  maxHour,
  lang,
}: {
  hours: UsageHourStat[];
  peak: number | null;
  maxHour: number;
  lang: Lang;
}): JSX.Element {
  return (
    <div className="cal-peak">
      <span>{L.byHour[lang]}</span>
      <div className="bars">
        {hours.map((h) => (
          <i
            key={h.hour}
            className={h.hour === peak ? "peak" : ""}
            style={{ height: `${4 + (h.messages / maxHour) * 14}px` }}
            data-tip={`${pad2(h.hour)}:00 · ${h.messages} ${L.msgs[lang]}`}
          />
        ))}
      </div>
      {peak != null && (
        <span>
          {L.peak[lang]}{" "}
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
  lang,
}: {
  models: UsageModelStat[];
  sub: boolean;
  lang: Lang;
}): JSX.Element {
  const sorted = useMemo(() => [...models].sort((a, b) => b.cost - a.cost), [models]);
  const maxGen = Math.max(1, ...sorted.map((m) => m.generated_tokens));
  const maxReused = Math.max(1, ...sorted.map((m) => m.cache_read_tokens));
  return (
    <div className="panel modelview">
      <div className="panel-h">
        <span className="t">{L.byModel[lang]}</span>
        <div className="mlegend">
          <span className="lg"><i style={{ background: "var(--tk-input)" }} />{L.input[lang]}</span>
          <span className="lg"><i style={{ background: "var(--tk-output)" }} />{L.output[lang]}</span>
          <span className="lg"><i style={{ background: "var(--tk-cw)" }} />{L.cacheWrite[lang]}</span>
          <span className="lg"><i style={{ background: "var(--tk-cr)" }} />{L.cacheRead[lang]}</span>
        </div>
      </div>
      <div className="mnote">
        {L.modelsNote[lang]} {(sub ? L.sortedByValue : L.sortedBySpend)[lang]}.
      </div>
      <div className="mrows">
        {sorted.map((m) => {
          const segs = [
            {
              v: m.input_tokens,
              c: "var(--tk-input)",
              k: m.provider === "codex" ? L.uncachedInput[lang] : L.inputShort[lang],
            },
            { v: m.output_tokens, c: "var(--tk-output)", k: L.outputShort[lang] },
            ...(m.provider === "claude"
              ? [{ v: m.cache_creation_tokens, c: "var(--tk-cw)", k: L.cacheWriteShort[lang] }]
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
                  <span className="bl-label">{L.generatedTrack[lang]}</span>
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
                  <span className="bl-label">{L.reusedTrack[lang]}</span>
                  <div className="bar2">
                    {m.cache_read_tokens > 0 && (
                      <div className="bar2-fill" style={{ width: `${Math.max(6, reusedScale)}%` }}>
                        <i style={{ background: "var(--tk-cr)", flex: 1 }} data-tip={`${L.cacheReadTip[lang]}: ${fmtTok(m.cache_read_tokens)}`} />
                      </div>
                    )}
                  </div>
                  <span
                    className={"bl-hit " + health}
                    data-tip={L.cacheServedTip[lang]
                      .replace("{cached}", fmtTok(m.cache_read_tokens))
                      .replace("{total}", fmtTok(cacheDenom))}
                  >
                    <i className="hdot" />
                    {hitPct}% {L.hitShort[lang]}
                  </span>
                </div>

                <div className="m-meta2">
                  <span><b>{fmtInt(m.messages)}</b> {L.msgs[lang]}</span>
                  <span className="sep">·</span>
                  <span><b>{m.sessions}</b> {L.sess[lang]}</span>
                  <span className="sep">·</span>
                  <span><b>{fmtTok(m.input_tokens)}</b> {L.inputShort[lang]}</span>
                  <span className="sep">·</span>
                  <span><b>{fmtTok(m.output_tokens)}</b> {L.outputShort[lang]}</span>
                  {cw > 0 && (
                    <>
                      <span className="sep">·</span>
                      <span><b>{fmtTok(cw)}</b> {L.cacheWriteShort[lang]}</span>
                    </>
                  )}
                  <span className="sep">·</span>
                  <span><b>{fmtTok(m.cache_read_tokens)}</b> {L.cacheReadShort[lang]}</span>
                  {hasContextTier && (
                    <>
                      <span className="sep">·</span>
                      <span
                        data-tip={L.contextTierTip[lang]
                          .replace("{long}", String(ctx.long))
                          .replace("{short}", String(ctx.short))}
                      >
                        <b>{ctx.long}</b> {L.longContext[lang]}
                      </span>
                    </>
                  )}
                </div>
              </div>

              <div className="m-right">
                <div className="m-num">
                  <div className="big">{fmtTok(m.generated_tokens)}</div>
                  <div className="lbl">{L.generatedShort[lang]}</div>
                </div>
                <div className="m-num m-cost">
                  <div className="big">{fmtMoney(m.cost)}</div>
                  <div className="lbl">{(sub ? L.apiValue : L.estSpend)[lang]}</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
