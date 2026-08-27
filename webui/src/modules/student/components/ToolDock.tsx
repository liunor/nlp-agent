import { BookOpenCheck, FileText, Globe2, Plus, Terminal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent, ReactNode } from "react";

export type ToolDockTool = "files" | "learning" | "browser" | "terminal";

const tools: Array<{
  id: ToolDockTool;
  label: string;
  buttonLabel: string;
  shortcut: string;
  icon: typeof FileText;
  description: string;
}> = [
  { id: "files", label: "文件", buttonLabel: "打开文件工具", shortcut: "Ctrl+P", icon: FileText, description: "代码工作区将在这里打开。" },
  { id: "learning", label: "学习记录", buttonLabel: "打开学习记录工具", shortcut: "Ctrl+Alt+S", icon: BookOpenCheck, description: "查看本次对话的学习目标、概念与进度。" },
  { id: "browser", label: "浏览器", buttonLabel: "打开浏览器工具", shortcut: "Ctrl+T", icon: Globe2, description: "后续可在这里安全查看学习资料与网页。" },
  { id: "terminal", label: "终端", buttonLabel: "打开终端工具", shortcut: "Ctrl+~", icon: Terminal, description: "代码沙箱接入后将在这里显示终端与运行输出。" },
];

function EmptyToolPanel({ tool }: { tool: Exclude<ToolDockTool, "learning"> }) {
  const item = tools.find((candidate) => candidate.id === tool)!;
  const Icon = item.icon;
  return <section className="tool-dock-empty-panel">
    <span><Icon size={20} /></span>
    <strong>{item.label}</strong>
    <p>{item.description}</p>
  </section>;
}

const DEFAULT_DOCK_WIDTH = 420;
const MIN_DOCK_WIDTH = 320;
const MIN_THREAD_WIDTH = 560;
const DESKTOP_SIDEBAR_WIDTH = 252;
const MOBILE_DOCK_VIEWPORT_RATIO = 0.92;

function getMaxDockWidth() {
  const viewportWidth = window.innerWidth;
  if (viewportWidth <= 900) {
    return Math.max(
      MIN_DOCK_WIDTH,
      Math.floor(viewportWidth * MOBILE_DOCK_VIEWPORT_RATIO),
    );
  }

  const sidebarWidth = viewportWidth > 1024 ? DESKTOP_SIDEBAR_WIDTH : 0;
  return Math.max(
    MIN_DOCK_WIDTH,
    viewportWidth - sidebarWidth - MIN_THREAD_WIDTH,
  );
}

function ToolPicker({ onOpenTool }: { onOpenTool: (tool: ToolDockTool) => void }) {
  return <nav className="tool-dock-picker" role="menu" aria-label="工具列表">
    {tools.map((item) => {
      const Icon = item.icon;
      return <button key={item.id} type="button" role="menuitem" aria-label={item.buttonLabel} onClick={() => onOpenTool(item.id)}>
        <span><Icon size={17} /></span>
        <strong>{item.label}</strong>
        <kbd>{item.shortcut}</kbd>
      </button>;
    })}
  </nav>;
}

