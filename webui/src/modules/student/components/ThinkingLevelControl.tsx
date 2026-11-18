import { ChevronDown, Sparkles } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { ThinkingLevel } from "@/shared/types";
import "./composer-controls.css";
import "./thinking-level.css";

const levels: readonly ThinkingLevel[] = ["quick", "light", "deep", "extreme"];
const labels: Record<ThinkingLevel, string> = { quick: "快速", light: "轻度", deep: "深度", extreme: "极致" };
const navigationKeys = ["ArrowLeft", "ArrowRight", "ArrowDown", "ArrowUp", "Home", "End"];

interface Props {
  modelLabel?: string;
  value: ThinkingLevel;
  supportedLevels: readonly ThinkingLevel[];
  disabled?: boolean;
  onChange: (level: ThinkingLevel) => void;
  onPreviewChange?: (active: boolean) => void;
  onRequireLogin?: () => void;
}

export function ThinkingLevelControl({ modelLabel, value, supportedLevels, disabled = false, onChange, onPreviewChange, onRequireLogin }: Props) {
  const [open, setOpen] = useState(false);
  const [impact, setImpact] = useState(false);
  const atEnd = useRef(false);
  const [draft, setDraft] = useState(value);
  const [sliderValue, setSliderValue] = useState(levels.indexOf(value));
  const [draftOwner, setDraftOwner] = useState(value);
  if (draftOwner !== value) {
    setDraftOwner(value);
    setDraft(value);
    setSliderValue(levels.indexOf(value));
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
    setSliderValue(levels.indexOf(value));
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
        top: Math.max(12, Math.min(
          rect.top >= panel.offsetHeight + 22 ? rect.top - panel.offsetHeight - 10 : rect.bottom + 10,
          window.innerHeight - panel.offsetHeight - 12,
        )),
      });
    };
    place();
    sliderRef.current?.focus();
    // Input growth and attachment previews can move the toolbar without a window resize.
    const observer = new ResizeObserver(place);
    observer.observe(panelRef.current!);
    if (triggerRef.current?.parentElement) observer.observe(triggerRef.current.parentElement);
    const composer = triggerRef.current?.closest(".composer");
    if (composer) observer.observe(composer);
    const wrapper = triggerRef.current?.closest(".composer-wrap");
    if (wrapper) observer.observe(wrapper);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      observer.disconnect();
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
    const changed = draftRef.current !== next;
    draftRef.current = next;
    setDraft(next);
    setSliderValue(levels.indexOf(next));
    if (changed) onPreviewChange?.(next !== value);
  };
  const feedback = (position: number) => {
    const reachedEnd = position >= 2.99 && supportedLevels.includes("extreme");
    if (reachedEnd && !atEnd.current) setImpact(true);
    if (!reachedEnd) setImpact(false);
    atEnd.current = reachedEnd;
  };
  const commit = () => {
    const next = draftRef.current;
    interaction.current = null;
    setSliderValue(levels.indexOf(next));
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
    if (next !== undefined) { feedback(next); preview(levels[next]); }
  };

  return <>
    <button ref={triggerRef} type="button" className={`thinking-level-trigger ${open ? "is-open" : ""}`} disabled={disabled}
      aria-label={`思考程度：${labels[value]}`} aria-expanded={open} aria-haspopup="dialog" aria-controls={open ? id : undefined}
      onClick={() => {
        if (onRequireLogin) { onRequireLogin(); return; }
        if (open) close();
        else { draftRef.current = value; setDraft(value); setSliderValue(levels.indexOf(value)); atEnd.current = value === "extreme"; setImpact(false); setOpen(true); }
      }}>
      <Sparkles size={15} aria-hidden="true" /><span>{labels[value]}</span><ChevronDown size={13} aria-hidden="true" />
    </button>
    {open && createPortal(<div ref={panelRef} id={id} className="thinking-level-panel" role="dialog" aria-label="选择思考程度"
      style={position} onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); } }}
      onBlur={(event) => { if (!disabled && !event.currentTarget.contains(event.relatedTarget as Node | null)) close(); }}>
      <div className="thinking-level-heading" aria-hidden="true"><Sparkles size={14} /><span>{labels[draft]}</span></div>
      {modelLabel && <div className="thinking-level-model" title={modelLabel}>{modelLabel}</div>}
      <div className="thinking-level-track" style={{ "--thinking-progress": sliderValue / 3 } as CSSProperties}>
        <div className={`thinking-level-rail${impact ? " is-impacting" : ""}`} onAnimationEnd={() => setImpact(false)} aria-hidden="true"><div className="thinking-level-fill" />{levels.map((level, index) =>
          <i key={level} style={{ left: `calc(13px + (100% - 26px) * ${index / 3})` }} className={supportedLevels.includes(level) ? "" : "unavailable"} />)}</div>
        <input ref={sliderRef} type="range" min={0} max={3} step={0.01} value={sliderValue} disabled={disabled || supportedLevels.length === 0}
          aria-label="思考程度" aria-valuetext={labels[draft]} onPointerDown={(event) => { interaction.current = "pointer"; event.currentTarget.setPointerCapture?.(event.pointerId); }}
          onChange={(event) => {
            const position = Number(event.target.value);
            feedback(position);
            const nearest = levels.filter((level) => supportedLevels.includes(level)).reduce<ThinkingLevel | undefined>((best, level) =>
              best === undefined || Math.abs(levels.indexOf(level) - position) < Math.abs(levels.indexOf(best) - position) ? level : best, undefined);
            if (nearest === undefined) return;
            preview(nearest);
            if (interaction.current === "pointer") setSliderValue(position);
            if (!interaction.current) commit();
          }}
          onPointerUp={commit} onPointerCancel={close} onKeyDown={navigate}
          onLostPointerCapture={() => { if (interaction.current === "pointer") commit(); }}
          onKeyUp={(event) => { if (navigationKeys.includes(event.key)) commit(); }}
          onBlur={() => { if (interaction.current) commit(); }} />
      </div>
      {!available && <p className="thinking-level-unavailable" role="status">{supportedLevels.length === 0
        ? "当前模型不支持思考程度，请切换支持该功能的模型。"
        : `当前模型不支持${labels[value]}，请选择其他等级`}</p>}
    </div>, document.body)}
  </>;
}
