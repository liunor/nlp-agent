import { ChevronDown, Sparkles } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { ThinkingLevel } from "@/shared/types";
import "./thinking-level.css";

const levels: readonly ThinkingLevel[] = ["quick", "light", "deep", "extreme"];
const labels: Record<ThinkingLevel, string> = { quick: "快速", light: "轻度", deep: "深度", extreme: "极致" };
const navigationKeys = ["ArrowLeft", "ArrowRight", "ArrowDown", "ArrowUp", "Home", "End"];

interface Props {
  value: ThinkingLevel;
  supportedLevels: readonly ThinkingLevel[];
  disabled?: boolean;
  onChange: (level: ThinkingLevel) => void;
  onPreviewChange?: (active: boolean) => void;
  onRequireLogin?: () => void;
}

export function ThinkingLevelControl({ value, supportedLevels, disabled = false, onChange, onPreviewChange, onRequireLogin }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [draftOwner, setDraftOwner] = useState(value);
  if (draftOwner !== value) {
    setDraftOwner(value);
    setDraft(value);
  }
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const sliderRef = useRef<HTMLInputElement>(null);
  const draftRef = useRef(value);
  const interaction = useRef<"pointer" | "keyboard" | null>(null);
  const id = useId();
  const available = supportedLevels.includes(value);
  useLayoutEffect(() => { draftRef.current = value; }, [value]);
  useEffect(() => () => { onPreviewChange?.(false); }, [onPreviewChange]);
  const close = useCallback(() => {
    interaction.current = null;
    draftRef.current = value;
    setDraft(value);
    setOpen(false);
    onPreviewChange?.(false);
    triggerRef.current?.focus();
  }, [onPreviewChange, value]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const rect = triggerRef.current?.getBoundingClientRect();
      const panel = panelRef.current;
      if (!rect || !panel) return;
      setPosition({
        left: Math.max(12, Math.min(rect.right - panel.offsetWidth, window.innerWidth - panel.offsetWidth - 12)),
        top: Math.max(12, rect.top - panel.offsetHeight - 10),
      });
    };
    place();
    sliderRef.current?.focus();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, available, disabled]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!panelRef.current?.contains(event.target as Node) && !triggerRef.current?.contains(event.target as Node)) close();
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [close, open]);

  const preview = (next: ThinkingLevel) => {
    if (disabled || !supportedLevels.includes(next)) return;
    draftRef.current = next;
    setDraft(next);
    onPreviewChange?.(next !== value);
  };
  const commit = () => {
    const next = draftRef.current;
    interaction.current = null;
    // Start the write before releasing the preview barrier so sends cannot slip through.
    if (!disabled && next !== value && supportedLevels.includes(next)) onChange(next);
    onPreviewChange?.(false);
  };
  const navigate = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!navigationKeys.includes(event.key) || disabled) return;
    event.preventDefault();
    interaction.current = "keyboard";
    const indices = levels.map((level, index) => supportedLevels.includes(level) ? index : -1).filter((index) => index >= 0);
    const current = levels.indexOf(draftRef.current);
    const next = event.key === "Home" ? indices[0] : event.key === "End" ? indices.at(-1)
      : ["ArrowRight", "ArrowUp"].includes(event.key) ? indices.find((index) => index > current)
        : [...indices].reverse().find((index) => index < current);
    if (next !== undefined) preview(levels[next]);
  };

  return <>
    <button ref={triggerRef} type="button" className={`thinking-level-trigger ${open ? "is-open" : ""}`} disabled={disabled}
      aria-label={`思考程度：${labels[value]}`} aria-expanded={open} aria-haspopup="dialog" aria-controls={open ? id : undefined}
      onClick={() => {
        if (onRequireLogin) { onRequireLogin(); return; }
        if (open) close();
        else { draftRef.current = value; setDraft(value); setOpen(true); }
      }}>
      <Sparkles size={15} aria-hidden="true" /><span>{labels[value]}</span><ChevronDown size={13} aria-hidden="true" />
    </button>
    {open && createPortal(<div ref={panelRef} id={id} className="thinking-level-panel" role="dialog" aria-label="选择思考程度"
      style={position} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); } }}
      onBlur={(event) => { if (!disabled && !event.currentTarget.contains(event.relatedTarget as Node | null)) close(); }}>
      <div className="thinking-level-heading" aria-hidden="true"><Sparkles size={18} /><strong>{labels[draft]}</strong></div>
      <div className="thinking-level-track" style={{ "--thinking-progress": `${levels.indexOf(draft) / 3 * 100}%` } as CSSProperties}>
        <div className="thinking-level-rail"><div className="thinking-level-fill" />{levels.map((level, index) =>
          <i key={level} style={{ left: `${index / 3 * 100}%` }} className={supportedLevels.includes(level) ? "" : "unavailable"} />)}</div>
        <input ref={sliderRef} type="range" min={0} max={3} step={1} value={levels.indexOf(draft)} disabled={disabled || supportedLevels.length === 0}
          aria-label="思考程度" aria-valuetext={labels[draft]} onPointerDown={(event) => { interaction.current = "pointer"; event.currentTarget.setPointerCapture?.(event.pointerId); }}
          onChange={(event) => { preview(levels[Number(event.target.value)]); if (!interaction.current) commit(); }}
          onPointerUp={commit} onPointerCancel={close} onKeyDown={navigate}
          onLostPointerCapture={() => { if (interaction.current === "pointer") commit(); }}
          onKeyUp={(event) => { if (navigationKeys.includes(event.key)) commit(); }}
          onBlur={() => { if (interaction.current) commit(); }} />
      </div>
      <div className="thinking-level-labels">{levels.map((level) => <button key={level} type="button" disabled={disabled || !supportedLevels.includes(level)}
        aria-pressed={draft === level} title={!supportedLevels.includes(level) ? "当前模型不支持" : undefined}
        onClick={() => { preview(level); draftRef.current = level; commit(); }}>{labels[level]}</button>)}</div>
      {!available && <p className="thinking-level-unavailable" role="status">当前模型不支持{labels[value]}，请选择其他等级</p>}
    </div>, document.body)}
  </>;
}
