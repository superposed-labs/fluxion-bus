import { useCallback, useEffect, useState } from "react";
import type { LocalePreference } from "../i18n";

const STORAGE_KEY = "fluxion.tweaks";

export interface Tweaks {
  density: "compact" | "regular" | "comfy";
  mode: "dark" | "light";
  accent: string;
  sideRail: boolean;
  showQuota: boolean;
  groupByConversation: boolean;
  locale: LocalePreference;
}

export const TWEAK_DEFAULTS: Tweaks = {
  density: "regular",
  mode: "dark",
  accent: "#f5a524",
  sideRail: true,
  showQuota: true,
  groupByConversation: false,
  locale: "auto",
};

// Amber, blue, mint, violet — chosen to map cleanly onto --accent in OKLCH.
export const ACCENT_OPTIONS: string[] = ["#f5a524", "#5aa9ff", "#5be1a9", "#c98bff"];

export const ACCENT_TO_OKLCH: Record<string, string> = {
  "#f5a524": "oklch(0.80 0.155 70)",
  "#5aa9ff": "oklch(0.74 0.150 230)",
  "#5be1a9": "oklch(0.78 0.130 160)",
  "#c98bff": "oklch(0.76 0.150 290)",
};

const SUPPORTS_OKLCH =
  typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("color", "oklch(0.80 0.155 70)");

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.replace("#", "");
  const expanded = normalized.length === 3
    ? normalized.split("").map((ch) => ch + ch).join("")
    : normalized;
  const value = Number.parseInt(expanded, 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function readStored(): Tweaks {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return TWEAK_DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<Tweaks>;
    return { ...TWEAK_DEFAULTS, ...parsed };
  } catch {
    return TWEAK_DEFAULTS;
  }
}

export type SetTweak = <K extends keyof Tweaks>(key: K, value: Tweaks[K]) => void;

export function useTweaks(): [Tweaks, SetTweak] {
  const [values, setValues] = useState<Tweaks>(readStored);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
    } catch {
      // localStorage may be unavailable in private mode — silently skip.
    }
  }, [values]);

  const setTweak = useCallback<SetTweak>((key, value) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  return [values, setTweak];
}

export function applyTweaks(t: Tweaks): void {
  const root = document.documentElement;
  root.setAttribute("data-density", t.density);
  root.setAttribute("data-mode", t.mode);
  root.setAttribute("data-side", t.sideRail ? "on" : "off");
  const accentHex = ACCENT_OPTIONS.includes(t.accent) ? t.accent : TWEAK_DEFAULTS.accent;

  if (SUPPORTS_OKLCH) {
    const accent = ACCENT_TO_OKLCH[accentHex] ?? ACCENT_TO_OKLCH[TWEAK_DEFAULTS.accent]!;
    root.style.setProperty("--accent", accent);
    root.style.setProperty("--accent-faint", accent.replace(")", " / 0.14)"));
    root.style.setProperty("--accent-line", accent.replace(")", " / 0.35)"));
    return;
  }

  const [r, g, b] = hexToRgb(accentHex);
  root.style.setProperty("--accent", accentHex);
  root.style.setProperty("--accent-faint", `rgba(${r}, ${g}, ${b}, 0.14)`);
  root.style.setProperty("--accent-line", `rgba(${r}, ${g}, ${b}, 0.35)`);
}
