/* ───────────────────────── cron utilities ─────────────────────────── */
export const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function parseField(f: string, min: number, max: number): number[] | null {
  if (f === "*") return null; // null = every value
  const out = new Set<number>();
  for (const part of f.split(",")) {
    let m;
    if ((m = part.match(/^(\*|\d+)(?:-(\d+))?(?:\/(\d+))?$/))) {
      let lo: number;
      let hi: number;
      const step = m[3] ? parseInt(m[3]!, 10) : 1;
      if (m[1] === "*") {
        lo = min;
        hi = max;
      } else {
        lo = parseInt(m[1]!, 10);
        hi = m[2] != null ? parseInt(m[2]!, 10) : (m[3] ? max : lo);
      }
      if (Number.isNaN(lo) || Number.isNaN(hi) || lo < min || hi > max || lo > hi || step < 1) {
        throw new Error();
      }
      for (let v = lo; v <= hi; v += step) out.add(v);
    } else {
      throw new Error();
    }
  }
  return Array.from(out).sort((a, b) => a - b);
}

export interface ParsedCron {
  valid: boolean;
  min?: number[] | null;
  hr?: number[] | null;
  dom?: number[] | null;
  mon?: number[] | null;
  dow?: number[] | null;
}

export function parseCron(expr: string): ParsedCron {
  const p = expr.trim().split(/\s+/);
  if (p.length !== 5) return { valid: false };
  try {
    return {
      valid: true,
      min: parseField(p[0]!, 0, 59),
      hr: parseField(p[1]!, 0, 23),
      dom: parseField(p[2]!, 1, 31),
      mon: parseField(p[3]!, 1, 12),
      dow: parseField(p[4]!, 0, 6),
    };
  } catch (e) {
    return { valid: false };
  }
}

export const inSet = (set: number[] | null | undefined, v: number): boolean =>
  set === null || set === undefined || set.includes(v);

export function nextRuns(expr: string, from: Date, n: number): Date[] {
  const c = parseCron(expr);
  if (!c.valid || c.dom === undefined || c.dow === undefined || c.mon === undefined || c.hr === undefined || c.min === undefined) return [];
  const res: Date[] = [];
  const d = new Date(from.getTime());
  d.setSeconds(0, 0);
  d.setMinutes(d.getMinutes() + 1);
  const domR = c.dom !== null,
    dowR = c.dow !== null;
  for (let i = 0; i < 366 * 24 * 60 && res.length < n; i++) {
    const okMon = inSet(c.mon, d.getMonth() + 1);
    const okHr = inSet(c.hr, d.getHours());
    const okMin = inSet(c.min, d.getMinutes());
    let okDay;
    if (domR && dowR) {
      okDay = (c.dom !== null && c.dom.includes(d.getDate())) || (c.dow !== null && c.dow.includes(d.getDay()));
    } else {
      okDay = inSet(c.dom, d.getDate()) && inSet(c.dow, d.getDay());
    }
    if (okMon && okDay && okHr && okMin) res.push(new Date(d.getTime()));
    d.setMinutes(d.getMinutes() + 1);
  }
  return res;
}

export const pad = (n: number): string => String(n).padStart(2, "0");

type Translate = (key: string, vars?: Record<string, string | number>) => string;

function ordinalSuffix(day: number): string {
  if (day >= 11 && day <= 13) return "th";
  switch (day % 10) {
    case 1:
      return "st";
    case 2:
      return "nd";
    case 3:
      return "rd";
    default:
      return "th";
  }
}

export function describeCron(expr: string, t?: Translate): string | null {
  const c = parseCron(expr);
  if (!c.valid) return null;
  const single = (s: number[] | null | undefined) =>
    s !== null && s !== undefined && s.length === 1 ? s[0] : null;
  const hr = single(c.hr),
    mn = single(c.min);
  const timePart =
    hr != null && mn != null
      ? `${pad(hr)}:${pad(mn)}`
      : c.min !== null && c.min !== undefined && c.min.length === 1 && c.hr === null
      ? t
        ? t("cron.minuteEveryHour", { minute: pad(mn!) })
        : `:${pad(mn!)} every hour`
      : null;

  if (c.dow !== null && c.dow !== undefined && c.dom === null && c.mon === null && hr != null && mn != null) {
    const time = timePart ?? `${pad(hr)}:${pad(mn)}`;
    const days = c.dow.map((d) => (t ? t(`cron.day.${d}`) : DOW[d])).join(", ");
    if (c.dow.length === 7) return t ? t("cron.everyDayAt", { time }) : `Every day at ${time}`;
    if (c.dow.length === 5 && c.dow.join() === "1,2,3,4,5") {
      return t ? t("cron.everyWeekdayAt", { time }) : `Every weekday at ${time}`;
    }
    if (c.dow.length === 2 && c.dow.join() === "0,6") {
      return t ? t("cron.everyWeekendAt", { time }) : `Every weekend at ${time}`;
    }
    return t ? t("cron.everyDaysAt", { days, time }) : `Every ${days} at ${time}`;
  }
  if (c.dow === null && c.dom === null && c.mon === null && hr != null && mn != null) {
    const time = timePart ?? `${pad(hr)}:${pad(mn)}`;
    return t ? t("cron.everyDayAt", { time }) : `Every day at ${time}`;
  }
  if (c.dom !== null && c.dom !== undefined && c.dom.length === 1 && c.dow === null && hr != null && mn != null) {
    const time = timePart ?? `${pad(hr)}:${pad(mn)}`;
    const dd = c.dom[0]!;
    const suffix = ordinalSuffix(dd);
    return t ? t("cron.monthlyDayAt", { day: dd, suffix, time }) : `On the ${dd}${suffix} of each month at ${time}`;
  }
  if (c.hr === null && mn != null && c.dom === null && c.dow === null) {
    return t ? t("cron.everyHourAt", { minute: pad(mn) }) : `Every hour at :${pad(mn)}`;
  }
  if (c.hr === null && c.min !== null && c.min !== undefined && c.min.length > 1) {
    const minArr = c.min;
    const diffs = minArr.slice(1).map((v, i) => v - minArr[i]!);
    if (diffs.every((x) => x === diffs[0])) {
      return t ? t("cron.everyMinutes", { minutes: diffs[0]! }) : `Every ${diffs[0]} minutes`;
    }
  }
  return null;
}

export function relFuture(date: Date, now: Date): string {
  let s = Math.round((date.getTime() - now.getTime()) / 1000);
  if (s < 0) return "now";
  const d = Math.floor(s / 86400);
  s -= d * 86400;
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return "<1m";
}

export function relPast(date: Date, now: Date): string {
  let s = Math.round((now.getTime() - date.getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export function fmtRun(date: Date): string {
  return `${MON[date.getMonth()]} ${date.getDate()} · ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