export function ToolDock({ open, expanded, openTools, activeTool, toolMenuOpen, onToolMenuOpenChange, onOpenTool, onCloseTool, onActiveToolChange, learningPanel }: {
  open: boolean;
  expanded: boolean;
  openTools: ToolDockTool[];
  activeTool: ToolDockTool | null;
  toolMenuOpen: boolean;
  onToolMenuOpenChange: (open: boolean) => void;
  onOpenTool: (tool: ToolDockTool) => void;
  onCloseTool: (tool: ToolDockTool) => void;
  onActiveToolChange: (tool: ToolDockTool | null) => void;
  learningPanel: ReactNode;
}) {
  const [width, setWidth] = useState(() =>
  Math.min(DEFAULT_DOCK_WIDTH, getMaxDockWidth()),
);
  const [resizing, setResizing] = useState(false);
  const [maxWidth, setMaxWidth] = useState(getMaxDockWidth);
  const resizeStart = useRef<{ pointerX: number; width: number } | null>(null);
  const tabStripRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const updateMaxWidth = () => {
      const nextMaxWidth = getMaxDockWidth();
      setMaxWidth(nextMaxWidth);
      setWidth((current) => Math.min(current, nextMaxWidth));
    };
    window.addEventListener("resize", updateMaxWidth);
    return () => window.removeEventListener("resize", updateMaxWidth);
  }, []);

  useEffect(() => {
    if (!resizing) return undefined;
    const handlePointerMove = (event: globalThis.PointerEvent) => {
      const start = resizeStart.current;
      if (!start) return;
      const nextWidth = Math.min(maxWidth, Math.max(MIN_DOCK_WIDTH, start.width - (event.clientX - start.pointerX)));
      setWidth(nextWidth);
    };
    const stopResizing = () => {
      resizeStart.current = null;
      setResizing(false);
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResizing, { once: true });
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResizing);
    };
  }, [maxWidth, resizing]);

  useEffect(() => {
    if (!activeTool) return;
    const activeTab = tabStripRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.parentElement;
    activeTab?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [activeTool, openTools]);

  const openTool = (tool: ToolDockTool) => {
    onToolMenuOpenChange(false);
    onOpenTool(tool);
  };
  const beginResize = (event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizeStart.current = { pointerX: event.clientX, width };
    setResizing(true);
  };
  const resizeWithKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const delta = event.key === "ArrowLeft" ? 24 : -24;
    setWidth((current) => Math.min(maxWidth, Math.max(MIN_DOCK_WIDTH, current + delta)));
  };
 const dockStyle = { "--tool-dock-width": `${width}px` } as CSSProperties;
  const showHome = openTools.length === 0;

  return <aside className={["tool-dock", open && "open", expanded && "expanded", resizing && "resizing"].filter(Boolean).join(" ")} aria-label="工具侧栏" style={dockStyle}>
    {open && <div className="tool-dock-surface">
      {!expanded && <div className="tool-dock-resize-handle" role="separator" aria-label="调整工具侧栏宽度" aria-orientation="vertical" aria-valuemin={MIN_DOCK_WIDTH} aria-valuemax={maxWidth} aria-valuenow={Math.round(width)} tabIndex={0} onPointerDown={beginResize} onKeyDown={resizeWithKeyboard} />}
      {openTools.length > 0 && <header className="tool-dock-tabs" role="tablist" aria-label="已打开的工具">
        <div ref={tabStripRef} className="tool-dock-tab-strip">
          {openTools.map((tool) => {
            const item = tools.find((candidate) => candidate.id === tool)!;
            const Icon = item.icon;
            const selected = tool === activeTool;
            return <div key={tool} className={["tool-dock-tab", selected && "active"].filter(Boolean).join(" ")}>
              <button type="button" role="tab" aria-selected={selected} onClick={() => onActiveToolChange(tool)}><Icon size={15} /><span>{item.label}</span></button>
              <button type="button" aria-label={"关闭" + item.label} onClick={() => onCloseTool(tool)}><X size={14} /></button>
            </div>;
          })}
        </div>
        <div className="tool-dock-add-control">
          <button className="tool-dock-add-tab" type="button" aria-label="显示工具列表" aria-expanded={toolMenuOpen} aria-haspopup="menu" onClick={() => onToolMenuOpenChange(!toolMenuOpen)}><Plus size={16} /></button>
          {toolMenuOpen && <ToolPicker onOpenTool={openTool} />}
        </div>
      </header>}
      {showHome ? <nav className="tool-dock-home" aria-label="工具列表">
        {tools.map((item) => {
          const Icon = item.icon;
          return <button key={item.id} type="button" aria-label={item.buttonLabel} onClick={() => openTool(item.id)}>
            <span><Icon size={18} /></span>
            <strong>{item.label}</strong>
            <kbd>{item.shortcut}</kbd>
          </button>;
        })}
      </nav> : activeTool === "learning" ? learningPanel : activeTool ? <EmptyToolPanel tool={activeTool} /> : null}
    </div>}
  </aside>;
}
