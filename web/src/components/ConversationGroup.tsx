import { fmtAge } from "../lib/format";
import { useI18n } from "../i18n";
import type { Task } from "../types";
import { ExecutorPill } from "./Pills";
import { TaskRow } from "./TaskRow";

export interface ConversationTaskGroup {
  key: string;
  tasks: Task[];
  status: string;
  executors: string[];
  latestAt: string;
  additions: number;
  deletions: number;
  liveCount: number;
}

interface ConversationGroupProps {
  group: ConversationTaskGroup;
  expanded: boolean;
  selectedId: string | null;
  onToggle: (key: string) => void;
  onSelect: (taskId: string) => void;
}

export function ConversationGroup({
  group,
  expanded,
  selectedId,
  onToggle,
  onSelect,
}: ConversationGroupProps): JSX.Element {
  const { t } = useI18n();
  const hasSelection = group.tasks.some((task) => task.task_id === selectedId);
  const diffCount = group.additions + group.deletions;

  return (
    <div
      className={"conv-group" + (hasSelection ? " has-sel" : "")}
      data-expanded={expanded}
    >
      <button
        type="button"
        className="conv-head"
        onClick={() => onToggle(group.key)}
        aria-expanded={expanded}
        title={group.key}
      >
        <span className="conv-chevron" aria-hidden="true">
          ›
        </span>
        <span className="task-status conv-status" data-st={group.status} />
        <span className="conv-key">{group.key}</span>
        <span className="conv-count">{group.tasks.length}</span>
        <span className="conv-execs">
          {group.executors.map((executor) => (
            <ExecutorPill key={executor} executor={executor} />
          ))}
        </span>
        <span className="conv-right">
          {group.liveCount > 0 && (
            <span className="conv-run">
              ▶ {group.liveCount} {t("filters.live")}
            </span>
          )}
          {diffCount > 0 && (
            <span className="conv-diff mono">
              <b style={{ color: "var(--st-returned)" }}>+{group.additions}</b>{" "}
              <b style={{ color: "var(--st-failed)" }}>−{group.deletions}</b>
            </span>
          )}
          <span className="age">{fmtAge(group.latestAt, t)}</span>
        </span>
      </button>
      {expanded && (
        <div className="conv-items">
          {group.tasks.map((task) => (
            <TaskRow
              key={task.task_id}
              task={task}
              selected={task.task_id === selectedId}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}
