import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { useI18n } from "../i18n";

// Visual styling for the floating panel. Scoped to .twk-* selectors and
// injected once via <style> so we don't pollute the global stylesheet with
// chrome that's only used in one place.
const TWEAKS_STYLE = `
  .twk-fab{position:fixed;right:16px;bottom:16px;z-index:2147483645;
    width:36px;height:36px;border-radius:50%;border:.5px solid rgba(0,0,0,.18);
    background:rgba(250,249,247,.85);color:#29261b;cursor:pointer;
    -webkit-backdrop-filter:blur(20px) saturate(160%);backdrop-filter:blur(20px) saturate(160%);
    box-shadow:0 8px 24px rgba(0,0,0,.18);
    display:flex;align-items:center;justify-content:center;font-size:16px;padding:0}
  .twk-fab:hover{background:rgba(250,249,247,.95)}
  .twk-panel{position:fixed;right:16px;bottom:16px;z-index:2147483646;width:280px;
    max-height:calc(100vh - 32px);display:flex;flex-direction:column;
    background:rgba(250,249,247,.78);color:#29261b;
    -webkit-backdrop-filter:blur(24px) saturate(160%);backdrop-filter:blur(24px) saturate(160%);
    border:.5px solid rgba(255,255,255,.6);border-radius:14px;
    box-shadow:0 1px 0 rgba(255,255,255,.5) inset,0 12px 40px rgba(0,0,0,.18);
    font:11.5px/1.4 ui-sans-serif,system-ui,-apple-system,sans-serif;overflow:hidden}
  .twk-hd{display:flex;align-items:center;justify-content:space-between;
    padding:10px 8px 10px 14px;cursor:move;user-select:none}
  .twk-hd b{font-size:12px;font-weight:600;letter-spacing:.01em}
  .twk-x{appearance:none;border:0;background:transparent;color:rgba(41,38,27,.55);
    width:22px;height:22px;border-radius:6px;cursor:pointer;font-size:13px;line-height:1}
  .twk-x:hover{background:rgba(0,0,0,.06);color:#29261b}
  .twk-body{padding:2px 14px 14px;display:flex;flex-direction:column;gap:10px;
    overflow-y:auto;overflow-x:hidden;min-height:0;
    scrollbar-width:thin;scrollbar-color:rgba(0,0,0,.15) transparent}
  .twk-body::-webkit-scrollbar{width:8px}
  .twk-body::-webkit-scrollbar-track{background:transparent;margin:2px}
  .twk-body::-webkit-scrollbar-thumb{background:rgba(0,0,0,.15);border-radius:4px;
    border:2px solid transparent;background-clip:content-box}
  .twk-body::-webkit-scrollbar-thumb:hover{background:rgba(0,0,0,.25)}
  .twk-row{display:flex;flex-direction:column;gap:5px}
  .twk-row-h{flex-direction:row;align-items:center;justify-content:space-between;gap:10px}
  .twk-lbl{display:flex;justify-content:space-between;align-items:baseline;
    color:rgba(41,38,27,.72)}
  .twk-lbl>span:first-child{font-weight:500}
  .twk-val{color:rgba(41,38,27,.5);font-variant-numeric:tabular-nums}
  .twk-sect{font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
    color:rgba(41,38,27,.45);padding:10px 0 0}
  .twk-sect:first-child{padding-top:0}

  .twk-seg{position:relative;display:flex;padding:2px;border-radius:8px;
    background:rgba(0,0,0,.06);user-select:none}
  .twk-seg-thumb{position:absolute;top:2px;bottom:2px;border-radius:6px;
    background:rgba(255,255,255,.9);box-shadow:0 1px 2px rgba(0,0,0,.12);
    transition:left .15s cubic-bezier(.3,.7,.4,1),width .15s}
  .twk-seg button{appearance:none;position:relative;z-index:1;flex:1;border:0;
    background:transparent;color:inherit;font:inherit;font-weight:500;min-height:22px;
    border-radius:6px;cursor:pointer;padding:4px 6px;line-height:1.2}

  .twk-toggle{position:relative;width:32px;height:18px;border:0;border-radius:999px;
    background:rgba(0,0,0,.15);transition:background .15s;cursor:pointer;padding:0}
  .twk-toggle[data-on="1"]{background:#34c759}
  .twk-toggle i{position:absolute;top:2px;left:2px;width:14px;height:14px;border-radius:50%;
    background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.25);transition:transform .15s}
  .twk-toggle[data-on="1"] i{transform:translateX(14px)}

  .twk-chips{display:flex;gap:6px}
  .twk-chip{position:relative;appearance:none;flex:1;min-width:0;height:46px;
    padding:0;border:0;border-radius:6px;overflow:hidden;cursor:pointer;
    box-shadow:0 0 0 .5px rgba(0,0,0,.12),0 1px 2px rgba(0,0,0,.06);
    transition:transform .12s cubic-bezier(.3,.7,.4,1),box-shadow .12s}
  .twk-chip:hover{transform:translateY(-1px);
    box-shadow:0 0 0 .5px rgba(0,0,0,.18),0 4px 10px rgba(0,0,0,.12)}
  .twk-chip[data-on="1"]{box-shadow:0 0 0 1.5px rgba(0,0,0,.85),
    0 2px 6px rgba(0,0,0,.15)}
  .twk-chip svg{position:absolute;top:6px;left:6px;width:13px;height:13px;
    filter:drop-shadow(0 1px 1px rgba(0,0,0,.3))}
`;

