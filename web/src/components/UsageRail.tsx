import { fmtAge, fmtResetIn } from "../lib/format";
import { useI18n } from "../i18n";
import type { ProviderUsage, UsageWindow } from "../types";

const PROVIDER_LABEL: Record<string, string> = {
  claude: "Claude",
  codex: "Codex",
  antigravity: "Antigravity",
};

// Color the consumption bar by how much is already used.
function barColor(usedPct: number): string {
  if (usedPct >= 85) return "var(--st-failed)";
  if (usedPct >= 60) return "var(--accent)";
  return "var(--st-returned)";
}

function clampPct(n: number): number {
  return Math.min(100, Math.max(0, n));
}

// Model-scoped sub-limits (e.g. Claude's Fable weekly cap) — carved out of the
// weekly window, rendered as an indented child row rather than a peer window.
function isScopedWindow(w: UsageWindow): boolean {
  return w.key.startsWith("scoped_");
}

interface UsageRailProps {
  usage: ProviderUsage[];
}

export function UsageRail({ usage }: UsageRailProps): JSX.Element | null {
  const { t } = useI18n();
  if (!usage || usage.length === 0) return null;

  return (
    <div className="rail-section">
      <div className="rail-h">
        <span>{t("rail.modelQuota")}</span>
        <span className="meta">
          {usage.length} {t(usage.length === 1 ? "common.provider" : "common.providers")}
        </span>
      </div>
      <div className="quota-list">
        {usage.map((p) => (
          <div className="quota-prov" key={p.provider}>
            <div className="quota-prov-head">
              <span className="quota-prov-name">
                {PROVIDER_LABEL[p.provider] ?? p.provider}
              </span>
              {p.account_label ? <span className="quota-tag">{p.account_label}</span> : null}
              <span style={{ flex: 1 }} />
              <span className="quota-age" data-st={p.status}>
                {p.status === "ok" ? fmtAge(p.fetched_at, t) : p.status}
              </span>
            </div>
            {p.status === "ok" && visibleWindows(p).length > 0 ? (
              visibleWindows(p).map((w) => <QuotaWindowRow key={w.key} w={w} />)
            ) : (
              <div className="quota-note">{p.detail || t("common.unavailable")}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function visibleWindows(provider: ProviderUsage): UsageWindow[] {
  return provider.windows;
}

function fmtCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
  return `${n}`;
}

function formatCreditAmount(w: UsageWindow, locale: string): string {
  if (w.remaining == null) return "—";
  if (w.currency) {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency: w.currency,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(w.remaining);
  }
  return fmtCount(w.remaining);
}

function formatCreditExpiry(iso: string | null | undefined, locale: string): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric" }).format(date);
}

function QuotaWindowRow({ w }: { w: UsageWindow }): JSX.Element {
  const { locale, t } = useI18n();
  const used = w.used_percent;
  const scoped = isScopedWindow(w);

  // Pure credit balance (Antigravity AI Credits): a raw count, no bar/clock.
  if (used == null && w.total == null && w.remaining != null) {
    const expiry = formatCreditExpiry(w.expires_at, locale);
    return (
      <div className="quota-row">
        <div className="quota-row-top">
          <span className="quota-w-label">{w.label}</span>
          <span className="quota-w-left">
            {formatCreditAmount(w, locale)}{!w.currency ? ` ${t("common.credits")}` : ""}
          </span>
        </div>
        {expiry ? <div className="quota-reset">{t("rail.exp", { date: expiry })}</div> : null}
      </div>
    );
  }

  const usedNum = used ?? 0;
  // Credit pool with a cap → "remaining / total"; otherwise a rolling window
  // (Claude/Codex 5h·weekly, or Antigravity per-model) → "% left" + reset.
  const isCappedCredits = w.total != null;
  const remainingPct = used == null ? null : Math.max(0, 100 - used);
  const rightLabel = isCappedCredits
    ? `${fmtCount(w.remaining ?? 0)} / ${fmtCount(w.total ?? 0)}`
    : remainingPct == null
      ? "—"
      : `${Math.round(remainingPct)}% ${t("common.left")}`;
  return (
    <div className={scoped ? "quota-row scoped" : "quota-row"}>
      <div className="quota-row-top">
        <span className="quota-w-label">
          {w.label}
          {scoped && usedNum >= 100 ? <span className="quota-scoped-chip">{t("rail.scopedCapped")}</span> : null}
        </span>
        <span className="quota-w-left">{rightLabel}</span>
      </div>
      <div className="quota-bar" title={remainingPct == null ? t("common.unknown") : `${Math.round(remainingPct)}% ${t("common.remaining")}`}>
        <i
          className="quota-fill"
          style={{ width: `${clampPct(remainingPct ?? 0)}%`, background: barColor(usedNum) }}
        />
      </div>
      <div className="quota-reset">{isCappedCredits ? t("common.monthly") : fmtResetIn(w.resets_at, t)}</div>
    </div>
  );
}
