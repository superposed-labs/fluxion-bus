import { useCallback, useMemo, useSyncExternalStore } from "react";

import { CHANNELS, EXECUTORS, STATUSES } from "./constants";
import type { UsageWindowKey } from "../types";

/**
 * Everything the console encodes in its URL.
 *
 * The URL is the single source of truth for "what am I looking at" — the
 * view, the filters, and the selected task are all derived from it rather
 * than mirrored into component state. That buys three things at once: a
 * reload keeps the page you were on, the address bar is a shareable link to
 * exactly what you see, and back/forward work without extra bookkeeping.
 *
 * Two write modes, and the distinction matters:
 *
 *   push    — switching the top-level view. A different page; going back
 *             should return to the previous one.
 *   replace — filters and selection. These refine the page you are already
 *             on, they change several times per interaction (chip toggling,
 *             arrow-key cursor movement), and the selection is re-asserted
 *             on every poll. Pushing would fill the history stack with
 *             states the user never deliberately navigated to.
 */

export type AppView = "tasks" | "stats";

/** Which breakdown the usage page shows. */
export type StatsBreakdown = "overview" | "models";

export const USAGE_RANGES: readonly UsageWindowKey[] = ["1d", "7d", "30d", "all"];
const DEFAULT_RANGE: UsageWindowKey = "7d";

export interface Filters {
  status: string[];
  executor: string[];
  channel: string[];
}

export const EMPTY_FILTERS: Filters = { status: [], executor: [], channel: [] };

const VIEW_PARAM = "view";
const TASK_PARAM = "task";
const BREAKDOWN_PARAM = "breakdown";
const RANGE_PARAM = "range";
const FILTER_PARAM: Record<keyof Filters, string> = {
  status: "status",
  executor: "executor",
  channel: "channel",
};

// Values a filter param is allowed to hold. Anything else in the URL is
// dropped rather than trusted: a stale or hand-edited link must not be able
// to install a filter the UI has no chip for, which would hide tasks with no
// visible way to clear it. Filtering the vocabulary (rather than the raw
// input) also gives the URL a stable canonical order.
const FILTER_VOCABULARY: Record<keyof Filters, readonly string[]> = {
  status: STATUSES,
  executor: EXECUTORS,
  channel: CHANNELS,
};

const FILTER_KEYS = ["status", "executor", "channel"] as const;

const NAV_EVENT = "fluxion:navigate";

function renderSearch(params: URLSearchParams): string {
  const query = params.toString();
  return query ? `?${query}` : "";
}

// ── pure URL ⇄ state mapping ────────────────────────────────────────
// Kept free of window access so the round-trip is testable in isolation.

export function viewFromSearch(search: string): AppView {
  return new URLSearchParams(search).get(VIEW_PARAM) === "stats" ? "stats" : "tasks";
}

/** "tasks" is the default, so it drops the param rather than spelling it out. */
export function searchWithView(search: string, view: AppView): string {
  const params = new URLSearchParams(search);
  if (view === "stats") params.set(VIEW_PARAM, "stats");
  else params.delete(VIEW_PARAM);
  return renderSearch(params);
}

export function filtersFromSearch(search: string): Filters {
  const params = new URLSearchParams(search);
  const read = (key: keyof Filters): string[] => {
    const raw = params.getAll(FILTER_PARAM[key]);
    return FILTER_VOCABULARY[key].filter((value) => raw.includes(value));
  };
  return { status: read("status"), executor: read("executor"), channel: read("channel") };
}

/**
 * Multi-valued filters repeat the param (`?status=FAILED&status=RUNNING`)
 * instead of joining with a separator, so the query string stays readable
 * rather than turning every comma into `%2C`.
 */
export function searchWithFilters(search: string, filters: Filters): string {
  const params = new URLSearchParams(search);
  for (const key of FILTER_KEYS) {
    params.delete(FILTER_PARAM[key]);
    for (const value of FILTER_VOCABULARY[key]) {
      if (filters[key].includes(value)) params.append(FILTER_PARAM[key], value);
    }
  }
  return renderSearch(params);
}

export function breakdownFromSearch(search: string): StatsBreakdown {
  return new URLSearchParams(search).get(BREAKDOWN_PARAM) === "models" ? "models" : "overview";
}

