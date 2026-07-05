import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  CHANNELS,
  EXECUTORS,
  FILTER_STATUSES,
  STATUSES,
  executorVar,
  statusVar,
} from "../lib/constants";
import { channelLabel } from "../lib/channels";
import { useI18n } from "../i18n";
import type { Task } from "../types";

export interface Filters {
  status: string[];
  executor: string[];
  channel: string[];
}

interface FilterStripProps {
  tasks: Task[];
  filters: Filters;
  setFilters: (updater: (prev: Filters) => Filters) => void;
  grouped: boolean;
  onToggleGroup: () => void;
}

export function FilterStrip({
  tasks,
  filters,
  setFilters,
  grouped,
  onToggleGroup,
}: FilterStripProps): JSX.Element {
  const { t } = useI18n();
  const chipsRef = useRef<HTMLDivElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [edge, setEdge] = useState({ left: false, right: false });
  const byStatus = useMemo(() => {
    const acc: Record<string, number> = Object.fromEntries(STATUSES.map((s) => [s, 0]));
    for (const t of tasks) acc[t.status] = (acc[t.status] ?? 0) + 1;
    return acc;
  }, [tasks]);
  const byExec = useMemo(() => {
    const acc: Record<string, number> = Object.fromEntries(EXECUTORS.map((s) => [s, 0]));
    for (const t of tasks) acc[t.executor] = (acc[t.executor] ?? 0) + 1;
    return acc;
  }, [tasks]);
  const byChan = useMemo(() => {
    const acc: Record<string, number> = Object.fromEntries(CHANNELS.map((s) => [s, 0]));
    for (const t of tasks) acc[t.channel] = (acc[t.channel] ?? 0) + 1;
    return acc;
  }, [tasks]);

  const toggle = (key: keyof Filters, val: string) => {
    setFilters((prev) => {
      const cur = prev[key];
      const next = cur.includes(val) ? cur.filter((v) => v !== val) : [...cur, val];
      return { ...prev, [key]: next };
    });
  };
  const clearFilters = () => setFilters(() => ({ status: [], executor: [], channel: [] }));
  const hiddenStatusCount = filters.status.filter(
    (status) => !FILTER_STATUSES.includes(status as (typeof FILTER_STATUSES)[number]),
  ).length;
  const secondaryCount = hiddenStatusCount + filters.executor.length + filters.channel.length;
  const anyFiltersActive = filters.status.length + secondaryCount > 0;

  const updateEdge = useCallback(() => {
    const el = chipsRef.current;
    if (!el) return;
    setEdge({
      left: el.scrollLeft > 2,
      right: el.scrollLeft + el.clientWidth < el.scrollWidth - 2,
    });
  }, []);

  useEffect(() => {
    const el = chipsRef.current;
    if (!el) return;
    updateEdge();
    const onWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      if (el.scrollWidth <= el.clientWidth) return;
      el.scrollLeft += event.deltaY;
      event.preventDefault();
    };
    el.addEventListener("scroll", updateEdge, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("resize", updateEdge);
    return () => {
      el.removeEventListener("scroll", updateEdge);
      el.removeEventListener("wheel", onWheel);
      window.removeEventListener("resize", updateEdge);
    };
  }, [updateEdge]);

  useEffect(() => {
    if (!filtersOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!popoverRef.current?.contains(event.target as Node)) {
        setFiltersOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFiltersOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [filtersOpen]);

  return (
    <div className="filter-strip">
      <div
        className="filter-chips"
        ref={chipsRef}
        data-edge-l={edge.left}
        data-edge-r={edge.right}
      >
        <div className="chip-group">
          {FILTER_STATUSES.map((s) => (
            <button
              key={s}
              type="button"
              className={"chip statuschip" + (filters.status.includes(s) ? " on" : "")}
              onClick={() => toggle("status", s)}
              title={`${t(`status.${s}`)} · ${byStatus[s] ?? 0}`}
            >
              <span className="dot" style={{ background: statusVar(s) }} />
              <span className="statuschip-label">{t(`status.${s}`)}</span>
              <span className="count">{byStatus[s] ?? 0}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="filter-pop-wrap" ref={popoverRef}>
        <button
          type="button"
          className={
            "chip filterbtn toolbtn" +
            (filtersOpen ? " open" : "") +
            (secondaryCount > 0 ? " has" : "")
          }
          onClick={() => setFiltersOpen((open) => !open)}
          aria-expanded={filtersOpen}
          aria-label={t("filters.more")}
          title={t("filters.more")}
        >
          <svg viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path
              d="M1.5 3h11M3.5 7h7M5.5 11h3"
              stroke="currentColor"
              strokeLinecap="round"
              strokeWidth="1.3"
            />
          </svg>
          {secondaryCount > 0 && <span className="filterbtn-badge">{secondaryCount}</span>}
          <span className="filterbtn-caret" aria-hidden="true">
            ▾
          </span>
        </button>
        {filtersOpen && (
          <div className="filter-pop" role="dialog" aria-label={t("filters.more")}>
            <div className="fp-section">
              <div className="fp-h">{t("filters.status")}</div>
              <div className="fp-chips">
                {STATUSES.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={"chip" + (filters.status.includes(s) ? " on" : "")}
                    onClick={() => toggle("status", s)}
                  >
                    <span className="dot" style={{ background: statusVar(s) }} />
                    {t(`status.${s}`)}
                    <span className="count">{byStatus[s] ?? 0}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="fp-section">
              <div className="fp-h">{t("detail.executor")}</div>
              <div className="fp-chips">
                {EXECUTORS.map((e) => (
                  <button
                    key={e}
                    type="button"
                    className={"chip" + (filters.executor.includes(e) ? " on" : "")}
                    onClick={() => toggle("executor", e)}
                  >
                    <span className="dot" style={{ background: executorVar(e) }} />
                    {e}
                    <span className="count">{byExec[e] ?? 0}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="fp-section">
              <div className="fp-h">{t("detail.channel")}</div>
              <div className="fp-chips">
                {CHANNELS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={"chip" + (filters.channel.includes(s) ? " on" : "")}
                    onClick={() => toggle("channel", s)}
                  >
                    {channelLabel(s, t)}
                    <span className="count">{byChan[s] ?? 0}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="fp-foot">
              <button
                type="button"
                className="fp-clear"
                disabled={!anyFiltersActive}
                onClick={clearFilters}
              >
                {t("filters.clear")}
              </button>
            </div>
          </div>
        )}
      </div>

      <button
        type="button"
        className={"chip groupchip toolbtn" + (grouped ? " on" : "")}
        onClick={onToggleGroup}
        aria-pressed={grouped}
        aria-label={t("filters.groupByConversation")}
        title={t("filters.groupByConversation")}
      >
        <svg viewBox="0 0 14 14" fill="none" aria-hidden="true">
          <rect x="1.5" y="2" width="11" height="2.2" rx="0.6" fill="currentColor" />
          <rect x="3.5" y="6" width="9" height="2" rx="0.6" fill="currentColor" opacity="0.72" />
          <rect x="3.5" y="9.5" width="9" height="2" rx="0.6" fill="currentColor" opacity="0.72" />
        </svg>
      </button>
    </div>
  );
}
