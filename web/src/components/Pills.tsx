import type { ChannelMeta, LocalChannelMeta, SlackChannelMeta } from "../types";
import { statusLabel } from "../lib/constants";
import { channelLabel } from "../lib/channels";
import { useI18n } from "../i18n";

interface ChannelPillProps {
  channel: string;
  meta: ChannelMeta;
  compact?: boolean;
}

export function ChannelPill({ channel, meta, compact = false }: ChannelPillProps): JSX.Element {
  const { t } = useI18n();
  if (channel === "slack") {
    const slack = meta as SlackChannelMeta;
    const label = channelLabel(channel, t);
    return (
      <span className="srcpill" title={`${slack.channel} · ${slack.user}`}>
        <svg viewBox="0 0 14 14" fill="none">
          <rect x="1.5" y="5.5" width="3" height="3" rx="0.7" fill="currentColor" />
          <rect x="5.5" y="1.5" width="3" height="3" rx="0.7" fill="currentColor" />
          <rect x="9.5" y="5.5" width="3" height="3" rx="0.7" fill="currentColor" />
          <rect x="5.5" y="9.5" width="3" height="3" rx="0.7" fill="currentColor" />
        </svg>
        {compact ? label : (
          <>
            {label} <b style={{ color: "var(--fg-1)" }}>{slack.channel}</b>
          </>
        )}
      </span>
    );
  }
  if (channel === "wechat") {
    const wechat = meta as { user?: string };
    const displayUser = wechat.user ? wechat.user.split("@")[0] : "";
    const label = channelLabel(channel, t);
    return (
      <span className="srcpill" title={`${label} · ${wechat.user ?? ""}`}>
        <svg viewBox="0 0 14 14" fill="none">
          <path d="M4.5 9.5c.3 0 .5-.1.8-.2.8.5 1.7.9 1.7.9s-.2-1-.4-1.7c1.1-.7 1.9-1.8 1.9-3 0-2.2-1.8-4-4-4S.5 3.3.5 5.5s1.8 4 4 4z" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M9.5 11.5s-.1-.8-.3-1.3c.9-.5 1.5-1.4 1.5-2.4 0-1.7-1.4-3.1-3.1-3.1" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {compact ? label : (
          <>
            {label} <b style={{ color: "var(--fg-1)" }}>{displayUser}</b>
          </>
        )}
      </span>
    );
  }
  if (channel === "telegram") {
    const telegram = meta as { user?: string };
    const label = channelLabel(channel, t);
    return (
      <span className="srcpill" title={`${label} · ${telegram.user ?? ""}`}>
        <svg viewBox="0 0 14 14" fill="none">
          <path d="M13 1L1 6.5l4.5 1.5M13 1L9.5 12.5 5.5 8M13 1L5.5 8M5.5 8v3.5l2-2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {compact ? label : (
          <>
            {label} <b style={{ color: "var(--fg-1)" }}>{telegram.user ?? ""}</b>
          </>
        )}
      </span>
    );
  }
  if (channel === "qqbot") {
    const qq = meta as { user?: string; target_type?: string; openid?: string };
    const target = qq.user ?? qq.openid ?? "";
    const label = channelLabel(channel, t);
    return (
      <span className="srcpill" title={`${label} · ${target}`}>
        <svg viewBox="0 0 14 14" fill="none">
          <circle cx="7" cy="7" r="5.2" stroke="currentColor" strokeWidth="1.2" />
          <path d="M4.4 8.4c.8 1 1.6 1.5 2.6 1.5s1.8-.5 2.6-1.5M5.1 5.4h0M8.9 5.4h0" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
        {compact ? label : (
          <>
            {label} <b style={{ color: "var(--fg-1)" }}>{target}</b>
          </>
        )}
      </span>
    );
  }
  if (channel === "feishu") {
    const feishu = meta as { user?: string; chat_id?: string };
    const target = feishu.user ?? feishu.chat_id ?? "";
    const label = channelLabel(channel, t);
    return (
      <span className="srcpill" title={`${label} · ${target}`}>
        <svg viewBox="0 0 14 14" fill="none">
          <rect x="1.5" y="2.5" width="11" height="9" rx="2" stroke="currentColor" strokeWidth="1.2" />
          <path d="M4 7c1.2 1.6 4.8 1.6 6 0" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
        {compact ? label : (
          <>
            {label} <b style={{ color: "var(--fg-1)" }}>{target}</b>
          </>
        )}
      </span>
    );
  }
  const local = meta as LocalChannelMeta;
  return (
    <span className="srcpill" title={`${local?.host ?? ""}: ${local?.cwd ?? ""}`}>
      <svg viewBox="0 0 14 14" fill="none">
        <rect x="1" y="2.5" width="12" height="9" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
        <path
          d="M3.5 5.5l1.5 1.5-1.5 1.5M6 8.5h3"
          stroke="currentColor"
          strokeWidth="1.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {compact ? channel : (
        <>
          {channelLabel(channel, t)} <b style={{ color: "var(--fg-1)" }}>{local?.host ?? ""}</b>
        </>
      )}
    </span>
  );
}

export function ExecutorPill({ executor }: { executor: string }): JSX.Element {
  return (
    <span className="expill" data-ex={executor}>
      <span className="dot" />
      {executor}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }): JSX.Element {
  return (
    <span className="statusbadge" data-st={status}>
      <span className="ind" />
      {statusLabel(status)}
    </span>
  );
}
