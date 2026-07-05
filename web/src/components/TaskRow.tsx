import { fmtAge, fmtDurBetween } from "../lib/format";
import { useI18n } from "../i18n";
import type { Task } from "../types";
import { MarkdownText } from "./MarkdownText";
import { ChannelPill, ExecutorPill } from "./Pills";

interface TaskRowProps {
  task: Task;
  selected: boolean;
  onSelect: (taskId: string) => void;
}

export function TaskRow({ task, selected, onSelect }: TaskRowProps): JSX.Element {
  const { t } = useI18n();
  const ranAt =
    task.timestamp.started_at ?? task.timestamp.queued_at ?? task.timestamp.received_at;
  const isLive = task.status === "RUNNING" || task.status === "RETRYING";
  const dur = task.timestamp.started_at
    ? fmtDurBetween(task.timestamp.started_at, isLive ? null : task.timestamp.ended_at)
    : "—";

  return (
    <div
      className={"task-row" + (selected ? " sel" : "")}
      data-row-st={task.status}
      onClick={() => onSelect(task.task_id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(task.task_id);
      }}
    >
      <span className="task-status" data-st={task.status} />
      <div className="task-body">
        <div className="task-head">
          <ExecutorPill executor={task.executor} />
          <ChannelPill channel={task.channel} meta={task.channel_meta} compact />
          <span className="muted mono" style={{ fontSize: 11 }}>
            {task.task_id}
          </span>
          {task.status === "RETRYING" && task.attempt != null && (
            <span
              className="muted mono"
              style={{ fontSize: 10.5, color: "var(--st-retrying)" }}
            >
              {t("common.attempt")} {task.attempt}
              {task.max_attempts ? `/${task.max_attempts}` : ""}
            </span>
          )}
        </div>
        <div className="task-summary" style={{ marginTop: 4 }}>
          <MarkdownText
            text={task.summary}
            empty={<span className="muted">{t("common.noSummary")}</span>}
          />
        </div>
        <div className="task-meta">
          {task.changed_files.length > 0 && (
            <span>
              <b>{task.changed_files.length}</b> {t("common.files")}
            </span>
          )}
          {task.diff_summary.additions + task.diff_summary.deletions > 0 && (
            <span>
              <b style={{ color: "var(--st-returned)" }}>+{task.diff_summary.additions}</b>{" "}
              <b style={{ color: "var(--st-failed)" }}>−{task.diff_summary.deletions}</b>
            </span>
          )}
          {task.artifacts.length > 0 && (
            <span>
              <b>{task.artifacts.length}</b> {t("common.artifacts")}
            </span>
          )}
          <span>· {task.conversation_key}</span>
        </div>
      </div>
      <div className="task-right">
        <span className="age">{fmtAge(ranAt, t)}</span>
        <span className="dur">
          {isLive ? "▶ " : ""}
          {dur}
        </span>
      </div>
    </div>
  );
}