let styleInjected = false;

function ensureStyle(): void {
  if (styleInjected || typeof document === "undefined") return;
  const el = document.createElement("style");
  el.textContent = TWEAKS_STYLE;
  document.head.appendChild(el);
  styleInjected = true;
}

interface TweaksPanelProps {
  title?: string;
  children: ReactNode;
}

const PAD = 16;

export function TweaksPanel({ title = "Tweaks", children }: TweaksPanelProps): JSX.Element | null {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const dragRef = useRef<HTMLDivElement | null>(null);
  const offsetRef = useRef<{ x: number; y: number }>({ x: PAD, y: PAD });

  useEffect(() => {
    ensureStyle();
  }, []);

  const clampToViewport = useCallback(() => {
    const panel = dragRef.current;
    if (!panel) return;
    const w = panel.offsetWidth;
    const h = panel.offsetHeight;
    const maxRight = Math.max(PAD, window.innerWidth - w - PAD);
    const maxBottom = Math.max(PAD, window.innerHeight - h - PAD);
    offsetRef.current = {
      x: Math.min(maxRight, Math.max(PAD, offsetRef.current.x)),
      y: Math.min(maxBottom, Math.max(PAD, offsetRef.current.y)),
    };
    panel.style.right = offsetRef.current.x + "px";
    panel.style.bottom = offsetRef.current.y + "px";
  }, []);

  useEffect(() => {
    if (!open) return;
    clampToViewport();
    window.addEventListener("resize", clampToViewport);
    return () => window.removeEventListener("resize", clampToViewport);
  }, [open, clampToViewport]);

  const onDragStart = (e: React.MouseEvent<HTMLDivElement>) => {
    const panel = dragRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    const sx = e.clientX;
    const sy = e.clientY;
    const startRight = window.innerWidth - rect.right;
    const startBottom = window.innerHeight - rect.bottom;
    const move = (ev: MouseEvent) => {
      offsetRef.current = {
        x: startRight - (ev.clientX - sx),
        y: startBottom - (ev.clientY - sy),
      };
      clampToViewport();
    };
    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };

  if (!open) {
    if (import.meta.env.DEV) {
      return (
        <button
          type="button"
          className="twk-fab"
          aria-label={t("tweaks.open")}
          title={t("tweaks.title")}
          onClick={() => setOpen(true)}
        >
          ⚙
        </button>
      );
    }
    return null;
  }

  return (
    <div
      ref={dragRef}
      className="twk-panel"
      style={{ right: offsetRef.current.x, bottom: offsetRef.current.y }}
    >
      <div className="twk-hd" onMouseDown={onDragStart}>
        <b>{title}</b>
        <button
          type="button"
          className="twk-x"
          aria-label={t("tweaks.close")}
          onMouseDown={(e) => e.stopPropagation()}
          onClick={() => setOpen(false)}
        >
          ✕
        </button>
      </div>
      <div className="twk-body">{children}</div>
    </div>
  );
}

