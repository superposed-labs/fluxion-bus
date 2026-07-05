import type { NotifyChannel } from "../types";

type Translate = (key: string) => string;

export const NOTIFY_CHANNEL_ORDER: NotifyChannel[] = [
  "slack",
  "telegram",
  "line",
  "qqbot",
  "wechat",
  "feishu",
];

export function channelLabel(channel: string, t: Translate): string {
  const key = `channel.${channel}`;
  const label = t(key);
  return label === key ? channel : label;
}
