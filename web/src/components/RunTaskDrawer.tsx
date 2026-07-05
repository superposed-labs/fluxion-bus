import { useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "../i18n";
import type {
  RunTaskAgent,
  RunTaskInput,
  RunTaskMode,
  RunTaskProfile,
  RunTaskResponse,
  Task,
} from "../types";
import { MarkdownText } from "./MarkdownText";

export type RunTaskPrefill =
  | { mode: "new" }
  | { mode: "rerun"; task: Task }
  | { mode: "continue"; task: Task };

interface RunTaskDrawerProps {
  prefill: RunTaskPrefill;
  executors?: string[];
  onClose: () => void;
  onSubmit: (input: RunTaskInput) => Promise<RunTaskResponse>;
  onAccepted: (taskId: string) => void;
}

const ALL_AGENTS: Array<{ value: RunTaskAgent; label: string }> = [
  { value: "auto", label: "auto" },
  { value: "codex", label: "codex" },
  { value: "claude", label: "claude" },
  { value: "antigravity", label: "antigravity" },
];

const PROFILES: Array<{ value: RunTaskProfile; labelKey: string; mode: RunTaskMode; hintKey: string }> = [
  { value: "inspect", labelKey: "run.profile.inspect", mode: "read-only", hintKey: "run.profile.inspectHint" },
  { value: "implement", labelKey: "run.profile.implement", mode: "workspace-write", hintKey: "run.profile.implementHint" },
  { value: "verify", labelKey: "run.profile.verify", mode: "read-only", hintKey: "run.profile.verifyHint" },
  { value: "summarize", labelKey: "run.profile.summarize", mode: "read-only", hintKey: "run.profile.summarizeHint" },
];

const DEFAULT_WORKSPACE = ".";

function taskWorkspace(task: Task | null): string {
  if (!task) return DEFAULT_WORKSPACE;
  const meta = task.channel_meta as { cwd?: string };
  return meta.cwd || DEFAULT_WORKSPACE;
}

function cleanTaskName(value: string): string {
  const normalized = value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 48);
  return normalized || "web_task";
}

function defaultThread(task: Task | null): string {
  return task?.subagent?.thread || task?.conversation_key || "";
}

function profileFor(value: string) {
  return PROFILES.find((item) => item.value === value) ?? PROFILES[0]!;
}

