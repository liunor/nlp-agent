import { BookOpenCheck, Code2, Contrast, Copy, Download, FileText, Globe2, MessageSquareText, Moon, Play, Plus, RotateCcw, Sun, Terminal, Trash2, X, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent, ReactNode } from "react";
import { api } from "@/platform/http/api";
import { SandboxArtifactFrame } from "./SandboxArtifactFrame";

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

type SandboxEditorTheme = "light" | "dark" | "high-contrast";

const sandboxThemes: Array<{ id: SandboxEditorTheme; label: string; buttonLabel: string; icon: typeof Sun }> = [
  { id: "light", label: "浅色", buttonLabel: "浅色", icon: Sun },
  { id: "dark", label: "深色", buttonLabel: "深色", icon: Moon },
  { id: "high-contrast", label: "高对比", buttonLabel: "高对比", icon: Contrast },
];

const MIN_OUTPUT_HEIGHT = 132;
const MAX_OUTPUT_HEIGHT = 440;
const SANDBOX_THEME_STORAGE_KEY = "nova.sandbox.editor-theme";
const MIN_EDITOR_FONT_SIZE = 14;
const MAX_EDITOR_FONT_SIZE = 24;
const DEFAULT_EDITOR_FONT_SIZE = 15;

function storedSandboxTheme(): SandboxEditorTheme {
  try {
    const value = window.localStorage.getItem(SANDBOX_THEME_STORAGE_KEY);
    return value === "dark" || value === "high-contrast" || value === "light" ? value : "light";
  } catch {
    return "light";
  }
}

