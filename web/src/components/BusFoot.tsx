import { EXECUTORS } from "../lib/constants";
import { useI18n } from "../i18n";
import type { Task } from "../types";

export function BusFoot({
  tasks,
  executorCount = EXECUTORS.length,
}: {
  tasks: Task[];
  executorCount?: number;
}): JSX.Element {
  const { t } = useI18n();
  const running = tasks.filter((t) => t.status === "RUNNING" || t.status === "RETRYING").length;
  const queued = tasks.filter(
    (t) => t.status === "QUEUED" || t.status === "VALIDATED" || t.status === "RECEIVED",
  ).length;

  return (
    <div className="busfoot">
      <div className="dotline">
        <span className="dot" />
        <span>{t("bus.gateway")}</span>
      </div>
      <div className="dotline" style={{ gap: 18 }}>
        <span>
          {t("bus.executors")} <b style={{ color: "var(--fg-0)" }}>{executorCount}</b>
        </span>
        <span>
          {t("bus.inFlight")} <b style={{ color: "var(--fg-0)" }}>{running}</b>
        </span>
        <span>
          {t("bus.backlog")}{" "}
          <b style={{ color: queued > 0 ? "var(--accent)" : "var(--fg-0)" }}>{queued}</b>
        </span>
      </div>
    </div>
  );
}