function Icon({ name }: { name: "play" | "close" | "continue" | "rerun" | "write" | "lock" | "warn" }): JSX.Element {
  if (name === "play") {
    return <svg viewBox="0 0 16 16" fill="none"><path d="M5 3.5l7 4.5-7 4.5z" fill="currentColor" /></svg>;
  }
  if (name === "close") {
    return <svg viewBox="0 0 16 16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>;
  }
  if (name === "continue") {
    return <svg viewBox="0 0 16 16" fill="none"><path d="M3 5.5h7.5M3 8h6M3 10.5h7.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /><path d="M12 6.5 14 8l-2 1.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  }
  if (name === "rerun") {
    return <svg viewBox="0 0 16 16" fill="none"><path d="M13 8a5 5 0 1 1-1.46-3.54M13 3.2V5.4H10.8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  }
  if (name === "write") {
    return <svg viewBox="0 0 16 16" fill="none"><path d="M10.4 3.2l2.4 2.4M11.1 2.5a1.2 1.2 0 0 1 1.7 0l.3.3a1.2 1.2 0 0 1 0 1.7L6.1 11.8l-3 .8.8-3 7.2-7.1Z" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  }
  if (name === "lock") {
    return <svg viewBox="0 0 16 16" fill="none"><rect x="3.5" y="7" width="9" height="6" rx="1.4" stroke="currentColor" strokeWidth="1.3" /><path d="M5.5 7V5.3a2.5 2.5 0 0 1 5 0V7" stroke="currentColor" strokeWidth="1.3" /></svg>;
  }
  return <svg viewBox="0 0 16 16" fill="none"><path d="M8 2.6 14.4 13H1.6L8 2.6Z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /><path d="M8 6.6v3M8 11.2v.01" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg>;
}

function Seg<T extends string>({
  options,
  value,
  onChange,
  accent,
}: {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  accent?: boolean;
}): JSX.Element {
  return (
    <div className={"seg" + (accent ? " accent" : "")}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={value === option.value ? "on" : ""}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function OriginBanner({ prefill }: { prefill: RunTaskPrefill }): JSX.Element | null {
  const { t } = useI18n();
  if (prefill.mode === "new") return null;
  const isContinue = prefill.mode === "continue";
  return (
    <div className={"rt-origin" + (isContinue ? " cont" : "")}>
      <span className="rt-origin-ico"><Icon name={isContinue ? "continue" : "rerun"} /></span>
      <div className="rt-origin-copy">
        <div className="rt-origin-t">
          {t(isContinue ? "run.originContinue" : "run.originRerun")}
          <span className="rt-origin-id">{prefill.task.task_id}</span>
        </div>
        <div className="rt-origin-d">
          <MarkdownText
            text={prefill.task.summary}
            empty={<span className="muted">{t("common.noSummary")}</span>}
          />
        </div>
      </div>
    </div>
  );
}

function WriteConfirm({
  input,
  onCancel,
  onConfirm,
}: {
  input: RunTaskInput;
  onCancel: () => void;
  onConfirm: () => void;
}): JSX.Element {
  const { t } = useI18n();
  return (
    <div className="apc-wrap">
      <div className="apc-scrim" onClick={onCancel} />
      <div className="apc" role="dialog" aria-modal="true">
        <div className="apc-h">
          <span className="apc-ico"><Icon name="warn" /></span>
          <div>
            <div className="apc-t">{t("run.confirmTitle")}</div>
            <div className="apc-sub">{t("run.confirmSub")}</div>
          </div>
        </div>
        <div className="apc-body">
          <p className="apc-p">
            {t("run.confirmBody", { agent: input.agent, workspace: input.workspace })}
          </p>
          <div className="rt-confirm-grid">
            <div className="rc"><span className="rc-k">{t("run.workspace")}</span><span className="rc-v mono">{input.workspace}</span></div>
            <div className="rc"><span className="rc-k">{t("run.profile")}</span><span className="rc-v">{input.profile}</span></div>
            <div className="rc"><span className="rc-k">{t("run.thread")}</span><span className="rc-v mono">{input.thread || "default"}</span></div>
            <div className="rc"><span className="rc-k">{t("run.executor")}</span><span className="rc-v">{input.agent}</span></div>
          </div>
          <div className="rt-confirm-prompt">
            <span className="rc-k">{t("run.prompt")}</span>
            <p>{input.prompt}</p>
          </div>
        </div>
        <div className="apc-foot">
          <button type="button" className="btn" onClick={onCancel}>{t("run.back")}</button>
          <button type="button" className="btn btn-warn" onClick={onConfirm}>
            <Icon name="write" /> {t("run.confirmRun")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function RunTaskDrawer({
  prefill,
  executors,
  onClose,
  onSubmit,
  onAccepted,
}: RunTaskDrawerProps): JSX.Element {
  const { t } = useI18n();
  const originTask = prefill.mode === "new" ? null : prefill.task;
  const promptRef = useRef<HTMLTextAreaElement | null>(null);
  const [workspace, setWorkspace] = useState(() => taskWorkspace(originTask));
  const [agent, setAgent] = useState<RunTaskAgent>(() =>
    originTask?.executor === "codex" || originTask?.executor === "claude" || originTask?.executor === "antigravity"
      ? originTask.executor
      : "auto",
  );
  const [profile, setProfile] = useState<RunTaskProfile>(() => {
    const fromTask = originTask?.subagent?.profile;
    return fromTask === "implement" || fromTask === "verify" || fromTask === "summarize" ? fromTask : "inspect";
  });
  const [mode, setMode] = useState<RunTaskMode>(() =>
    originTask?.subagent?.mode === "workspace-write" ? "workspace-write" : profileFor(profile).mode,
  );
  const [thread, setThread] = useState(() => defaultThread(originTask));
  const [prompt, setPrompt] = useState(() => (prefill.mode === "rerun" ? originTask?.summary ?? "" : ""));
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const agentOptions = useMemo(() => {
    const enabled = new Set(executors ?? []);
    return ALL_AGENTS.filter((item) => item.value === "auto" || enabled.has(item.value));
  }, [executors]);

  useEffect(() => {
    if (!agentOptions.some((item) => item.value === agent)) setAgent("auto");
  }, [agent, agentOptions]);

  useEffect(() => {
    const id = window.setTimeout(() => promptRef.current?.focus(), 120);
    return () => window.clearTimeout(id);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (confirming) setConfirming(false);
      else onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [confirming, onClose]);

  const title = prefill.mode === "continue"
    ? t("run.continueTitle")
    : prefill.mode === "rerun"
      ? t("run.rerunTitle")
      : t("run.title");

  const summary = useMemo(
    () => `${agent} · ${profile} · ${mode} · ${workspace || DEFAULT_WORKSPACE}`,
    [agent, profile, mode, workspace],
  );

  const canSubmit = prompt.trim().length > 0 && !busy;
  const input: RunTaskInput = {
    prompt: prompt.trim(),
    agent,
    workspace: workspace.trim() || DEFAULT_WORKSPACE,
    thread: thread.trim(),
    task_name: cleanTaskName(thread || prompt || "web task"),
    parent_path: "/root",
    profile,
    mode,
    session_policy: prefill.mode === "continue" ? "continue" : "new",
    conversation_key: prefill.mode === "continue" ? originTask?.conversation_key : undefined,
  };

  async function submit(): Promise<void> {
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const result = await onSubmit(input);
      onAccepted(result.task_id);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function run(): void {
    if (!canSubmit) return;
    if (mode === "workspace-write") {
      setConfirming(true);
      return;
    }
    void submit();
  }

  return (
    <div className="drawer-wrap rt-wrap" data-screen-label="Run task">
      <div className="drawer-scrim" onClick={onClose} />
      <div className="drawer rt-drawer">
        <div className="drawer-head">
          <div className="drawer-titlerow">
            <div className="drawer-title">
              <span className="rt-title-ico"><Icon name={prefill.mode === "continue" ? "continue" : prefill.mode === "rerun" ? "rerun" : "play"} /></span>
              {title}
            </div>
            <div className="drawer-spacer" />
            <button type="button" className="iconbtn" title={t("common.close")} onClick={onClose}>
              <Icon name="close" />
            </button>
          </div>
        </div>

        <div className="drawer-body scroll">
          <div className="rt-form">
            <OriginBanner prefill={prefill} />

            <div className="fsection">
              <div className="fsection-h">{t("run.where")}</div>
              <div className="field">
                <label>{t("run.workspace")}</label>
                <input
                  className="input mono"
                  value={workspace}
                  onChange={(e) => setWorkspace(e.target.value)}
                  placeholder="."
                  spellCheck={false}
                />
              </div>
            </div>

            <div className="fsection">
              <div className="fsection-h">{t("run.who")}</div>
              <div className="field">
                <label>{t("run.executor")}</label>
                <Seg options={agentOptions} value={agent} onChange={setAgent} />
              </div>
              <div className="field">
                <label>{t("run.profile")}</label>
                <Seg
                  accent
                  options={PROFILES.map((item) => ({ value: item.value, label: t(item.labelKey) }))}
                  value={profile}
                  onChange={(next) => {
                    setProfile(next);
                    setMode(profileFor(next).mode);
                  }}
                />
                <div className="rt-hint">{t(profileFor(profile).hintKey)}</div>
              </div>
            </div>

            <div className="fsection">
              <div className="fsection-h">{t("run.permission")}</div>
              <div className="perm">
                <button
                  type="button"
                  className={"perm-opt" + (mode === "read-only" ? " on" : "")}
                  onClick={() => setMode("read-only")}
                >
                  <span className="perm-ico ro"><Icon name="lock" /></span>
                  <span className="perm-copy">
                    <b>{t("run.readOnly")}</b>
                    <span>{t("run.readOnlyHint")}</span>
                  </span>
                </button>
                <button
                  type="button"
                  className={"perm-opt write" + (mode === "workspace-write" ? " on" : "")}
                  onClick={() => setMode("workspace-write")}
                >
                  <span className="perm-ico wr"><Icon name="write" /></span>
                  <span className="perm-copy">
                    <b>{t("run.workspaceWrite")}</b>
                    <span>{t("run.workspaceWriteHint", { workspace: workspace || DEFAULT_WORKSPACE })}</span>
                  </span>
                </button>
              </div>
              {mode === "workspace-write" && (
                <div className="rt-writewarn">
                  <span className="rt-writewarn-ico"><Icon name="warn" /></span>
                  {t("run.writeWarn", { workspace: workspace || DEFAULT_WORKSPACE })}
                </div>
              )}
            </div>

            <div className="fsection">
              <div className="fsection-h">{t("run.what")}</div>
              <div className="field">
                <label>{t("run.prompt")}</label>
                <textarea
                  ref={promptRef}
                  className="textarea"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={t(prefill.mode === "continue" ? "run.promptContinue" : "run.promptNew")}
                />
              </div>
            </div>

            <div className="fsection">
              <div className="fsection-h">{t("run.session")}</div>
              <div className="field">
                <label>{t("run.thread")}</label>
                <input
                  className="input mono"
                  value={thread}
                  onChange={(e) => setThread(e.target.value)}
                  placeholder="default"
                  spellCheck={false}
                />
                <div className="rt-session-note">
                  {prefill.mode === "continue" ? t("run.sessionContinue") : t("run.sessionNew")}
                </div>
              </div>
            </div>

            {error && <div className="rt-error">{error}</div>}
          </div>
        </div>

        <div className="rt-foot">
          <span className="rt-foot-summ">{summary}</span>
          <div className="rt-foot-acts">
            <button type="button" className="btn" onClick={onClose}>{t("schedules.cancel")}</button>
            {mode === "workspace-write" ? (
              <button type="button" className="btn btn-warn" disabled={!canSubmit} onClick={run}>
                <Icon name="write" /> {busy ? t("run.submitting") : t("run.reviewRun")}
              </button>
            ) : (
              <button type="button" className="btn btn-primary" disabled={!canSubmit} onClick={run}>
                <Icon name="play" /> {busy ? t("run.submitting") : t("run.run")}
              </button>
            )}
          </div>
        </div>

        {confirming && (
          <WriteConfirm
            input={input}
            onCancel={() => setConfirming(false)}
            onConfirm={() => {
              setConfirming(false);
              void submit();
            }}
          />
        )}
      </div>
    </div>
  );
}