export function searchWithBreakdown(search: string, breakdown: StatsBreakdown): string {
  const params = new URLSearchParams(search);
  if (breakdown === "models") params.set(BREAKDOWN_PARAM, "models");
  else params.delete(BREAKDOWN_PARAM);
  return renderSearch(params);
}

export function rangeFromSearch(search: string): UsageWindowKey {
  const raw = new URLSearchParams(search).get(RANGE_PARAM);
  return USAGE_RANGES.find((value) => value === raw) ?? DEFAULT_RANGE;
}

export function searchWithRange(search: string, range: UsageWindowKey): string {
  const params = new URLSearchParams(search);
  if (range !== DEFAULT_RANGE) params.set(RANGE_PARAM, range);
  else params.delete(RANGE_PARAM);
  return renderSearch(params);
}

export function selectedTaskFromSearch(search: string): string | null {
  return new URLSearchParams(search).get(TASK_PARAM) || null;
}

export function searchWithSelectedTask(search: string, taskId: string | null): string {
  const params = new URLSearchParams(search);
  if (taskId) params.set(TASK_PARAM, taskId);
  else params.delete(TASK_PARAM);
  return renderSearch(params);
}

// ── plumbing ────────────────────────────────────────────────────────

// history.pushState/replaceState fire no event of their own, so navigations
// we initiate are announced on the window alongside the back/forward ones.
function subscribe(onChange: () => void): () => void {
  window.addEventListener("popstate", onChange);
  window.addEventListener(NAV_EVENT, onChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener(NAV_EVENT, onChange);
  };
}

function commit(nextSearch: string, mode: "push" | "replace"): void {
  const { pathname, hash, search } = window.location;
  if (nextSearch === search) return;
  const target = `${pathname}${nextSearch}${hash}`;
  if (mode === "replace") window.history.replaceState(null, "", target);
  else window.history.pushState(null, "", target);
  window.dispatchEvent(new Event(NAV_EVENT));
}

/**
 * The live query string. A plain string keeps the snapshot referentially
 * stable, which `useSyncExternalStore` requires — parsed objects are derived
 * from it with `useMemo` instead.
 */
function useSearch(): string {
  return useSyncExternalStore(
    subscribe,
    () => window.location.search,
    () => "",
  );
}

// ── hooks ───────────────────────────────────────────────────────────

export function useView(): [AppView, (next: AppView) => void] {
  const search = useSearch();
  const view = useMemo(() => viewFromSearch(search), [search]);

  // Reading the current URL rather than closing over `search` keeps the
  // setter stable and immune to stale closures.
  const setView = useCallback((next: AppView) => {
    commit(searchWithView(window.location.search, next), "push");
  }, []);

  return [view, setView];
}

export function useFilters(): [Filters, (updater: (prev: Filters) => Filters) => void] {
  const search = useSearch();
  const filters = useMemo(() => filtersFromSearch(search), [search]);

  const setFilters = useCallback((updater: (prev: Filters) => Filters) => {
    const current = window.location.search;
    commit(searchWithFilters(current, updater(filtersFromSearch(current))), "replace");
  }, []);

  return [filters, setFilters];
}

/**
 * The usage page's own controls. Both are sub-views of `?view=stats` rather
 * than page changes, so they replace — you should not have to press back
 * four times to leave the usage page after comparing a few ranges.
 */
export function useBreakdown(): [StatsBreakdown, (next: StatsBreakdown) => void] {
  const search = useSearch();
  const breakdown = useMemo(() => breakdownFromSearch(search), [search]);

  const setBreakdown = useCallback((next: StatsBreakdown) => {
    commit(searchWithBreakdown(window.location.search, next), "replace");
  }, []);

  return [breakdown, setBreakdown];
}

export function useUsageRange(): [UsageWindowKey, (next: UsageWindowKey) => void] {
  const search = useSearch();
  const range = useMemo(() => rangeFromSearch(search), [search]);

  const setRange = useCallback((next: UsageWindowKey) => {
    commit(searchWithRange(window.location.search, next), "replace");
  }, []);

  return [range, setRange];
}

export function useSelectedTask(): [string | null, (next: string | null) => void] {
  const search = useSearch();
  const selectedId = useMemo(() => selectedTaskFromSearch(search), [search]);

  const setSelectedId = useCallback((next: string | null) => {
    commit(searchWithSelectedTask(window.location.search, next), "replace");
  }, []);

  return [selectedId, setSelectedId];
}
