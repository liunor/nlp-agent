import { BookOpenCheck, BookOpenText, Code2, Contrast, Copy, Cpu, Download, FileText, MemoryStick, MessageSquareText, Moon, Play, Plus, RotateCcw, Sun, Terminal, Timer, Trash2, X, ZoomIn, ZoomOut } from "lucide-react";
import { Fragment, useEffect, useRef, useState } from "react";
import type { CSSProperties, DragEvent, KeyboardEvent, PointerEvent, ReactNode } from "react";
import { api, type SandboxRuntimeProfile, type SandboxRuntimeUsage } from "@/platform/http/api";
import { FilesPanel } from "./FilesPanel";
import { SandboxArtifactFrame } from "./SandboxArtifactFrame";

export type ToolDockTool = "files" | "learning" | "book" | "sandbox";
export type ToolDockTabDropPosition = "before" | "after";

const tools: Array<{
  id: ToolDockTool;
  label: string;
  buttonLabel: string;
  shortcut: string;
  shortcutKey: string;
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
  icon: typeof FileText;
  description: string;
}> = [
  { id: "files", label: "文件", buttonLabel: "打开文件工具", shortcut: "Ctrl+Alt+F", shortcutKey: "f", ctrl: true, alt: true, shift: false, icon: FileText, description: "导入并预览 Markdown、TXT 与代码文档。" },
  { id: "learning", label: "学习记录", buttonLabel: "打开学习记录工具", shortcut: "Ctrl+Alt+S", shortcutKey: "s", ctrl: true, alt: true, shift: false, icon: BookOpenCheck, description: "查看本次对话的学习目标、概念与进度。" },
  { id: "book", label: "知识教材", buttonLabel: "打开知识教材工具", shortcut: "Ctrl+Alt+B", shortcutKey: "b", ctrl: true, alt: true, shift: false, icon: BookOpenText, description: "阅读教师发布的知识点教材与实操内容。" },
  { id: "sandbox", label: "代码沙箱", buttonLabel: "打开代码沙箱工具", shortcut: "Ctrl+Alt+E", shortcutKey: "e", ctrl: true, alt: true, shift: false, icon: Code2, description: "为当前登录用户准备独立的代码运行环境。" },
];

type SandboxEditorTheme = "light" | "dark" | "high-contrast";

const sandboxThemes: Array<{ id: SandboxEditorTheme; label: string; buttonLabel: string; icon: typeof Sun }> = [
  { id: "light", label: "浅色", buttonLabel: "浅色", icon: Sun },
  { id: "dark", label: "深色", buttonLabel: "深色", icon: Moon },
  { id: "high-contrast", label: "高对比", buttonLabel: "高对比", icon: Contrast },
];

const MIN_OUTPUT_HEIGHT = 0;
const FALLBACK_MAX_OUTPUT_HEIGHT = 640;
const SANDBOX_THEME_STORAGE_KEY = "nova.sandbox.editor-theme";
const MIN_EDITOR_FONT_SIZE = 14;
const MAX_EDITOR_FONT_SIZE = 24;
const DEFAULT_EDITOR_FONT_SIZE = 15;

const FALLBACK_SANDBOX_RUNTIME_PROFILE: SandboxRuntimeProfile = {
  id: "python-base",
  runtime: "runsc",
  isolation: "runsc 隔离",
  python_version: "3.11",
  kernel_version: "6.29.5",
  pytorch_version: "2.7.1",
  pytorch_device: "CPU",
};

function formatBytes(bytes: number) {
  if (bytes >= 1_000_000 && bytes % 1_000_000 === 0) return `${bytes / 1_000_000} MB`;
  if (bytes >= 1_000 && bytes % 1_000 === 0) return `${bytes / 1_000} KB`;
  return `${bytes} B`;
}

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

