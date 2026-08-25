import { Check, ChevronDown, ChevronRight, SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { CourseTopic, LearningContext, RuntimeModelProfile } from "@/shared/types";

const levels = [["beginner", "入门"], ["intermediate", "进阶"], ["advanced", "深入"]] as const;
const modes = [["explain", "讲解模式"], ["socratic", "引导模式"], ["practice", "练习模式"], ["review", "复习模式"]] as const;
type Section = "topic" | "level" | "mode" | "model";

interface LearningContextBarProps {
  value: LearningContext;
  onChange: (value: LearningContext) => void;
  topics?: CourseTopic[];
  unavailableModes?: LearningContext["mode"][];
  onUnavailableMode?: (mode: "practice" | "review") => void;
  modelProfiles?: Record<string, RuntimeModelProfile>;
  modelProfile?: string;
  onModelProfileChange?: (modelProfile: string) => void;
  modelSelectionDisabled?: boolean;
}

export function LearningContextBar({
  value,
  onChange,
  topics = [],
  unavailableModes = [],
  onUnavailableMode,
  modelProfiles = {},
  modelProfile,
  onModelProfileChange,
  modelSelectionDisabled = false,
}: LearningContextBarProps) {
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<Section | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const selectedTopic = topics.find((topic) => topic.id === value.topic_id);
  const labels = {
    topic: selectedTopic?.name ?? "未选择主题",
    level: levels.find(([key]) => key === value.level)?.[1] ?? value.level,
    mode: modes.find(([key]) => key === value.mode)?.[1] ?? value.mode,
    model: modelProfile ? modelProfiles[modelProfile]?.label ?? modelProfile : "未选择模型",
  };
  const sections: Section[] = ["topic", "level", "mode"];
  if (modelProfile && onModelProfileChange && Object.keys(modelProfiles).length > 0) sections.push("model");
  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) {
        setOpen(false);
        setSection(null);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const choose = (next: Section, key: string) => {
    if (next === "topic") {
      const topic = topics.find((item) => item.id === key);
      onChange({ ...value, topic_id: topic?.id ?? null, topic_name: topic?.name ?? "" });
    } else if (next === "level") {
      onChange({ ...value, level: key as LearningContext["level"] });
    } else if (next === "mode") {
      if (unavailableModes.includes(key as LearningContext["mode"])) {
        if (key === "practice" || key === "review") onUnavailableMode?.(key);
        return;
      }
      onChange({ ...value, mode: key as LearningContext["mode"] });
    } else {
      const profile = modelProfiles[key];
      if (modelSelectionDisabled || !profile?.available) return;
      onModelProfileChange?.(key);
    }
    setOpen(false);
    setSection(null);
  };
  const options = section === "topic"
    ? [["", "未选择主题"], ...topics.map((topic) => [topic.id, topic.name] as const)]
    : section === "level" ? levels
      : section === "mode" ? modes
        : Object.entries(modelProfiles).map(([key, profile]) => [key, profile.label] as const);
  return <div className="learning-context-menu" ref={ref}>
    <button className="learning-context-menu-trigger" type="button" aria-label="学习设置" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((current) => { if (current) setSection(null); return !current; })}><SlidersHorizontal size={15} /><span className="learning-context-menu-current">{labels.topic}</span><ChevronDown size={14} /></button>
    {open && <div className={section ? "learning-context-menu-panel has-options" : "learning-context-menu-panel"} role="dialog" aria-label="学习设置" onMouseLeave={() => setSection(null)}>
      <div className="learning-context-menu-sections">
        {sections.map((item) => {
          const itemLabel = item === "topic" ? "主题" : item === "level" ? "水平" : item === "mode" ? "模式" : "模型";
          const ariaLabel = item === "topic" ? "学习主题" : item === "level" ? "学习难度" : item === "mode" ? "教学模式" : "对话模型";
          return <button key={item} type="button" aria-label={ariaLabel} className={section === item ? "active" : ""} onMouseEnter={() => setSection(item)} onFocus={() => setSection(item)} onClick={() => setSection(item)}><span>{itemLabel}</span><small>{labels[item]}</small><ChevronRight size={14} /></button>;
        })}
      </div>
      {section && <div className="learning-context-menu-options" role="listbox" aria-label={section === "topic" ? "学习主题选项" : section === "level" ? "学习难度选项" : section === "mode" ? "教学模式选项" : "对话模型选项"}>
        <strong>{section === "topic" ? "选择主题" : section === "level" ? "选择水平" : section === "mode" ? "选择模式" : "选择模型"}</strong>
        {options.map(([key, label]) => {
          const unavailableMode = section === "mode" && unavailableModes.includes(key as LearningContext["mode"]);
          const unavailableModel = section === "model" && (!modelProfiles[key]?.available || modelSelectionDisabled);
          const unavailable = unavailableMode || unavailableModel;
          const selected = section === "topic" ? key === (value.topic_id ?? "") : section === "level" ? key === value.level : section === "mode" ? key === value.mode : key === modelProfile;
          const unavailableSuffix = unavailableMode ? "（未配置）" : section === "model" && !modelProfiles[key]?.available ? "（不可用）" : "";
          return <button key={key} type="button" role="option" aria-selected={selected} aria-disabled={unavailable} className={unavailable ? "unavailable" : ""} onClick={() => choose(section, key)}><span>{label}{unavailableSuffix}</span>{selected && <Check size={15} />}</button>;
        })}
      </div>}
    </div>}
  </div>;
}
