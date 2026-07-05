import { useEffect, useRef, useState } from "react";

import { statusLabel } from "../lib/constants";
import { fmtAge, fmtClock, fmtDurBetween, fmtTs } from "../lib/format";
import { useI18n } from "../i18n";
import type { LogLine, Task } from "../types";
import { MarkdownText } from "./MarkdownText";
import { ChannelPill, ExecutorPill, StatusBadge } from "./Pills";

interface DetailPaneProps {
  task: Task | null;
  onRerun?: (task: Task) => void;
  onContinue?: (task: Task) => void;
}

type TabId = "summary" | "stdout" | "stderr" | "diff" | "files" | "meta";

interface TabSpec {
  id: TabId;
  label: string;
  count?: number;
}

export function DetailPane({ task, onRerun, onContinue }: DetailPaneProps): JSX.Element {
  const { t } = useI18n();
  const [tab, setTab] = useState<TabId>("summary");

  useEffect(() => {
    setTab("summary");
  }, [task?.task_id]);

  if (!task) {
    return (
      <div className="pane">
        <div className="empty">
          {t("detail.selectTask")}
          <div className="mark">{t("detail.navigateHint")}</div>
        </div>
      </div>
    );
  }

  const isLive = task.status === "RUNNING" || task.status === "RETRYING";
  const hunks = task.diff_hunks ?? {};

  const tabs: TabSpec[] = [
    { id: "summary", label: t("detail.tab.summary") },
    { id: "stdout", label: t("detail.tab.stdout"), count: task.stdout.length },
    { id: "stderr", label: t("detail.tab.stderr"), count: task.stderr.length },
    {
      id: "diff",
      label: t("detail.tab.diff"),
      count: task.changed_files.filter((f) => hunks[f.path]).length,
    },
    { id: "files", label: t("detail.tab.files"), count: task.changed_files.length },
    { id: "meta", label: t("detail.tab.meta") },
  ];

  return (
    <div className="pane">
      <DetailHeader task={task} onRerun={onRerun} onContinue={onContinue} />
      <div className="tabs">
        {tabs.map((tt) => (
          <button
            key={tt.id}
            type="button"
            className={"tab" + (tab === tt.id ? " on" : "")}
            onClick={() => setTab(tt.id)}
          >
            {tt.label}
            {tt.count != null && <span className="num">{tt.count}</span>}
          </button>
        ))}
      </div>
      {tab === "summary" && <SummaryTab task={task} />}
      {tab === "stdout" && (
        <ConsoleTab lines={task.stdout} isLive={isLive} emptyHint={t("detail.noStdout")} />
      )}
      {tab === "stderr" && (
        <ConsoleTab lines={task.stderr} isLive={false} emptyHint={t("detail.noStderr")} />
      )}
      {tab === "diff" && <DiffTab task={task} />}
      {tab === "files" && <FilesTab task={task} />}
      {tab === "meta" && <MetaTab task={task} />}
    </div>
  );
}