function SandboxPhaseZeroPanel({ onExplainCode, initialSource }: { onExplainCode: (source: string) => void; initialSource?: string | null }) {
  // Keep the local in-memory preview immediately usable; a Docker response
  // without its mandatory ticket replaces this optimistic display with
  // “warming” before any privileged execution is attempted.
  const [leaseStatus, setLeaseStatus] = useState<"creating" | "ready" | "error">("ready");
  const [source, setSource] = useState(() => initialSource ?? "# 在这里运行 Python 代码\n");
  const [result, setResult] = useState("");
  const [running, setRunning] = useState(false);
  const [runtimeTicket, setRuntimeTicket] = useState<string | null>(null);
  const [runtimeAllowsNullTicket, setRuntimeAllowsNullTicket] = useState(true);
  const [runtimeProfile, setRuntimeProfile] = useState<SandboxRuntimeProfile>(FALLBACK_SANDBOX_RUNTIME_PROFILE);
  const [runtimeUsage, setRuntimeUsage] = useState<SandboxRuntimeUsage | null>(null);
  const [recentExecutionMetrics, setRecentExecutionMetrics] = useState<{ duration_ms: number; output_bytes: number } | null>(null);
  const [timeline, setTimeline] = useState<Array<{ id: number | string; label: string; detail: string }>>([]);
  const [artifactUrls, setArtifactUrls] = useState<string[]>([]);
  const [editorTheme, setEditorTheme] = useState<SandboxEditorTheme>(storedSandboxTheme);
  const [editorFontSize, setEditorFontSize] = useState(DEFAULT_EDITOR_FONT_SIZE);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied">("idle");
  const [outputHeight, setOutputHeight] = useState(204);
  const [outputBounds, setOutputBounds] = useState({ min: MIN_OUTPUT_HEIGHT, max: FALLBACK_MAX_OUTPUT_HEIGHT });
  const [resizingOutput, setResizingOutput] = useState(false);
  const [editorScrollTop, setEditorScrollTop] = useState(0);
  const [editorScrollLeft, setEditorScrollLeft] = useState(0);
  const outputResizeStart = useRef<{ pointerY: number; height: number; min: number; max: number } | null>(null);
  const workbenchRef = useRef<HTMLElement>(null);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  const getOutputResizeBounds = () => {
    const workbench = workbenchRef.current;
    const minimum = MIN_OUTPUT_HEIGHT;
    if (!workbench) return { min: minimum, max: FALLBACK_MAX_OUTPUT_HEIGHT };
    const workbenchHeight = Math.round(workbench.getBoundingClientRect().height || workbench.clientHeight);
    const fixedHeight = [
      workbench.querySelector<HTMLElement>(".sandbox-workbench-titlebar"),
      workbench.querySelector<HTMLElement>(".sandbox-environment-bar"),
      workbench.querySelector<HTMLElement>(".sandbox-editor-statusbar"),
      workbench.querySelector<HTMLElement>(".sandbox-output-resizer"),
    ].reduce((total, element) => total + (element ? Math.round(element.getBoundingClientRect().height) : 0), 0);
    const maximum = Math.max(minimum, workbenchHeight - fixedHeight);
    return { min: minimum, max: maximum };
  };

  useEffect(() => {
    if (initialSource === undefined || initialSource === null) return undefined;
    const timer = window.setTimeout(() => setSource(initialSource), 0);
    return () => window.clearTimeout(timer);
  }, [initialSource]);

  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;
    const acquireLease = () => {
      void api.ensureSandboxLease()
        .then((value) => {
          if (!active) return;
          const ticket = value.runtime?.ticket ?? null;
          if (value.runtime_profile) setRuntimeProfile(value.runtime_profile);
          setRuntimeUsage(null);
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
    if (!runtimeTicket || leaseStatus !== "ready") {
      return undefined;
    }
    let active = true;
    let timer: number | undefined;
    const poll = () => {
      void api.getSandboxUsage(runtimeTicket)
        .then((value) => {
          if (active) setRuntimeUsage(value);
        })
        .catch(() => {
          if (active) setRuntimeUsage(null);
        })
        .finally(() => {
          if (active) timer = window.setTimeout(poll, 2_000);
        });
    };
    poll();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [leaseStatus, runtimeTicket]);

  const visibleRuntimeUsage = runtimeTicket && leaseStatus === "ready" ? runtimeUsage : null;

  useEffect(() => {
    if (!resizingOutput) return undefined;
    const resize = (event: globalThis.PointerEvent) => {
      const start = outputResizeStart.current;
      if (!start) return;
      setOutputHeight(Math.min(start.max, Math.max(start.min, start.height - (event.clientY - start.pointerY))));
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
    const bounds = getOutputResizeBounds();
    setOutputBounds(bounds);
    setOutputHeight((current) => Math.min(bounds.max, Math.max(bounds.min, current)));
    outputResizeStart.current = { pointerY: event.clientY, height: Math.min(bounds.max, Math.max(bounds.min, outputHeight)), ...bounds };
    setResizingOutput(true);
  };
  const resizeOutputWithKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const bounds = getOutputResizeBounds();
    setOutputBounds(bounds);
    const change = event.key === "ArrowUp" ? 24 : -24;
    setOutputHeight((current) => Math.min(bounds.max, Math.max(bounds.min, current + change)));
  };
  const editorLines = source.split("\n");
  const runCode = () => {
        if ((!runtimeTicket && !runtimeAllowsNullTicket) || leaseStatus !== "ready") {
          setResult("运行环境仍在准备中，请稍候。");
          return;
        }
        setRunning(true); setTimeline((current) => [...current, { id: Date.now(), label: "开始执行", detail: "正在向隔离 Kernel 发送代码。" }]);
        void api.executeSandbox(source, runtimeTicket)
          .then(async (value) => { if (value.ticket) setRuntimeTicket(value.ticket); if (value.execution_metrics) setRecentExecutionMetrics(value.execution_metrics); setResult(value.stdout || value.stderr || "运行完成。"); if (value.artifacts) setArtifactUrls((await Promise.all(value.artifacts.map((artifact) => api.getSandboxArtifactUrl(artifact.id).then((access) => access.url).catch(() => null)))).filter((url): url is string => Boolean(url))); if (value.execution_id) { const replay = await api.replaySandboxEvents(value.execution_id); setTimeline(replay.events.map((event) => ({ id: event.event_id, label: event.type === "execution.output" ? "运行输出" : event.type === "execution.completed" ? "执行完成" : "开始执行", detail: event.payload.text ?? "运行状态已恢复。" }))); } else setTimeline((current) => [...current, { id: Date.now(), label: "执行完成", detail: value.stderr ? "运行返回错误输出。" : "已收到 Kernel 输出。" }]); })
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
  return <section ref={workbenchRef} className={"sandbox-workbench " + (resizingOutput ? "is-resizing" : "")} role="region" aria-label="代码工作台" data-editor-theme={editorTheme} style={workbenchStyle}>
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
      <div className="sandbox-environment-core">
        <span>当前会话使用隔离运行环境</span><span>Python {runtimeProfile.python_version}</span><span>IPython Kernel {runtimeProfile.kernel_version}</span><span>PyTorch {runtimeProfile.pytorch_version} {runtimeProfile.pytorch_device}</span><span>{runtimeProfile.isolation}</span>
      </div>
      <div className="sandbox-runtime-usage" aria-label="当前沙箱资源使用率" aria-live="polite">
        <span title="当前沙箱 CPU 使用率"><Cpu size={13} aria-hidden="true" /><b>CPU</b><strong>{formatPercent(visibleRuntimeUsage?.cpu_percent)}</strong></span>
        <span title="当前沙箱内存使用率"><MemoryStick size={13} aria-hidden="true" /><b>内存</b><strong>{formatPercent(visibleRuntimeUsage?.memory_percent)}</strong></span>
        <span title="最近一次代码运行耗时"><Timer size={13} aria-hidden="true" /><b>耗时</b><strong>{recentExecutionMetrics ? `${recentExecutionMetrics.duration_ms} ms` : "--"}</strong></span>
        <span title="最近一次代码运行产生的输出大小"><Terminal size={13} aria-hidden="true" /><b>输出</b><strong>{recentExecutionMetrics ? formatBytes(recentExecutionMetrics.output_bytes) : "--"}</strong></span>
      </div>
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
    <div className="sandbox-output-resizer" role="separator" aria-label="调整输出面板高度" aria-orientation="horizontal" aria-valuemin={outputBounds.min} aria-valuemax={outputBounds.max} aria-valuenow={Math.min(outputBounds.max, Math.max(outputBounds.min, outputHeight))} tabIndex={0} onPointerDown={beginOutputResize} onKeyDown={resizeOutputWithKeyboard}><i /></div>
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
const MIN_THREAD_WIDTH = 320;
const DESKTOP_SIDEBAR_WIDTH = 252;
const MOBILE_DOCK_VIEWPORT_RATIO = 0.92;
const TOOL_PANEL_RESIZER_WIDTH = 8;
const MIN_TOOL_PANEL_WIDTH = 96;

function equalToolPanelWidths(count: number) {
  return count > 0 ? Array.from({ length: count }, () => 100 / count) : [];
}

function formatPercent(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "--" : `${value.toFixed(1)}%`;
}

function resizeToolPanelWidths(widths: number[], index: number, deltaPx: number, containerWidth: number) {
  if (index < 0 || index >= widths.length - 1 || containerWidth <= 0) return widths;
  const usableWidth = Math.max(1, containerWidth - TOOL_PANEL_RESIZER_WIDTH * (widths.length - 1));
  const minShare = Math.min(48, (MIN_TOOL_PANEL_WIDTH / usableWidth) * 100);
  const desiredDelta = (deltaPx / usableWidth) * 100;
  const minDelta = minShare - widths[index];
  const maxDelta = widths[index + 1] - minShare;
  const appliedDelta = Math.max(minDelta, Math.min(maxDelta, desiredDelta));
  const next = [...widths];
  next[index] += appliedDelta;
  next[index + 1] -= appliedDelta;
  return next;
}

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
        <kbd aria-hidden="true">{item.shortcut}</kbd>
      </button>;
    })}
  </nav>;
}

export function ToolDock({ open, expanded, openTools, activeTool, toolMenuOpen, onToolMenuOpenChange, onOpenTool, onReorderTools, onCloseTool, onActiveToolChange, onExplainCode, learningPanel, knowledgeBookPanel, sandboxSource }: {
  open: boolean;
  expanded: boolean;
  openTools: ToolDockTool[];
  activeTool: ToolDockTool | null;
  toolMenuOpen: boolean;
  onToolMenuOpenChange: (open: boolean) => void;
  onOpenTool: (tool: ToolDockTool) => void;
  onReorderTools: (draggedTool: ToolDockTool, targetTool: ToolDockTool, position: ToolDockTabDropPosition) => void;
  onCloseTool: (tool: ToolDockTool) => void;
  onActiveToolChange: (tool: ToolDockTool | null) => void;
  onExplainCode: (source: string) => void;
  learningPanel: ReactNode;
  knowledgeBookPanel: ReactNode;
  sandboxSource?: string | null;
}) {
  const [width, setWidth] = useState(() =>
  Math.min(DEFAULT_DOCK_WIDTH, getMaxDockWidth()),
);
  const [resizing, setResizing] = useState(false);
  const [maxWidth, setMaxWidth] = useState(getMaxDockWidth);
  const resizeStart = useRef<{ pointerX: number; width: number } | null>(null);
  const tabStripRef = useRef<HTMLDivElement>(null);
  const tabsHeaderRef = useRef<HTMLElement>(null);
  const draggedTool = useRef<ToolDockTool | null>(null);
  const lastDragOver = useRef<{ tool: ToolDockTool; position: ToolDockTabDropPosition } | null>(null);
  const [draggingTool, setDraggingTool] = useState<ToolDockTool | null>(null);
  const [dragOverTool, setDragOverTool] = useState<ToolDockTool | null>(null);
  const panelStripRef = useRef<HTMLDivElement>(null);
  const panelResizeStart = useRef<{ index: number; pointerX: number; widths: number[]; containerWidth: number } | null>(null);
  const [panelWidths, setPanelWidths] = useState(() => equalToolPanelWidths(openTools.length));
  const [panelResizing, setPanelResizing] = useState(false);

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
    if (!toolMenuOpen) return undefined;
    const closeMenuOnOutsidePointer = (event: globalThis.PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && tabsHeaderRef.current?.contains(target)) return;
      onToolMenuOpenChange(false);
    };
    document.addEventListener("pointerdown", closeMenuOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeMenuOnOutsidePointer);
  }, [onToolMenuOpenChange, toolMenuOpen]);

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
    if (!panelResizing) return undefined;
    const handlePanelPointerMove = (event: globalThis.PointerEvent) => {
      const start = panelResizeStart.current;
      if (!start) return;
      setPanelWidths(resizeToolPanelWidths(start.widths, start.index, event.clientX - start.pointerX, start.containerWidth));
    };
    const stopPanelResizing = () => {
      panelResizeStart.current = null;
      setPanelResizing(false);
    };
    window.addEventListener("pointermove", handlePanelPointerMove);
    window.addEventListener("pointerup", stopPanelResizing, { once: true });
    return () => {
      window.removeEventListener("pointermove", handlePanelPointerMove);
      window.removeEventListener("pointerup", stopPanelResizing);
    };
  }, [panelResizing]);

  useEffect(() => {
    if (!activeTool) return;
    const activeTab = tabStripRef.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.parentElement;
    activeTab?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [activeTool, openTools]);

  const openTool = (tool: ToolDockTool) => {
    onToolMenuOpenChange(false);
    onOpenTool(tool);
  };

  useEffect(() => {
    const handleShortcut = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) return;
      const key = event.key.toLowerCase();
      const matched = tools.find((tool) => tool.shortcutKey === key && tool.ctrl === event.ctrlKey && tool.alt === event.altKey && tool.shift === event.shiftKey);
      if (!matched) return;
      event.preventDefault();
      onToolMenuOpenChange(false);
      onOpenTool(matched.id);
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [onOpenTool, onToolMenuOpenChange]);

  const clearTabDrag = () => {
    draggedTool.current = null;
    lastDragOver.current = null;
    setDraggingTool(null);
    setDragOverTool(null);
  };
  const beginTabDrag = (tool: ToolDockTool, event: DragEvent<HTMLDivElement>) => {
    draggedTool.current = tool;
    lastDragOver.current = null;
    setDraggingTool(tool);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", tool);
    setDragOverTool(null);
  };
  const dragTabOver = (tool: ToolDockTool, event: DragEvent<HTMLDivElement>) => {
    if (!draggedTool.current || draggedTool.current === tool) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const position: ToolDockTabDropPosition = openTools.indexOf(draggedTool.current) < openTools.indexOf(tool) ? "after" : "before";
    if (lastDragOver.current?.tool !== tool || lastDragOver.current.position !== position) {
      onReorderTools(draggedTool.current, tool, position);
      lastDragOver.current = { tool, position };
    }
    setDragOverTool(tool);
  };
  const dropTab = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    clearTabDrag();
  };
  const beginResize = (event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizeStart.current = { pointerX: event.clientX, width };
    setResizing(true);
  };
  const beginPanelResize = (index: number, event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const containerWidth = panelStripRef.current?.getBoundingClientRect().width ?? 0;
    const currentWidths = panelWidths.length === openTools.length ? panelWidths : equalToolPanelWidths(openTools.length);
    panelResizeStart.current = { index, pointerX: event.clientX, widths: currentWidths, containerWidth: Math.max(1, containerWidth) };
    setPanelResizing(true);
  };
  const resizePanelWithKeyboard = (index: number, event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const containerWidth = panelStripRef.current?.getBoundingClientRect().width ?? 0;
    const currentWidths = panelWidths.length === openTools.length ? panelWidths : equalToolPanelWidths(openTools.length);
    setPanelWidths(resizeToolPanelWidths(currentWidths, index, event.key === "ArrowRight" ? 24 : -24, Math.max(1, containerWidth)));
  };
  const resizeWithKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const delta = event.key === "ArrowLeft" ? 24 : -24;
    setWidth((current) => Math.min(maxWidth, Math.max(MIN_DOCK_WIDTH, current + delta)));
  };
  const dockStyle = { "--tool-dock-width": `${width}px` } as CSSProperties;
  const currentPanelWidths = panelWidths.length === openTools.length ? panelWidths : equalToolPanelWidths(openTools.length);
  const panelGridTemplate = currentPanelWidths.flatMap((share, index) => [
    `minmax(0, ${share}fr)`,
    ...(index < currentPanelWidths.length - 1 ? [`${TOOL_PANEL_RESIZER_WIDTH}px`] : []),
  ]).join(" ");
  const showHome = openTools.length === 0;

  return <aside className={["tool-dock", open && "open", expanded && "expanded", resizing && "resizing", panelResizing && "resizing-panels"].filter(Boolean).join(" ")} aria-label="工具侧栏" style={dockStyle}>
    <div className="tool-dock-surface" hidden={!open} aria-hidden={!open}>
      {!expanded && <div className="tool-dock-resize-handle" role="separator" aria-label="调整工具侧栏宽度" aria-orientation="vertical" aria-valuemin={MIN_DOCK_WIDTH} aria-valuemax={maxWidth} aria-valuenow={Math.round(width)} tabIndex={0} onPointerDown={beginResize} onKeyDown={resizeWithKeyboard} />}
      {openTools.length > 0 && <header ref={tabsHeaderRef} className="tool-dock-tabs" role="tablist" aria-label="已打开的工具">
        <div ref={tabStripRef} className="tool-dock-tab-strip">
          {openTools.map((tool, index) => {
            const item = tools.find((candidate) => candidate.id === tool)!;
            const Icon = item.icon;
            const selected = tool === activeTool;
            return <div key={tool} className={["tool-dock-tab", selected && "active", draggingTool === tool && "is-dragging", dragOverTool === tool && "is-drag-over"].filter(Boolean).join(" ")} draggable onDragStart={(event) => beginTabDrag(tool, event)} onDragOver={(event) => dragTabOver(tool, event)} onDrop={dropTab} onDragEnd={clearTabDrag}>
              <button type="button" role="tab" aria-selected={selected} aria-posinset={index + 1} aria-setsize={openTools.length} onClick={() => onActiveToolChange(tool)}><Icon size={15} /><span>{item.label}</span></button>
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
            <kbd aria-hidden="true">{item.shortcut}</kbd>
          </button>;
        })}
      </nav> : <div ref={panelStripRef} className="tool-dock-panels" style={{ "--tool-dock-panel-count": Math.max(1, openTools.length), gridTemplateColumns: panelGridTemplate } as CSSProperties}>
        {openTools.map((tool, index) => {
          const item = tools.find((candidate) => candidate.id === tool)!;
          const panelShare = currentPanelWidths[index] ?? 0;
          return <Fragment key={tool}>
            <div className="tool-dock-panel" data-active={tool === activeTool ? "true" : "false"}>
              {tool === "files" ? <FilesPanel /> : tool === "learning" ? learningPanel : tool === "book" ? knowledgeBookPanel : <SandboxPhaseZeroPanel onExplainCode={onExplainCode} initialSource={sandboxSource} />}
            </div>
            {index < openTools.length - 1 && <div className="tool-dock-panel-resizer" role="separator" aria-label={`调整${item.label}与${tools.find((candidate) => candidate.id === openTools[index + 1])?.label ?? "下个页面"}面板宽度`} aria-orientation="vertical" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(panelShare)} tabIndex={0} onPointerDown={(event) => beginPanelResize(index, event)} onKeyDown={(event) => resizePanelWithKeyboard(index, event)}><i /></div>}
          </Fragment>;
        })}
      </div>}
    </div>
  </aside>;
}
