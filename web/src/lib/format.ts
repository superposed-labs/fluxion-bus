type Translate = (key: string, vars?: Record<string, string | number>) => string;

export function fmtAge(iso: string | null | undefined, t?: Translate): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "—";
  const s = ms / 1000;
  if (s < 60) {
    const value = Math.max(1, Math.floor(s));
    return t ? t("time.secondsAgo", { value }) : `${value}s ago`;
  }
  if (s < 3600) {
    const value = Math.floor(s / 60);
    return t ? t("time.minutesAgo", { value }) : `${value}m ago`;
  }
  if (s < 86400) {
    const value = Math.floor(s / 3600);
    return t ? t("time.hoursAgo", { value }) : `${value}h ago`;
  }
  const value = Math.floor(s / 86400);
  return t ? t("time.daysAgo", { value }) : `${value}d ago`;
}

export function fmtDurBetween(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  if (!start) return "—";
  const endMs = end ? new Date(end).getTime() : Date.now();
  const ms = endMs - new Date(start).getTime();
  if (ms < 0 || Number.isNaN(ms)) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m < 60) return `${m}m ${String(r).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

export function fmtTs(ms: number): string {
  const s = Math.floor(ms / 1000);
  const ds = (ms % 1000).toString().padStart(3, "0");
  const mins = String(Math.floor(s / 60)).padStart(2, "0");
  const secs = String(s % 60).padStart(2, "0");
  return `${mins}:${secs}.${ds.slice(0, 2)}`;
}

export function fmtResetIn(iso: string | null | undefined, t?: Translate): string {
  if (!iso) return "—";
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return "—";
  if (ms <= 0) return t ? t("time.resetsNow") : "resets now";
  const s = Math.floor(ms / 1000);
  if (s < 3600) {
    const value = Math.max(1, Math.floor(s / 60));
    return t ? t("time.resetsInMinutes", { value }) : `resets in ${value}m`;
  }
  if (s < 86400) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const value = m > 0 ? `${h}h${m}m` : `${h}h`;
    return t ? t("time.resetsInHours", { value }) : `resets in ${value}`;
  }
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const value = h > 0 ? `${d}d${h}h` : `${d}d`;
  return t ? t("time.resetsInDays", { value }) : `resets in ${value}`;
}
