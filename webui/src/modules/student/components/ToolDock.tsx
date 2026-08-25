import { BookOpenCheck, Code2, FileText, Globe2, Plus, Terminal, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent, ReactNode } from "react";
import { api } from "@/platform/http/api";

export type ToolDockTool = "files" | "learning" | "browser" | "terminal" | "sandbox";

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
  { id: "sandbox", label: "代码沙箱", buttonLabel: "打开代码沙箱工具", shortcut: "Ctrl+Alt+R", icon: Code2, description: "为当前登录用户准备独立的代码运行环境。" },
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

function SandboxPhaseZeroPanel() {
  const [leaseStatus, setLeaseStatus] = useState<"creating" | "ready" | "error">("creating");
  const [source, setSource] = useState("# 在这里运行 Python 代码\n");
  const [result, setResult] = useState("");
  const [running, setRunning] = useState(false);
  const [runtimeTicket, setRuntimeTicket] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<Array<{ id: number; label: string; detail: string }>>([]);

  useEffect(() => {
    let active = true;
    void api.ensureSandboxLease()
      .then((value) => { if (active) { setRuntimeTicket(value.runtime?.ticket ?? null); setLeaseStatus(value.runtime_available ? "ready" : "creating"); setTimeline([{ id: Date.now(), label: value.runtime_available ? "运行环境已就绪" : "正在预热运行环境", detail: value.runtime_available ? "已绑定当前会话。" : "预热池正在补充干净实例。" }]); } })
      .catch(() => { if (active) { setLeaseStatus("error"); setTimeline([{ id: Date.now(), label: "无法建立运行环境", detail: "请稍后重试。" }]); } });
    return () => { active = false; };
  }, []);

  return <section className="tool-dock-empty-panel sandbox-phase-zero-panel">
    <span><Code2 size={20} /></span>
    <strong>Code Runner</strong>
    <p>当前会话使用隔离运行环境；变量和 import 会在 Runtime 存活期间保留。</p>
    <div className={`sandbox-runtime-status ${leaseStatus}`}><i />{leaseStatus === "creating" ? "正在预热运行环境…" : leaseStatus === "ready" ? "运行环境已就绪" : "运行环境不可用"}</div>
    <textarea aria-label="沙箱代码" value={source} onChange={(event) => setSource(event.target.value)} spellCheck={false} />
    <div className="sandbox-phase-zero-actions">
      <button type="button" disabled={running} onClick={() => {
        setRunning(true); setTimeline((current) => [...current, { id: Date.now(), label: "开始执行", detail: "正在向隔离 Kernel 发送代码。" }]);
        void api.executeSandbox(source, runtimeTicket)
          .then(async (value) => { if (value.ticket) setRuntimeTicket(value.ticket); setResult(value.stdout || value.stderr || "运行完成。"); if (value.execution_id) { const replay = await api.replaySandboxEvents(value.execution_id); setTimeline(replay.events.map((event) => ({ id: event.seq, label: event.type === "execution.output" ? "运行输出" : event.type === "execution.completed" ? "执行完成" : "开始执行", detail: event.payload.text ?? "运行状态已恢复。" }))); } else setTimeline((current) => [...current, { id: Date.now(), label: "执行完成", detail: value.stderr ? "运行返回错误输出。" : "已收到 Kernel 输出。" }]); })
          .catch(() => { setResult("当前运行环境不可用。"); setTimeline((current) => [...current, { id: Date.now(), label: "执行失败", detail: "请重新打开或重置运行环境。" }]); })
          .finally(() => setRunning(false));
      }}>{running ? "运行中…" : "运行代码"}</button>
      <button type="button" className="secondary" disabled={running} onClick={() => {
        void api.restartSandbox(runtimeTicket).then(() => { setRuntimeTicket(null); setResult("运行环境已重置，请重新打开沙箱。"); setTimeline((current) => [...current, { id: Date.now(), label: "运行环境已重置", detail: "Kernel 内存状态已清空。" }]); }).catch(() => setResult("当前运行环境不可用。"));
      }}>重置运行环境</button>
    </div>
    {result && <pre>{result}</pre>}
    <section className="sandbox-execution-timeline" aria-label="执行时间线"><strong>执行记录</strong>{timeline.slice(-5).reverse().map((event) => <div key={event.id}><i /><span><b>{event.label}</b><small>{event.detail}</small></span></div>)}</section>
  </section>;
}

const DEFAULT_DOCK_WIDTH = 420;
const MIN_DOCK_WIDTH = 320;
const MAX_DOCK_VIEWPORT_RATIO = 0.88;

function getMaxDockWidth() {
  return Math.max(MIN_DOCK_WIDTH, Math.floor(window.innerWidth * MAX_DOCK_VIEWPORT_RATIO));
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
  const [width, setWidth] = useState(DEFAULT_DOCK_WIDTH);
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
      </nav> : activeTool === "learning" ? learningPanel : activeTool === "sandbox" ? <SandboxPhaseZeroPanel /> : activeTool ? <EmptyToolPanel tool={activeTool} /> : null}
    </div>}
  </aside>;
}