function DetailHeader({
  task,
  onRerun,
  onContinue,
}: {
  task: Task;
  onRerun?: (task: Task) => void;
  onContinue?: (task: Task) => void;
}): JSX.Element {
  const { t } = useI18n();
  const isLive = task.status === "RUNNING" || task.status === "RETRYING";
  return (
    <div className="detail-head">
      <div className="detail-crumb">
        <b>{task.task_id}</b>
        <span className="sep">›</span>
        <span>{t("detail.conversation")}</span>
        <span className="sep">›</span>
        <b>{task.conversation_key}</b>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 16,
        }}
      >
        <div className="detail-title" style={{ flex: 1 }}>
          <MarkdownText
            text={task.summary}
            empty={<span className="muted">{t("common.noSummary")}</span>}
          />
        </div>
        <div className="detail-actions">
          {onContinue && (
            <button
              type="button"
              className="dh-act"
              onClick={() => onContinue(task)}
              title={t("run.continueTitle")}
            >
              <svg viewBox="0 0 16 16" fill="none"><path d="M3 5.5h7.5M3 8h6M3 10.5h7.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /><path d="M12 6.5 14 8l-2 1.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
              {t("run.continue")}
            </button>
          )}
          {onRerun && (
            <button
              type="button"
              className="dh-act"
              onClick={() => onRerun(task)}
              title={t("run.rerunTitle")}
            >
              <svg viewBox="0 0 16 16" fill="none"><path d="M13 8a5 5 0 1 1-1.46-3.54M13 3.2V5.4H10.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
              {t("run.rerun")}
            </button>
          )}
          <StatusBadge status={task.status} />
        </div>
      </div>
      <div className="detail-meta">
        <div className="dm">
          <span className="dm-lbl">{t("detail.executor")}</span>
          <ExecutorPill executor={task.executor} />
        </div>
        <div className="dm">
          <span className="dm-lbl">{t("detail.model")}</span>
          <span className="dm-val copy" title="model">
            {task.model || t("detail.modelDefault")}
          </span>
        </div>
        <div className="dm">
          <span className="dm-lbl">{t("detail.channel")}</span>
          <ChannelPill channel={task.channel} meta={task.channel_meta} />
        </div>
        <div className="dm">
          <span className="dm-lbl">{t("detail.session")}</span>
          <span className="dm-val copy" title="executor_session_id">
            {task.executor_session_id || "—"}
          </span>
        </div>
        <div className="dm">
          <span className="dm-lbl">{t("detail.convKey")}</span>
          <span className="dm-val copy" title="conversation_key">
            {task.conversation_key}
          </span>
        </div>
        <div className="dm">
          <span className="dm-lbl">{t("detail.received")}</span>
          <span className="dm-val">{fmtAge(task.timestamp.received_at, t)}</span>
        </div>
        <div className="dm">
          <span className="dm-lbl">{isLive ? t("detail.running") : t("detail.duration")}</span>
          <span className="dm-val">
            {task.timestamp.started_at
              ? fmtDurBetween(task.timestamp.started_at, isLive ? null : task.timestamp.ended_at)
              : "—"}
          </span>
        </div>
        {task.exit_code != null && (
          <div className="dm">
            <span className="dm-lbl">{t("detail.exit")}</span>
            <span className="dm-val" style={{ color: "var(--st-failed)" }}>
              {task.exit_code}
            </span>
          </div>
        )}
        {task.status === "RETRYING" && task.attempt != null && (
          <div className="dm">
            <span className="dm-lbl">{t("detail.attempt")}</span>
            <span className="dm-val" style={{ color: "var(--st-retrying)" }}>
              {task.attempt}
              {task.max_attempts ? ` / ${task.max_attempts}` : ""}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

interface LifecycleStep {
  id: string;
  label: string;
  at: string | null;
  end?: "ok" | "fail" | "cancel" | null;
}

function SummaryTab({ task }: { task: Task }): JSX.Element {
  const { t } = useI18n();
  const isLive = task.status === "RUNNING" || task.status === "RETRYING";
  const baseSteps: LifecycleStep[] = [
    { id: "RECEIVED", label: t("status.RECEIVED"), at: task.timestamp.received_at },
    { id: "VALIDATED", label: t("status.VALIDATED"), at: task.timestamp.validated_at },
    { id: "QUEUED", label: t("status.QUEUED"), at: task.timestamp.queued_at },
    { id: "STARTED", label: t("detail.step.STARTED"), at: task.timestamp.started_at },
  ];

  let terminal: LifecycleStep;
  switch (task.status) {
    case "RETURNED":
      terminal = { id: "RETURNED", label: t("status.RETURNED"), at: task.timestamp.ended_at, end: "ok" };
      break;
    case "FAILED":
      terminal = { id: "FAILED", label: t("status.FAILED"), at: task.timestamp.ended_at, end: "fail" };
      break;
    case "CANCELED":
      terminal = { id: "CANCELED", label: t("status.CANCELED"), at: task.timestamp.ended_at, end: "cancel" };
      break;
    case "RETRYING":
      terminal = { id: "RETRYING", label: t("status.RETRYING"), at: task.timestamp.started_at, end: null };
      break;
    default:
      terminal = { id: "ENDED", label: t("detail.step.ENDED"), at: null };
  }

  const steps = [...baseSteps, terminal];
  const logFileName = task.log_file.replace(/^.*\//, "");

  return (
    <div className="tab-body scroll">
      <div className="summary">
        <div className="sum-prose">
          <MarkdownText
            text={task.summary}
            empty={<span className="muted">{t("common.noSummary")}</span>}
          />
        </div>

        <div className="sum-grid">
          <div className="sum-tile">
            <span className="lbl">{t("detail.filesTouched")}</span>
            <span className="val">{task.diff_summary.files}</span>
            <span className="sub">
              {task.changed_files.length === 0
                ? t("detail.noEdits")
                : t(task.changed_files.length === 1 ? "detail.changeCount" : "detail.changeCountPlural", { count: task.changed_files.length })}
            </span>
          </div>
          <div className="sum-tile">
            <span className="lbl">{t("detail.lineDelta")}</span>
            <span className="val">
              <span className="add">+{task.diff_summary.additions}</span>{" "}
              <span className="del">−{task.diff_summary.deletions}</span>
            </span>
            <span className="sub">{t("detail.acrossChangedFiles")}</span>
          </div>
          <div className="sum-tile">
            <span className="lbl">{t("common.artifacts")}</span>
            <span className="val">{task.artifacts.length}</span>
            <span className="sub">
              {task.artifacts.length === 0
                ? t("detail.noneEmitted")
                : task.artifacts.map((a) => a.kind).join(" · ")}
            </span>
          </div>
          <div className="sum-tile">
            <span className="lbl">{t("detail.logFile")}</span>
            <span className="val" style={{ fontSize: 12, wordBreak: "break-all" }}>
              {logFileName}
            </span>
            <span
              className="sub mono"
              style={{ fontSize: 10.5, color: "var(--fg-3)" }}
            >
              {task.log_file}
            </span>
          </div>
        </div>

        <div>
          <div className="sum-section-h">
              <span>{t("detail.lifecycle")}</span>
            <span
              className="mono muted"
              style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}
            >
              {isLive
                ? t("detail.inProgress")
                : task.timestamp.ended_at
                  ? t("detail.endedAgo", { age: fmtAge(task.timestamp.ended_at, t) })
                  : "—"}
            </span>
          </div>
          <div className="lifecycle">
            {steps.map((s) => (
              <div
                key={s.id}
                className="lcstep"
                data-on={s.at ? "1" : "0"}
                data-end={s.end || ""}
              >
                <span className="lc-lbl">{s.label}</span>
                <span className="lc-time">{s.at ? fmtClock(s.at) : "—"}</span>
              </div>
            ))}
          </div>
        </div>

        {task.changed_files.length > 0 && (
          <div>
            <div className="sum-section-h">
              <span>{t("detail.changedFiles")}</span>
              <span
                className="mono muted"
                style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}
              >
                {task.changed_files.length}
              </span>
            </div>
            <div className="sum-list">
              {task.changed_files.map((f, i) => (
                <div key={`${f.path}-${i}`} className="sum-list-row">
                  <span className="icon" data-op={f.op}>
                    {f.op}
                  </span>
                  <span className="path">{f.path}</span>
                  <span className="changes">
                    <span className="add">+{f.additions}</span>
                    <span className="del">−{f.deletions}</span>
                  </span>
                  <span className="size" />
                </div>
              ))}
            </div>
          </div>
        )}

        {task.artifacts.length > 0 && (
          <div>
            <div className="sum-section-h">
              <span>{t("common.artifacts")}</span>
              <span
                className="mono muted"
                style={{ textTransform: "none", letterSpacing: 0, fontWeight: 400 }}
              >
                {task.artifacts.length}
              </span>
            </div>
            <div className="sum-list">
              {task.artifacts.map((a, i) => (
                <div key={`${a.path}-${i}`} className="sum-list-row">
                  <span className="icon" data-kind={a.kind} title={a.kind}>
                    {a.kind.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="path">{a.name}</span>
                  <span className="size mono">{a.size}</span>
                  <span className="size mono">{a.kind}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div>
          <div className="sum-section-h">
            <span>{t("detail.identifiers")}</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <KvRow k="task_id" v={task.task_id} />
            <KvRow k="conversation_key" v={task.conversation_key} />
            <KvRow k="executor_session_id" v={task.executor_session_id ?? "—"} />
          </div>
        </div>
      </div>
    </div>
  );
}

function KvRow({ k, v }: { k: string; v: string }): JSX.Element {
  return (
    <div className="kvbox">
      <span className="k">{k}</span>
      <span style={{ flex: 1, textAlign: "right" }}>
        <span className="v">{v}</span>
      </span>
    </div>
  );
}

interface ConsoleTabProps {
  lines: LogLine[];
  isLive: boolean;
  emptyHint: string;
}

function ConsoleTab({ lines, isLive, emptyHint }: ConsoleTabProps): JSX.Element {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <div className="tab-body scroll" ref={bodyRef}>
      <div className="console">
        {lines.map((l, i) => (
          <div key={i} className={`ln lvl-${l.lvl}`}>
            <span className="gut">{String(i + 1).padStart(3, "0")}</span>
            <span className="ts">{fmtTs(l.t)}</span>
            <span className="body">
              {l.body}
              {isLive && i === lines.length - 1 ? <span className="caret" /> : null}
            </span>
          </div>
        ))}
        {lines.length === 0 && (
          <div
            className="muted"
            style={{ padding: "20px 0", fontFamily: "var(--font-sans)" }}
          >
            {emptyHint}
          </div>
        )}
      </div>
    </div>
  );
}

function DiffTab({ task }: { task: Task }): JSX.Element {
  const { t } = useI18n();
  const hunks = task.diff_hunks ?? {};
  const present = task.changed_files.filter((f) => hunks[f.path]);
  if (present.length === 0) {
    return (
      <div className="tab-body">
        <div className="empty">
          {task.changed_files.length === 0
            ? t("detail.noFilesChanged")
            : t("detail.noDiff")}
        </div>
      </div>
    );
  }
  return (
    <div className="tab-body scroll">
      <div className="diff">
        {present.map((f) => (
          <div key={f.path} className="diff-file">
            <div className="diff-file-head">
              <span className="path">{f.path}</span>
              <span className="stat">
                <span className="add">+{f.additions}</span>
                <span className="del">−{f.deletions}</span>
                <span className="muted">{opLabel(f.op, t)}</span>
              </span>
            </div>
            <div className="diff-hunk">
              {(hunks[f.path] ?? []).map((h, i) => (
                <div
                  key={i}
                  className={`h ${h.type === "hunk" ? "hunk" : h.type === "add" ? "add" : h.type === "del" ? "del" : "ctx"}`}
                >
                  <span className="gn">{h.n1 ?? ""}</span>
                  <span className="gn">{h.n2 ?? ""}</span>
                  <span className="ln">{h.text}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function opLabel(op: string, t: (key: string) => string): string {
  switch (op) {
    case "M":
      return t("detail.op.modified");
    case "A":
      return t("detail.op.added");
    case "D":
      return t("detail.op.deleted");
    case "R":
      return t("detail.op.renamed");
    default:
      return op;
  }
}

function FilesTab({ task }: { task: Task }): JSX.Element {
  const { t } = useI18n();
  const files = task.changed_files;
  if (files.length === 0) {
    return (
      <div className="tab-body">
        <div className="empty">{t("detail.noFilesChanged")}</div>
      </div>
    );
  }
  const maxChanges = Math.max(1, ...files.map((f) => f.additions + f.deletions));
  const cells = 16;
  return (
    <div className="tab-body scroll">
      <div className="files">
        {files.map((f, i) => {
          const total = f.additions + f.deletions;
          const onCells = Math.max(1, Math.round((total / maxChanges) * cells));
          const addCells = Math.round((f.additions / Math.max(1, total)) * onCells);
          return (
            <div key={`${f.path}-${i}`} className="file-row">
              <span className="icon" data-op={f.op}>
                {f.op}
              </span>
              <span className="path">{f.path}</span>
              <span className="changes">
                <span className="add">+{f.additions}</span>
                <span className="del">−{f.deletions}</span>
              </span>
              <span className="bar">
                {Array.from({ length: cells }).map((_, j) => (
                  <i
                    key={j}
                    style={{
                      background:
                        j < addCells
                          ? "var(--st-returned)"
                          : j < onCells
                            ? "var(--st-failed)"
                            : "var(--bg-2)",
                    }}
                  />
                ))}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MetaTab({ task }: { task: Task }): JSX.Element {
  const { t } = useI18n();
  const entries: [string, string | number][] = [
    ["task_id", task.task_id],
    ["status", task.status],
    ["executor", task.executor],
    ["model", task.model || t("detail.modelDefault")],
    ["channel", task.channel],
    ["conversation_key", task.conversation_key],
    ["executor_session_id", task.executor_session_id ?? "—"],
    ["log_file", task.log_file],
    ["timestamp.received_at", task.timestamp.received_at ?? "—"],
    ["timestamp.validated_at", task.timestamp.validated_at ?? "—"],
    ["timestamp.queued_at", task.timestamp.queued_at ?? "—"],
    ["timestamp.started_at", task.timestamp.started_at ?? "—"],
    ["timestamp.ended_at", task.timestamp.ended_at ?? "—"],
    ["diff_summary.files", task.diff_summary.files],
    ["diff_summary.additions", task.diff_summary.additions],
    ["diff_summary.deletions", task.diff_summary.deletions],
    ["artifacts.count", task.artifacts.length],
    ["exit_code", task.exit_code ?? "—"],
  ];
  // Slots present in the UI but not produced today — kept dimmed so the meta
  // surface foreshadows what richer executors will fill in.
  const placeholders: [string, string][] = [
    ["meta.tokens", "—"],
    ["meta.cost_usd", "—"],
    ["meta.p50_latency_ms", "—"],
  ];
  return (
    <div className="tab-body scroll">
      <div
        style={{
          padding: "16px 22px 60px",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.9,
          maxWidth: 920,
        }}
      >
        {entries.map(([k, v]) => (
          <MetaRow key={k} k={k} v={v} />
        ))}
        <div
          style={{
            marginTop: 14,
            marginBottom: 6,
            fontSize: 10,
            color: "var(--fg-3)",
            textTransform: "uppercase",
            letterSpacing: 0.8,
            fontFamily: "var(--font-sans)",
            fontWeight: 600,
          }}
        >
          {t("detail.reserved")}
        </div>
        {placeholders.map(([k, v]) => (
          <MetaRow key={k} k={k} v={v} dimmed />
        ))}
      </div>
    </div>
  );
}

function MetaRow({
  k,
  v,
  dimmed = false,
}: {
  k: string;
  v: string | number;
  dimmed?: boolean;
}): JSX.Element {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "260px 1fr",
        gap: 16,
        borderBottom: "1px solid var(--line-soft)",
        padding: "4px 0",
        opacity: dimmed ? 0.5 : 1,
      }}
    >
      <span className="muted">{k}</span>
      <span style={{ color: dimmed ? "var(--fg-3)" : "var(--fg-0)", wordBreak: "break-all" }}>
        {String(v)}
      </span>
    </div>
  );
}

// statusLabel is re-exported indirectly via the imports above so consumers of
// Detail.tsx don't need to know where it lives.
export { statusLabel };