export function TweakSection({ label }: { label: string }): JSX.Element {
  return <div className="twk-sect">{label}</div>;
}

interface TweakRowProps {
  label: string;
  value?: string | null;
  children: ReactNode;
  inline?: boolean;
}

function TweakRow({ label, value, children, inline = false }: TweakRowProps): JSX.Element {
  return (
    <div className={inline ? "twk-row twk-row-h" : "twk-row"}>
      <div className="twk-lbl">
        <span>{label}</span>
        {value != null && <span className="twk-val">{value}</span>}
      </div>
      {children}
    </div>
  );
}

interface TweakToggleProps {
  label: string;
  value: boolean;
  onChange: (next: boolean) => void;
}

export function TweakToggle({ label, value, onChange }: TweakToggleProps): JSX.Element {
  return (
    <div className="twk-row twk-row-h">
      <div className="twk-lbl">
        <span>{label}</span>
      </div>
      <button
        type="button"
        className="twk-toggle"
        data-on={value ? "1" : "0"}
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
      >
        <i />
      </button>
    </div>
  );
}

interface TweakRadioProps<T extends string> {
  label: string;
  value: T;
  options: readonly T[];
  onChange: (next: T) => void;
  formatOption?: (option: T) => string;
}

export function TweakRadio<T extends string>({
  label,
  value,
  options,
  onChange,
  formatOption,
}: TweakRadioProps<T>): JSX.Element {
  const n = options.length;
  const idx = Math.max(0, options.indexOf(value));
  const thumbStyle: CSSProperties = {
    left: `calc(2px + ${idx} * (100% - 4px) / ${n})`,
    width: `calc((100% - 4px) / ${n})`,
  };
  return (
    <TweakRow label={label}>
      <div className="twk-seg" role="radiogroup">
        <div className="twk-seg-thumb" style={thumbStyle} />
        {options.map((o) => (
          <button
            key={o}
            type="button"
          role="radio"
          aria-checked={o === value}
          onClick={() => onChange(o)}
        >
            {formatOption ? formatOption(o) : o}
          </button>
        ))}
      </div>
    </TweakRow>
  );
}

function isLight(hex: string): boolean {
  const h = hex.replace("#", "");
  const padded = h.length === 3 ? h.replace(/./g, (c) => c + c) : h.padEnd(6, "0");
  const n = parseInt(padded.slice(0, 6), 16);
  if (Number.isNaN(n)) return true;
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return r * 299 + g * 587 + b * 114 > 148000;
}

interface TweakColorProps {
  label: string;
  value: string;
  options: string[];
  onChange: (next: string) => void;
}

export function TweakColor({ label, value, options, onChange }: TweakColorProps): JSX.Element {
  const cur = value.toLowerCase();
  return (
    <TweakRow label={label}>
      <div className="twk-chips" role="radiogroup">
        {options.map((o) => {
          const on = o.toLowerCase() === cur;
          return (
            <button
              key={o}
              type="button"
              className="twk-chip"
              role="radio"
              aria-checked={on}
              data-on={on ? "1" : "0"}
              aria-label={o}
              title={o}
              style={{ background: o }}
              onClick={() => onChange(o)}
            >
              {on && (
                <svg viewBox="0 0 14 14" aria-hidden>
                  <path
                    d="M3 7.2 5.8 10 11 4.2"
                    fill="none"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    stroke={isLight(o) ? "rgba(0,0,0,.78)" : "#fff"}
                  />
                </svg>
              )}
            </button>
          );
        })}
      </div>
    </TweakRow>
  );
}