const pythonTokenPattern = /(#.*$)|((?:[fbruFBRU])?(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'))|\b(?:False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b|\b(?:print|len|range|sum|list|dict|set|str|int|float|bool|enumerate|zip|open|type|isinstance)\b|\b\d+(?:\.\d+)?/gm;

function PythonSyntax({ source }: { source: string }) {
  const fragments: ReactNode[] = [];
  let cursor = 0;
  for (const match of source.matchAll(pythonTokenPattern)) {
    const index = match.index ?? 0;
    if (index > cursor) fragments.push(source.slice(cursor, index));
    const token = match[0];
    const tokenClass = match[1] ? "comment" : match[2] ? "string" : /^\d/.test(token) ? "number" : /^(print|len|range|sum|list|dict|set|str|int|float|bool|enumerate|zip|open|type|isinstance)$/.test(token) ? "builtin" : "keyword";
    fragments.push(<span className={`sandbox-syntax-${tokenClass}`} key={`${index}-${token}`}>{token}</span>);
    cursor = index + token.length;
  }
  if (cursor < source.length) fragments.push(source.slice(cursor));
  return <>{fragments}</>;
}

function SandboxPhaseZeroPanel({ onExplainCode }: { onExplainCode: (source: string) => void }) {
  // Keep the local in-memory preview immediately usable; a Docker response
  // without its mandatory ticket replaces this optimistic display with
  // “warming” before any privileged execution is attempted.
  const [leaseStatus, setLeaseStatus] = useState<"creating" | "ready" | "error">("ready");
  const [source, setSource] = useState("# 在这里运行 Python 代码\n");
  const [result, setResult] = useState("");
  const [running, setRunning] = useState(false);
  const [runtimeTicket, setRuntimeTicket] = useState<string | null>(null);
  const [runtimeAllowsNullTicket, setRuntimeAllowsNullTicket] = useState(true);
  const [timeline, setTimeline] = useState<Array<{ id: number | string; label: string; detail: string }>>([]);
  const [artifactUrls, setArtifactUrls] = useState<string[]>([]);
  const [editorTheme, setEditorTheme] = useState<SandboxEditorTheme>(storedSandboxTheme);
  const [editorFontSize, setEditorFontSize] = useState(DEFAULT_EDITOR_FONT_SIZE);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied">("idle");
  const [outputHeight, setOutputHeight] = useState(204);
  const [resizingOutput, setResizingOutput] = useState(false);
  const [editorScrollTop, setEditorScrollTop] = useState(0);
  const [editorScrollLeft, setEditorScrollLeft] = useState(0);
  const outputResizeStart = useRef<{ pointerY: number; height: number } | null>(null);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;
    const acquireLease = () => {
      void api.ensureSandboxLease()
        .then((value) => {
          if (!active) return;
          const ticket = value.runtime?.ticket ?? null;
          const isInMemory = Boolean(value.runtime && "kind" in value.runtime && value.runtime.kind === "inmemory");
          // The local in-memory backend intentionally has no signed Docker
          // capability. Docker is considered ready only when it supplied one.
          const ready = Boolean(value.runtime_available && (
            ticket || isInMemory || value.runtime === undefined
          ));
          setRuntimeTicket(ticket);
          setRuntimeAllowsNullTicket(isInMemory || value.runtime === undefined);
          setLeaseStatus(ready ? "ready" : "creating");
          setTimeline([{ id: Date.now(), label: ready ? "运行环境已就绪" : "正在预热运行环境", detail: ready ? "已绑定当前会话。" : "预热池正在补充干净实例。" }]);
          if (!ready) retryTimer = window.setTimeout(acquireLease, 1_000);
        })
        .catch(() => {
          if (!active) return;
          setLeaseStatus("error");
          setTimeline([{ id: Date.now(), label: "无法建立运行环境", detail: "正在重试连接。" }]);
          retryTimer = window.setTimeout(acquireLease, 2_000);
        });
    };
    acquireLease();
    return () => { active = false; if (retryTimer !== undefined) window.clearTimeout(retryTimer); };
  }, []);

  useEffect(() => {
    if (!resizingOutput) return undefined;
    const resize = (event: globalThis.PointerEvent) => {
      const start = outputResizeStart.current;
      if (!start) return;
      setOutputHeight(Math.min(MAX_OUTPUT_HEIGHT, Math.max(MIN_OUTPUT_HEIGHT, start.height - (event.clientY - start.pointerY))));
    };
    const stop = () => {
      outputResizeStart.current = null;
      setResizingOutput(false);
    };
    window.addEventListener("pointermove", resize);
    window.addEventListener("pointerup", stop, { once: true });
    return () => {
      window.removeEventListener("pointermove", resize);
      window.removeEventListener("pointerup", stop);
    };
  }, [resizingOutput]);

  const beginOutputResize = (event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    outputResizeStart.current = { pointerY: event.clientY, height: outputHeight };
    setResizingOutput(true);
  };
  const resizeOutputWithKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const change = event.key === "ArrowUp" ? 24 : -24;
    setOutputHeight((current) => Math.min(MAX_OUTPUT_HEIGHT, Math.max(MIN_OUTPUT_HEIGHT, current + change)));
  };
  const editorLines = source.split("\n");
  const runCode = () => {
        if ((!runtimeTicket && !runtimeAllowsNullTicket) || leaseStatus !== "ready") {
          setResult("运行环境仍在准备中，请稍候。");
          return;
        }
        setRunning(true); setTimeline((current) => [...current, { id: Date.now(), label: "开始执行", detail: "正在向隔离 Kernel 发送代码。" }]);
        void api.executeSandbox(source, runtimeTicket)
          .then(async (value) => { if (value.ticket) setRuntimeTicket(value.ticket); setResult(value.stdout || value.stderr || "运行完成。"); if (value.artifacts) setArtifactUrls((await Promise.all(value.artifacts.map((artifact) => api.getSandboxArtifactUrl(artifact.id).then((access) => access.url).catch(() => null)))).filter((url): url is string => Boolean(url))); if (value.execution_id) { const replay = await api.replaySandboxEvents(value.execution_id); setTimeline(replay.events.map((event) => ({ id: event.event_id, label: event.type === "execution.output" ? "运行输出" : event.type === "execution.completed" ? "执行完成" : "开始执行", detail: event.payload.text ?? "运行状态已恢复。" }))); } else setTimeline((current) => [...current, { id: Date.now(), label: "执行完成", detail: value.stderr ? "运行返回错误输出。" : "已收到 Kernel 输出。" }]); })
          .catch(() => { setResult("当前运行环境不可用。"); setTimeline((current) => [...current, { id: Date.now(), label: "执行失败", detail: "请重新打开或重置运行环境。" }]); })
          .finally(() => setRunning(false));
  };

  const setTheme = (theme: SandboxEditorTheme) => {
    setEditorTheme(theme);
    try {
      window.localStorage.setItem(SANDBOX_THEME_STORAGE_KEY, theme);
    } catch {
      // Local UI preferences remain optional in privacy-restricted browsers.
    }
  };
  const downloadSource = () => {
    const blob = new Blob([source], { type: "text/x-python;charset=utf-8" });
    const url = typeof URL.createObjectURL === "function" ? URL.createObjectURL(blob) : `data:text/plain;charset=utf-8,${encodeURIComponent(source)}`;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "main.py";
    anchor.click();
    if (typeof URL.revokeObjectURL === "function" && !url.startsWith("data:")) window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  const copySource = async () => {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(source);
      else {
        const fallback = document.createElement("textarea");
        fallback.value = source;
        fallback.style.position = "fixed";
        fallback.style.opacity = "0";
        document.body.append(fallback);
        fallback.select();
        document.execCommand("copy");
        fallback.remove();
      }
      setCopyStatus("copied");
      window.setTimeout(() => setCopyStatus("idle"), 1500);
    } catch {
      setCopyStatus("idle");
    }
  };
  const updateSourceAndSelection = (nextSource: string, selectionStart: number, selectionEnd = selectionStart) => {
    setSource(nextSource);
    window.requestAnimationFrame(() => {
      editorRef.current?.focus();
      editorRef.current?.setSelectionRange(selectionStart, selectionEnd);
    });
  };
  const toggleComment = (input: HTMLTextAreaElement) => {
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const lineStart = source.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const newlineAfterEnd = source.indexOf("\n", end);
    const lineEnd = newlineAfterEnd === -1 ? source.length : newlineAfterEnd;
    const selectedLines = source.slice(lineStart, lineEnd).split("\n");
    const uncomment = selectedLines.every((line) => !line.trim() || /^\s*# ?/.test(line));
    const nextLines = selectedLines.map((line) => uncomment ? line.replace(/^(\s*)# ?/, "$1") : line.replace(/^(\s*)/, "$1# "));
    const replacement = nextLines.join("\n");
    const nextSource = source.slice(0, lineStart) + replacement + source.slice(lineEnd);
    updateSourceAndSelection(nextSource, lineStart, lineStart + replacement.length);
  };
  const indentSelection = (input: HTMLTextAreaElement, outdent: boolean) => {
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const lineStart = source.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const newlineAfterEnd = source.indexOf("\n", end);
    const lineEnd = newlineAfterEnd === -1 ? source.length : newlineAfterEnd;
    const selected = source.slice(lineStart, lineEnd);
    const replacement = outdent ? selected.replace(/^(?: {1,4}|\t)/gm, "") : selected.replace(/^/gm, "    ");
    const nextSource = source.slice(0, lineStart) + replacement + source.slice(lineEnd);
    updateSourceAndSelection(nextSource, lineStart, lineStart + replacement.length);
  };
  const handleEditorKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const hasModifier = event.ctrlKey || event.metaKey;
    if (hasModifier && event.key === "Enter") {
      event.preventDefault();
      if (!running) runCode();
      return;
    }
    if (hasModifier && event.key.toLowerCase() === "s") {
      event.preventDefault();
      downloadSource();
      return;
    }
    if (hasModifier && event.key === "/") {
      event.preventDefault();
      toggleComment(event.currentTarget);
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      indentSelection(event.currentTarget, event.shiftKey);
    }
  };

  const workbenchStyle = { "--sandbox-editor-font-size": `${editorFontSize}px` } as CSSProperties;
  return <section className={"sandbox-workbench " + (resizingOutput ? "is-resizing" : "")} role="region" aria-label="代码工作台" data-editor-theme={editorTheme} style={workbenchStyle}>
    <header className="sandbox-workbench-titlebar">
      <div className="sandbox-file-tab"><Code2 size={16} /><strong>Code Runner</strong><span>main.py</span><small>Python</small></div>
      <div className="sandbox-theme-switcher" role="group" aria-label="代码编辑器主题">
        {sandboxThemes.map((theme) => {
          const Icon = theme.icon;
          return <button key={theme.id} type="button" aria-label={theme.buttonLabel} aria-pressed={editorTheme === theme.id} title={theme.label} onClick={() => setTheme(theme.id)}><Icon size={16} /></button>;
        })}
      </div>
      <div className="sandbox-editor-actions" aria-label="代码编辑器操作">
        <button type="button" aria-label="缩小代码字体" title="缩小代码字体" disabled={editorFontSize <= MIN_EDITOR_FONT_SIZE} onClick={() => setEditorFontSize((size) => Math.max(MIN_EDITOR_FONT_SIZE, size - 1))}><ZoomOut size={16} /></button>
        <button type="button" aria-label="放大代码字体" title="放大代码字体" disabled={editorFontSize >= MAX_EDITOR_FONT_SIZE} onClick={() => setEditorFontSize((size) => Math.min(MAX_EDITOR_FONT_SIZE, size + 1))}><ZoomIn size={16} /></button>
        <button type="button" aria-label="复制代码" title={copyStatus === "copied" ? "已复制" : "复制代码"} onClick={() => void copySource()}><Copy size={16} /></button>
        <button type="button" aria-label="下载代码" title="下载代码 (Ctrl/Cmd+S)" onClick={downloadSource}><Download size={16} /></button>
        <button type="button" aria-label="解释此代码" title="交给主页面智能体解释" onClick={() => onExplainCode(source)}><MessageSquareText size={16} /></button>
        <button type="button" aria-label="清空代码" title="清空代码" onClick={() => updateSourceAndSelection("", 0)}><Trash2 size={16} /></button>
      </div>
    </header>
    <div className="sandbox-environment-bar" aria-label="运行环境版本">
      <div className={`sandbox-runtime-status ${leaseStatus}`}><i />{leaseStatus === "creating" ? "正在预热…" : leaseStatus === "ready" ? "Kernel 已就绪" : "运行环境不可用"}</div>
      <span>当前会话使用隔离运行环境</span><span>Python 3.11</span><span>IPython Kernel 6.29</span><span>PyTorch 未预装</span><span>runsc 隔离</span>
    </div>
    <div className="sandbox-editor-pane">
      <div className="sandbox-code-gutter" role="list" aria-label="代码行号">
        <div style={{ transform: `translateY(${-editorScrollTop}px)` }}>{editorLines.map((_, index) => <span key={index} role="listitem">{index + 1}</span>)}</div>
      </div>
      <div className="sandbox-editor-layer">
        <pre className="sandbox-syntax-layer" aria-hidden="true" style={{ transform: `translate(${-editorScrollLeft}px, ${-editorScrollTop}px)` }}><code><PythonSyntax source={source} /></code></pre>
        <textarea ref={editorRef} aria-label="沙箱代码" value={source} onChange={(event) => setSource(event.target.value)} onKeyDown={handleEditorKeyDown} onScroll={(event) => { setEditorScrollTop(event.currentTarget.scrollTop); setEditorScrollLeft(event.currentTarget.scrollLeft); }} spellCheck={false} wrap="off" />
      </div>
    </div>
    <footer className="sandbox-editor-statusbar">
      <span>Ln {editorLines.length}, Col 1</span><span>Spaces: 4</span><span>UTF-8</span><span>Python</span>
    </footer>
    <div className="sandbox-output-resizer" role="separator" aria-label="调整输出面板高度" aria-orientation="horizontal" aria-valuemin={MIN_OUTPUT_HEIGHT} aria-valuemax={MAX_OUTPUT_HEIGHT} aria-valuenow={outputHeight} tabIndex={0} onPointerDown={beginOutputResize} onKeyDown={resizeOutputWithKeyboard}><i /></div>
    <section className="sandbox-output-panel" style={{ height: outputHeight }} aria-label="运行输出">
      <header>
        <div><Terminal size={15} /><strong>输出</strong><span>{running ? "运行中" : result ? "最近一次运行" : "等待运行"}</span></div>
        <div className="sandbox-phase-zero-actions">
          <button type="button" disabled={running || leaseStatus !== "ready"} onClick={runCode}><Play size={14} />{running ? "运行中…" : "运行代码"}</button>
          <button type="button" className="secondary" disabled={running} onClick={() => {
            void api.restartSandbox(runtimeTicket).then(() => { setRuntimeTicket(null); setResult("运行环境已重置，请重新打开沙箱。"); setTimeline((current) => [...current, { id: Date.now(), label: "运行环境已重置", detail: "Kernel 内存状态已清空。" }]); }).catch(() => setResult("当前运行环境不可用。"));
          }}><RotateCcw size={14} />重置</button>
        </div>
      </header>
      <pre>{result || "# 点击“运行代码”后，标准输出和错误输出将在这里显示。"}</pre>
      {artifactUrls.length > 0 && <section className="sandbox-artifacts" aria-label="沙箱产物预览">{artifactUrls.map((url) => <SandboxArtifactFrame key={url} url={url} />)}</section>}
      {timeline.length > 0 && <p className="sandbox-last-event">{timeline.at(-1)?.label} · {timeline.at(-1)?.detail}</p>}
    </section>
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

export function ToolDock({ open, expanded, openTools, activeTool, toolMenuOpen, onToolMenuOpenChange, onOpenTool, onCloseTool, onActiveToolChange, onExplainCode, learningPanel }: {
  open: boolean;
  expanded: boolean;
  openTools: ToolDockTool[];
  activeTool: ToolDockTool | null;
  toolMenuOpen: boolean;
  onToolMenuOpenChange: (open: boolean) => void;
  onOpenTool: (tool: ToolDockTool) => void;
  onCloseTool: (tool: ToolDockTool) => void;
  onActiveToolChange: (tool: ToolDockTool | null) => void;
  onExplainCode: (source: string) => void;
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
      </nav> : activeTool === "learning" ? learningPanel : activeTool === "sandbox" ? <SandboxPhaseZeroPanel onExplainCode={onExplainCode} /> : activeTool ? <EmptyToolPanel tool={activeTool} /> : null}
    </div>}
  </aside>;
}
