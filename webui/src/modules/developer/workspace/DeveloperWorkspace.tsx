import {
  Activity, AppWindow, Bot, Box, ChevronLeft, ChevronRight, Clock3, Code2, Database,
  ExternalLink, FileKey2, Gauge, Globe2, KeyRound, Mail, MailOpen, Newspaper, PlugZap,
  Inbox, MessageCircle, RefreshCw, Search, Settings2, ShieldCheck, Sparkles, TerminalSquare, Trash2, User, Wrench,
  Users, ScrollText, MessageSquare, WalletCards,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ensureAuth } from "@/platform/http/api";
import type { DeveloperSnapshot, FeedbackCategory, FeedbackPriority, FeedbackStatus, FeedbackThread, FeedbackThreadSummary, ReleaseNoteEntry } from "@/shared/types";
import { UserManagementPage } from "@/modules/admin/UserManagementPage";
import { RoleManagementPageV2 } from "@/modules/admin/RoleManagementPageV2";
import { AuditLogPageV2 } from "@/modules/admin/AuditLogPageV2";
import { AgentSessionListPageV2 } from "@/modules/admin/AgentSessionListPageV2";
import { monitorUrl } from "@/monitor/monitor-helpers";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { QuotaManagementPage } from "@/modules/quota/QuotaManagementPage";

export type DeveloperPage = "overview" | "agents" | "tools" | "models" | "mcp" | "skills" | "release-notes" | "automations" | "feedback" | "settings" | "users" | "roles" | "audit" | "sessions" | "quotas";

const NAV: Array<{ page: DeveloperPage; label: string; icon: typeof Gauge }> = [
  { page: "overview", label: "工作台", icon: Gauge },
  { page: "agents", label: "Agent 与 Worker", icon: Bot },
  { page: "tools", label: "工具", icon: Wrench },
  { page: "models", label: "模型与 Provider", icon: Sparkles },
  { page: "mcp", label: "MCP", icon: PlugZap },
  { page: "skills", label: "Skills", icon: Code2 },
  { page: "release-notes", label: "发布说明", icon: Newspaper },
  { page: "automations", label: "Apps 与自动化", icon: Clock3 },
  { page: "feedback", label: "意见反馈", icon: Mail },
  { page: "settings", label: "运行时设置", icon: Settings2 },
  { page: "users", label: "用户管理", icon: Users },
  { page: "roles", label: "角色权限", icon: ShieldCheck },
  { page: "audit", label: "审计日志", icon: ScrollText },
  { page: "sessions", label: "Agent 会话", icon: MessageSquare },
  { page: "quotas", label: "额度管理", icon: WalletCards },
];

function currentPage(): DeveloperPage {
  const value = location.pathname.split("/")[2] as DeveloperPage | undefined;
  return NAV.some((item) => item.page === value) ? value! : "overview";
}

function pageForMenuRoute(routePath: string | null): DeveloperPage | null {
  const item = NAV.find((candidate) => candidate.page !== "overview" && candidate.page && routePath === `/developer/${candidate.page}`);
  if (routePath === "/developer") return "overview";
  return item?.page ?? null;
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="developer-json">{JSON.stringify(value, null, 2)}</pre>;
}

function StatusPill({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <span className={`developer-status ${ok ? "ok" : "idle"}`}>{children}</span>;
}

function Section({ title, hint, className = "", children }: { title: string; hint?: string; className?: string; children: React.ReactNode }) {
  return <section className={`developer-section ${className}`.trim()}><header><div><h2>{title}</h2>{hint && <p>{hint}</p>}</div></header>{children}</section>;
}

export function JsonEditor({ value, onSave, label = "保存" }: { value: unknown; onSave: (value: Record<string, unknown>) => Promise<void>; label?: string }) {
  const serialized = JSON.stringify(value, null, 2);
  return <JsonEditorState key={serialized} serialized={serialized} onSave={onSave} label={label} />;
}

function JsonEditorState({ serialized, onSave, label }: { serialized: string; onSave: (value: Record<string, unknown>) => Promise<void>; label: string }) {
  const [text, setText] = useState(serialized);
  const [message, setMessage] = useState("");
  const save = async () => {
    try { const parsed = JSON.parse(text) as Record<string, unknown>; await onSave(parsed); setMessage("已保存并应用"); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  };
  return <div className="developer-editor"><textarea value={text} onChange={(event) => setText(event.target.value)} spellCheck={false} /><div><button type="button" onClick={() => void save()}>{label}</button>{message && <small>{message}</small>}</div></div>;
}

function Overview({ snapshot }: { snapshot: DeveloperSnapshot }) {
  const runtime = snapshot.runtime;
  const monitorOrigin = monitorUrl(location);
  return <div className="developer-page-grid">
    <section className="developer-hero"><div><span>DEVELOPER CONTROL PLANE</span><h1>后端基础工作台</h1><p>查看 Agent、工具、模型和本地数据边界。学生界面不会显示这些内部信息。</p></div><ShieldCheck size={54} /></section>
    <div className="developer-kpis">
      <article><Activity /><span>Gateway</span><strong>{String(runtime.status ?? "unknown")}</strong></article>
      <article><Bot /><span>活跃 Turn</span><strong>{String(runtime.active_turns ?? 0)}</strong></article>
      <article><Database /><span>持久事件</span><strong>{Number(runtime.durable_events ?? 0).toLocaleString()}</strong></article>
      <article><PlugZap /><span>工具目录版本</span><strong>{snapshot.tools.catalog_revision}</strong></article>
    </div>
    <Section title="能力状态" hint="未配置的通用工作台能力会明确显示，不伪造可用状态。"><div className="developer-card-grid">{Object.entries(snapshot.features).map(([name, feature]) => <article className="developer-card" key={name}><div><AppWindow size={18} /><strong>{name}</strong></div><StatusPill ok={feature.available}>{feature.available ? "已启用" : "未启用"}</StatusPill><p>{feature.reason}</p></article>)}</div></Section>
    <Section title="独立观测平台" hint="Trace、Token、错误和实时事件在当前环境的隔离端口展示。"><a className="developer-monitor-link" href={monitorOrigin} target="_blank" rel="noreferrer"><Gauge size={20} /><span><strong>打开 Observability Monitor</strong><small>{new URL(monitorOrigin).host}</small></span><ExternalLink size={16} /></a></Section>
  </div>;
}

function Agents({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  const agents = snapshot.agents as Record<string, unknown>;
  const profiles = (agents.profiles ?? {}) as Record<string, unknown>;
  const [name, setName] = useState("");
  return <><Section title="Worker Profile" hint="Profile 组合 Skill、能力与工具授权；保存后会重新加载 Skill 解析器。"><div className="developer-inline-form"><input value={name} onChange={(event) => setName(event.target.value)} placeholder="profile 名称，例如 researcher" /><button type="button" onClick={() => { if (name) void api.saveWorkerProfile(name, { description: "", skills: [], allowed_tools: [], capabilities: [] }).then(refresh); }}>新建</button></div>{Object.entries(profiles).map(([profileName, profile]) => <div className="developer-managed-card" key={profileName}><strong>{profileName}</strong><JsonEditor value={profile} label="保存 Profile" onSave={async (value) => { await api.saveWorkerProfile(profileName, value); await refresh(); }} /><button className="danger" type="button" onClick={() => { if (confirm(`删除 Profile ${profileName}？`)) void api.deleteWorkerProfile(profileName).then(refresh); }}>删除</button></div>)}</Section><Section title="Agent / Worker 运行配置"><JsonBlock value={{ runtime: agents.runtime, overrides: agents.overrides }} /></Section><Section title="当前 Gateway"><JsonBlock value={snapshot.runtime} /></Section></>;
}

function Tools({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  return <><Section title="工具目录" hint={`${snapshot.tools.items.length} 个已注册工具；高优先级工具会更靠前展示给模型，展示顺序不改变权限。`}><div className="developer-table-wrap"><table><thead><tr><th>工具</th><th>来源</th><th>类别 / 版本</th><th>优先级</th><th>作用域</th><th>风险</th><th>超时</th></tr></thead><tbody>{snapshot.tools.items.map((tool) => <tr key={String(tool.name)}><td><strong>{String(tool.name)}</strong><small>{String(tool.description ?? "")}</small></td><td>{String(tool.source)} / {String(tool.provider)}</td><td>{String(tool.category ?? "general")} / {String(tool.version ?? "1.0")}</td><td>{String(tool.prompt_priority ?? 100)}</td><td>{Array.isArray(tool.scopes) ? tool.scopes.join(", ") : "-"}</td><td>{String(tool.risk)}</td><td>{String(tool.timeout_s)}s</td></tr>)}</tbody></table></div></Section><Section title="角色权限策略" hint="保存后新建的 Coordinator/Worker 立即按新策略生成 ToolSet；已启动 Worker 的授权快照不会被扩大。"><JsonEditor value={snapshot.tools.policies} label="保存权限策略" onSave={async (value) => { await api.updateToolPolicies(value); await refresh(); }} /></Section><Section title="自定义 Tool" hint="每个 Provider 都需要 Manifest，声明版本、类别、优先级、作用域、能力与风险。修改来源已持久化，但必须重启 Runtime 才会安全加载或卸载 Python 模块。"><JsonEditor value={snapshot.tools.custom} label="保存自定义 Tool 配置" onSave={async (value) => { const result = await api.updateCustomTools(value); await refresh(); alert(result.reason); }} /></Section></>;
}

function Models({ snapshot }: { snapshot: DeveloperSnapshot }) {
  return <><Section title="Provider / API Key" hint="密钥值永远不会发送到浏览器。"><div className="developer-card-grid">{Object.entries(snapshot.models.providers).map(([name, provider]) => <article className="developer-card" key={name}><div><KeyRound size={18} /><strong>{name}</strong></div><StatusPill ok={Boolean(provider.api_key_configured)}>{provider.api_key_configured ? "密钥已配置" : "缺少密钥"}</StatusPill><p>{String(provider.base_url ?? "")}</p></article>)}</div></Section><Section title="模型路由与故障转移"><JsonBlock value={{ defaults: snapshot.models.defaults, routes: snapshot.models.routes }} /></Section><Section title="思考、生成、超时与重试预设"><JsonBlock value={snapshot.models.presets} /></Section></>;
}

function Mcp({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  const entries = Object.entries(snapshot.tools.mcp_servers);
  const [name, setName] = useState(""); const [config, setConfig] = useState('{\n  "transport": "stdio",\n  "command": "",\n  "args": [],\n  "enabled_tools": ["*"],\n  "scopes": ["worker"]\n}'); const [result, setResult] = useState("");
  const parsed = () => JSON.parse(config) as Record<string, unknown>;
  return <><Section title="MCP Servers" hint="保存会持久化并热重连；测试使用隔离 Catalog，不会把试连工具泄漏到运行时。"><div className="developer-editor"><input value={name} onChange={(event) => setName(event.target.value)} placeholder="server 名称" /><textarea value={config} onChange={(event) => setConfig(event.target.value)} spellCheck={false} /><div><button type="button" onClick={() => { try { void api.testMcp(name, parsed()).then((value) => setResult(`连接成功：${value.tools.join(", ") || "未发现工具"}`)).catch((error) => setResult(error.message)); } catch (error) { setResult(String(error)); } }}>测试连接</button><button type="button" onClick={() => { try { void api.saveMcp(name, parsed()).then(async () => { setResult("已保存并热重连"); await refresh(); }).catch((error) => setResult(error.message)); } catch (error) { setResult(String(error)); } }}>保存 MCP</button>{result && <small>{result}</small>}</div></div>{entries.map(([serverName, serverConfig]) => <div className="developer-managed-card" key={serverName}><strong>{serverName}</strong><JsonBlock value={serverConfig} /><button className="danger" type="button" onClick={() => { if (confirm(`删除 MCP ${serverName}？`)) void api.deleteMcp(serverName).then(refresh); }}>删除</button></div>)}</Section></>;
}

function Skills({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  const [name, setName] = useState(""); const [content, setContent] = useState("---\nname: example\ndescription: 用途说明\nallowed_tools: []\ncapabilities: []\n---\n\n写入该 Skill 的操作流程。"); const [message, setMessage] = useState("");
  return <Section title="Skills" hint="保存到 .data/skills/<name>/SKILL.md，并立即重新加载。项目内 Skill 只读；同名工作区 Skill 会覆盖它。"><div className="developer-editor"><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Skill 名称" /><textarea value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} /><div><button type="button" onClick={() => void api.saveSkill(name, content).then(async () => { setMessage("已保存并重载"); await refresh(); }).catch((error) => setMessage(error.message))}>保存 Skill</button>{message && <small>{message}</small>}</div></div><div className="developer-list">{snapshot.skills.map((skill) => <article key={skill.path}><FileKey2 size={18} /><span><strong>{skill.name}</strong><small>{skill.description} · {skill.source} · {skill.path}</small></span><StatusPill ok={skill.available}>{skill.available ? "可用" : "缺少依赖"}</StatusPill><button type="button" onClick={() => void api.getSkill(skill.name).then((value) => { setName(value.name); setContent(value.content); })}>编辑</button>{skill.source === "workspace" && <button className="danger" type="button" onClick={() => { if (confirm(`删除 Skill ${skill.name}？`)) void api.deleteSkill(skill.name).then(refresh); }}>删除</button>}</article>)}</div></Section>;
}

function Automations({ snapshot }: { snapshot: DeveloperSnapshot }) {
  return <><Section title="Apps"><div className="developer-empty"><Box /><strong>Apps Registry 未启用</strong><p>{snapshot.features.apps.reason}</p></div></Section><Section title="Automations / Cron"><div className="developer-empty"><Clock3 /><strong>Cron Runtime 未启用</strong><p>{snapshot.features.automations.reason}</p></div></Section></>;
}

const FEEDBACK_PAGE_SIZE = 20;
const FEEDBACK_STATUS_OPTIONS: Array<{ value: FeedbackStatus | ""; label: string; color: string }> = [
  { value: "", label: "全部状态", color: "#6b7280" },
  { value: "open", label: "待处理", color: "#f59e0b" },
  { value: "under_review", label: "审视中", color: "#3b82f6" },
  { value: "planned", label: "已规划", color: "#8b5cf6" },
  { value: "in_progress", label: "进行中", color: "#06b6d4" },
  { value: "complete", label: "已完成", color: "#10b981" },
  { value: "closed", label: "已关闭", color: "#9ca3af" },
];
const FEEDBACK_CATEGORY_OPTIONS: Array<{ value: FeedbackCategory | ""; label: string }> = [
  { value: "", label: "全部分类" },
  { value: "feature", label: "功能建议" },
  { value: "ux", label: "体验问题" },
  { value: "bug", label: "Bug" },
  { value: "other", label: "其他" },
];
const FEEDBACK_PRIORITY_OPTIONS: Array<{ value: FeedbackPriority; label: string; color: string }> = [
  { value: "low", label: "低", color: "#10b981" },
  { value: "medium", label: "中", color: "#f59e0b" },
  { value: "high", label: "高", color: "#ef4444" },
];
const FEEDBACK_SORT_OPTIONS: Array<{ value: "latest" | "oldest" | "unread"; label: string }> = [
  { value: "latest", label: "最新" },
  { value: "unread", label: "未读优先" },
  { value: "oldest", label: "最早" },
];
// Pages whose content is derived from the runtime snapshot; every other page
// fetches its own data and must render without it.
const SNAPSHOT_PAGES: DeveloperPage[] = ["overview", "agents", "tools", "models", "mcp", "skills", "automations", "settings"];

export function Feedback({
  threads,
  total,
  pageSize,
  offset,
  search,
  loadError,
  loading = false,
  selectedId,
  onSelect,
  onSearchChange,
  onOffsetChange,
  onDelete,
  onMarkRead,
  onBulkMarkRead,
  onBulkDelete,
  refresh,
  statusFilter = "",
  categoryFilter = "",
  priorityFilter = "",
  sort = "latest",
  onStatusFilterChange,
  onCategoryFilterChange,
  onPriorityFilterChange,
  onSortChange,
}: {
  threads: FeedbackThreadSummary[];
  total: number;
  pageSize: number;
  offset: number;
  search: string;
  loadError: string;
  loading?: boolean;
  selectedId: string | null;
  onSelect: (threadId: string) => void;
  onSearchChange: (value: string) => void;
  onOffsetChange: (offset: number) => void;
  onDelete?: (threadId: string) => Promise<void>;
  onMarkRead?: (threadId: string) => Promise<void>;
  onBulkMarkRead?: (threadIds: string[]) => Promise<void>;
  onBulkDelete?: (threadIds: string[]) => Promise<void>;
  refresh: () => Promise<void>;
  statusFilter?: FeedbackStatus | "";
  categoryFilter?: FeedbackCategory | "";
  priorityFilter?: FeedbackPriority | "";
  sort?: "latest" | "oldest" | "unread";
  onStatusFilterChange?: (value: FeedbackStatus | "") => void;
  onCategoryFilterChange?: (value: FeedbackCategory | "") => void;
  onPriorityFilterChange?: (value: FeedbackPriority | "") => void;
  onSortChange?: (value: "latest" | "oldest" | "unread") => void;
}) {
  const [detail, setDetail] = useState<{ threadId: string; thread: FeedbackThread } | null>(null);
  const [error, setError] = useState<{ threadId: string; message: string } | null>(null);
  const [detailRetryNonce, setDetailRetryNonce] = useState(0);
  const [olderMessagesLoading, setOlderMessagesLoading] = useState(false);
  const [searchInput, setSearchInput] = useState(search);
  const [deleteError, setDeleteError] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ threadIds: string[]; label: string; bulk: boolean } | null>(null);
  const [selectedThreadIds, setSelectedThreadIds] = useState<Set<string>>(new Set());
  const [bulkWorking, setBulkWorking] = useState<"read" | "delete" | null>(null);
  const [readError, setReadError] = useState("");
  const [replyText, setReplyText] = useState("");
  const [replyError, setReplyError] = useState("");
  const [replySending, setReplySending] = useState(false);
  const [patchError, setPatchError] = useState("");
  const [patching, setPatching] = useState(false);

  useEffect(() => { queueMicrotask(() => setSearchInput(search)); }, [search]);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const trimmed = searchInput.trim();
      if (trimmed !== search) onSearchChange(trimmed);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput, search, onSearchChange]);
  useEffect(() => {
    if (!selectedId) return undefined;
    let active = true;
    void api.getFeedback(selectedId).then(async (value) => {
      if (!active) return;
      setError((current) => current?.threadId === selectedId ? null : current);
      setDetail({ threadId: selectedId, thread: value });
      setReplyText("");
      setReplyError("");
      setPatchError("");
      const lastMessage = value.messages[value.messages.length - 1];
      if (!lastMessage) return;
      try {
        await api.markFeedbackRead(selectedId, lastMessage.id);
        if (active) await refresh();
      } catch (reason) {
        if (active) setError({ threadId: selectedId, message: reason instanceof Error ? reason.message : String(reason) });
      }
    }).catch((reason) => {
      if (active) setError({ threadId: selectedId, message: reason instanceof Error ? reason.message : String(reason) });
    });
    return () => { active = false; };
  }, [detailRetryNonce, refresh, selectedId]);

  const selected = threads.find((item) => item.thread_id === selectedId);
  const visibleSelectedThreadIds = useMemo(() => new Set([...selectedThreadIds].filter((threadId) => threads.some((item) => item.thread_id === threadId))), [selectedThreadIds, threads]);
  const visibleThreadIds = useMemo(() => threads.map((item) => item.thread_id), [threads]);
  const allVisibleSelected = visibleThreadIds.length > 0 && visibleThreadIds.every((threadId) => visibleSelectedThreadIds.has(threadId));
  const someVisibleSelected = visibleThreadIds.some((threadId) => visibleSelectedThreadIds.has(threadId));
  const selectAllRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someVisibleSelected && !allVisibleSelected;
  }, [allVisibleSelected, someVisibleSelected]);
  const activeThread = detail?.threadId === selectedId ? detail.thread : null;
  const activeError = error?.threadId === selectedId ? error.message : "";
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const pageIndex = Math.floor(offset / pageSize) + 1;
  const statusColor = (value: FeedbackStatus) => FEEDBACK_STATUS_OPTIONS.find((item) => item.value === value)?.color ?? "#6b7280";
  const statusLabel = (value: FeedbackStatus) => FEEDBACK_STATUS_OPTIONS.find((item) => item.value === value)?.label ?? value;
  const categoryLabel = (value: FeedbackCategory) => FEEDBACK_CATEGORY_OPTIONS.find((item) => item.value === value)?.label ?? value;
  const priorityMeta = (value: FeedbackPriority) => FEEDBACK_PRIORITY_OPTIONS.find((item) => item.value === value) ?? FEEDBACK_PRIORITY_OPTIONS[1];
  const formatTime = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
  const loadOlderMessages = async () => {
    if (!activeThread?.message_has_more || olderMessagesLoading) return;
    setOlderMessagesLoading(true);
    setError(null);
    try {
      const page = await api.getFeedback(activeThread.thread_id, { limit: activeThread.message_limit ?? 50, offset: activeThread.messages.length });
      setDetail((current) => current && current.threadId === page.thread_id ? {
        ...current,
        thread: {
          ...current.thread,
          ...page,
          messages: [...page.messages, ...current.thread.messages],
          message_offset: 0,
        },
      } : current);
    } catch (reason) {
      setError({ threadId: activeThread.thread_id, message: reason instanceof Error ? reason.message : String(reason) });
    } finally {
      setOlderMessagesLoading(false);
    }
  };

  const requestDelete = (threadId: string) => {
    if (!onDelete) return;
    const target = threads.find((item) => item.thread_id === threadId) ?? (detail?.threadId === threadId ? { display_name: detail.thread.display_name, username: detail.thread.username } : undefined);
    setDeleteError("");
    setDeleteTarget({ threadIds: [threadId], label: target?.display_name || target?.username || threadId, bulk: false });
  };
  const requestBulkDelete = () => {
    if (!onBulkDelete || visibleSelectedThreadIds.size === 0) return;
    setDeleteError("");
    setDeleteTarget({ threadIds: [...visibleSelectedThreadIds], label: `已选 ${visibleSelectedThreadIds.size} 条反馈`, bulk: true });
  };
  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleteError("");
    if (deleteTarget.bulk) {
      if (!onBulkDelete) return;
      setBulkWorking("delete");
      try { await onBulkDelete(deleteTarget.threadIds); setSelectedThreadIds(new Set()); setDeleteTarget(null); }
      catch (reason) { setDeleteError(reason instanceof Error ? reason.message : String(reason)); }
      finally { setBulkWorking(null); }
      return;
    }
    if (!onDelete) return;
    const threadId = deleteTarget.threadIds[0];
    setDeletingId(threadId);
    try { await onDelete(threadId); setDeleteTarget(null); }
    catch (reason) { setDeleteError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setDeletingId(null); }
  };
  const toggleThreadSelection = (threadId: string) => {
    setSelectedThreadIds((current) => {
      const next = new Set([...current].filter((id) => threads.some((item) => item.thread_id === id)));
      if (next.has(threadId)) next.delete(threadId);
      else next.add(threadId);
      return next;
    });
  };
  const toggleAllVisible = () => {
    setSelectedThreadIds((current) => {
      const next = new Set([...current].filter((id) => threads.some((item) => item.thread_id === id)));
      if (allVisibleSelected) visibleThreadIds.forEach((threadId) => next.delete(threadId));
      else visibleThreadIds.forEach((threadId) => next.add(threadId));
      return next;
    });
  };
  const markThreadRead = async (item: FeedbackThreadSummary) => {
    if (!onMarkRead || item.unread_count === 0 || !item.latest) return;
    setReadError("");
    try { await onMarkRead(item.thread_id); }
    catch (reason) { setReadError(reason instanceof Error ? reason.message : String(reason)); }
  };
  const markSelectedRead = async () => {
    if (!onBulkMarkRead || visibleSelectedThreadIds.size === 0) return;
    setReadError("");
    setBulkWorking("read");
    try { await onBulkMarkRead([...visibleSelectedThreadIds]); setSelectedThreadIds(new Set()); }
    catch (reason) { setReadError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBulkWorking(null); }
  };
  const handleReply = async () => {
    if (!activeThread || !replyText.trim()) return;
    setReplySending(true); setReplyError("");
    try {
      await api.replyFeedback(activeThread.thread_id, replyText.trim());
      const fresh = await api.getFeedback(activeThread.thread_id);
      setDetail({ threadId: fresh.thread_id, thread: fresh });
      setReplyText("");
      await refresh();
    } catch (reason) { setReplyError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setReplySending(false); }
  };
  const handlePatch = async (patch: { status?: FeedbackStatus; category?: FeedbackCategory; priority?: FeedbackPriority }) => {
    if (!activeThread) return;
    setPatching(true); setPatchError("");
    try {
      const updated = await api.updateFeedback(activeThread.thread_id, patch);
      setDetail({ threadId: updated.thread_id, thread: updated });
      await refresh();
    } catch (reason) { setPatchError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setPatching(false); }
  };

  return <Section className="developer-feedback-section" title="学生意见反馈" hint="按状态、分类和优先级管理反馈；支持回复、标记已读与状态流转。">
    <div className="developer-feedback-toolbar">
      <label className="developer-feedback-search"><Search size={14} /><input value={searchInput} onChange={(event) => setSearchInput(event.target.value)} placeholder="搜索用户名或昵称" aria-label="搜索反馈用户" autoComplete="off" /></label>
      <div className="developer-feedback-filters">
        <select value={statusFilter} onChange={(event) => onStatusFilterChange?.(event.target.value as FeedbackStatus | "")} aria-label="按状态筛选" className="developer-feedback-select">{FEEDBACK_STATUS_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
        <select value={categoryFilter} onChange={(event) => onCategoryFilterChange?.(event.target.value as FeedbackCategory | "")} aria-label="按分类筛选" className="developer-feedback-select">{FEEDBACK_CATEGORY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
        <select value={priorityFilter} onChange={(event) => onPriorityFilterChange?.(event.target.value as FeedbackPriority | "")} aria-label="按优先级筛选" className="developer-feedback-select"><option value="">全部优先级</option>{FEEDBACK_PRIORITY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}优先</option>)}</select>
        <select value={sort} onChange={(event) => onSortChange?.(event.target.value as "latest" | "oldest" | "unread")} aria-label="排序" className="developer-feedback-select">{FEEDBACK_SORT_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>
      </div>
      <span className="developer-feedback-count"><Inbox size={12} />共 {total} 条</span>
      {threads.length > 0 && <div className="developer-feedback-bulk-actions" aria-label="批量操作">
        <label className="developer-feedback-select-all"><input ref={selectAllRef} type="checkbox" checked={allVisibleSelected} onChange={toggleAllVisible} aria-label="全选当前页" /><span>全选本页</span></label>
        {visibleSelectedThreadIds.size > 0 && <>
          <span>已选 {visibleSelectedThreadIds.size} 条</span>
          {onBulkMarkRead && <button type="button" className="developer-feedback-page-btn" disabled={bulkWorking !== null} onClick={() => void markSelectedRead()}><MailOpen size={13} />{bulkWorking === "read" ? "处理中…" : "批量已读"}</button>}
          {onBulkDelete && <button type="button" className="developer-feedback-page-btn danger" disabled={bulkWorking !== null} onClick={requestBulkDelete}><Trash2 size={13} />批量删除</button>}
        </>}
      </div>}
    </div>
    {deleteError && <p className="developer-feedback-error" role="alert">删除失败：{deleteError}</p>}
    {readError && <p className="developer-feedback-error" role="alert">标记已读失败：{readError}</p>}
    {patchError && <p className="developer-feedback-error" role="alert">更新失败：{patchError}</p>}
    <div className="developer-feedback">
      <div className="developer-feedback-list">
        {loading && threads.length === 0 ? <div className="developer-feedback-empty"><RefreshCw className="spin" /><strong>正在加载反馈…</strong></div> : threads.length === 0 && loadError ? <div className="developer-feedback-failed"><Inbox size={20} /><strong>加载反馈失败</strong><p>{loadError}</p><button type="button" onClick={() => void refresh()}>重试</button></div> : threads.length === 0 ? <div className="developer-feedback-empty"><Inbox size={22} /><strong>{search || statusFilter || categoryFilter || priorityFilter ? "没有匹配的反馈" : "暂无反馈"}</strong><p>{search || statusFilter || categoryFilter || priorityFilter ? "调整筛选条件或搜索" : "学生提交的反馈会出现在这里"}</p></div> : <>
          {loadError && <p className="developer-feedback-stale">刷新失败：{loadError}，正在显示上次结果</p>}
          {threads.map((item) => <div key={item.thread_id} className={`developer-feedback-row ${item.thread_id === selectedId ? "active" : ""}`}>
            <input className="developer-feedback-selection" type="checkbox" checked={visibleSelectedThreadIds.has(item.thread_id)} onChange={() => toggleThreadSelection(item.thread_id)} aria-label={`选择 ${item.display_name || item.username} 的反馈`} />
            <button type="button" className="developer-feedback-row-main" onClick={() => onSelect(item.thread_id)} aria-label={`查看 ${item.display_name || item.username} 的反馈`}><span className="developer-feedback-row-text"><span className="developer-feedback-row-name"><strong>{item.display_name || item.username}</strong>{item.unread_count > 0 && <b className="developer-feedback-unread">{item.unread_count > 99 ? "99+" : item.unread_count}</b>}</span><small><span className="developer-feedback-username">@{item.username}</span><span className="developer-feedback-dot">·</span><span className="developer-feedback-time">{formatTime(item.updated_at)}</span></small></span></button>
            {onMarkRead && item.unread_count > 0 && <button type="button" className="developer-feedback-row-read" aria-label={`标记 ${item.display_name || item.username} 已读`} disabled={bulkWorking !== null} onClick={(event) => { event.stopPropagation(); void markThreadRead(item); }}><MailOpen size={13} /></button>}
          </div>)}
        </>}
      </div>
      <div className="developer-feedback-detail">
        {activeError && <div className="developer-feedback-error"><p>读取失败：{activeError}</p><button type="button" onClick={() => setDetailRetryNonce((nonce) => nonce + 1)}>重试读取反馈</button></div>}
        {selected && activeThread ? <>
          <div className="developer-feedback-detail-head"><div className="developer-feedback-detail-identity"><div><h3>{activeThread.display_name || selected.username}</h3><p><User size={11} />@{activeThread.username} · {activeThread.messages.length} 条消息</p><p className="developer-feedback-detail-meta"><span className="developer-feedback-status large" style={{ background: statusColor(activeThread.status) }}>{statusLabel(activeThread.status)}</span><span className="developer-feedback-category large">{categoryLabel(activeThread.category)}</span><span className="developer-feedback-priority large" style={{ borderColor: priorityMeta(activeThread.priority).color, color: priorityMeta(activeThread.priority).color }}>{priorityMeta(activeThread.priority).label}优先级</span></p></div></div><div className="developer-feedback-detail-actions">{onMarkRead && selected.unread_count > 0 && <button type="button" className="developer-feedback-page-btn" onClick={() => void markThreadRead(selected)}><MailOpen size={13} />标记已读</button>}<select value={activeThread.status} onChange={(event) => void handlePatch({ status: event.target.value as FeedbackStatus })} disabled={patching} aria-label="修改状态" className="developer-feedback-select small">{FEEDBACK_STATUS_OPTIONS.filter((item) => item.value).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select><select value={activeThread.priority} onChange={(event) => void handlePatch({ priority: event.target.value as FeedbackPriority })} disabled={patching} aria-label="修改优先级" className="developer-feedback-select small">{FEEDBACK_PRIORITY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}优先</option>)}</select><select value={activeThread.category} onChange={(event) => void handlePatch({ category: event.target.value as FeedbackCategory })} disabled={patching} aria-label="修改分类" className="developer-feedback-select small">{FEEDBACK_CATEGORY_OPTIONS.filter((item) => item.value).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select>{onDelete && <button type="button" className="danger" aria-label="删除当前反馈" disabled={deletingId === selected.thread_id} onClick={() => requestDelete(selected.thread_id)}><Trash2 size={14} />删除</button>}</div></div>
          <div className="developer-feedback-messages">{activeThread.message_has_more && <button type="button" className="developer-feedback-load-more" disabled={olderMessagesLoading} onClick={() => void loadOlderMessages()}>{olderMessagesLoading ? "正在加载更早消息…" : "加载更早消息"}</button>}{activeThread.messages.map((message) => { const isStudent = message.sender_type === "student"; return <article key={message.id} className={`developer-feedback-message ${isStudent ? "student" : "developer"}`}><div className="developer-feedback-bubble"><header><strong>{isStudent ? (activeThread.display_name || selected.username) : "开发者"}</strong><time><Clock3 size={10} />{formatTime(message.created_at)}</time></header><p>{message.body}</p></div></article>; })}</div>
          <div className="developer-feedback-reply"><textarea value={replyText} onChange={(event) => setReplyText(event.target.value)} placeholder="以开发者身份回复…（学生可在“我的反馈”中看到）" rows={3} maxLength={2000} /><div className="developer-feedback-reply-actions"><small>{replyText.length}/2000</small><button type="button" className="developer-feedback-page-btn primary" disabled={!replyText.trim() || replySending} onClick={() => void handleReply()}>{replySending ? "发送中…" : "回复"}</button></div>{replyError && <p className="developer-feedback-error" role="alert">回复失败：{replyError}</p>}</div>
        </> : !activeError && <div className="developer-feedback-detail-empty"><MessageCircle size={20} /><strong>{selected ? "正在读取反馈…" : "选择一位学生查看反馈"}</strong><p>{selected ? "正在加载完整意见与回复记录" : "从左侧选择学生，查看完整意见与处理记录。"}</p></div>}
      </div>
    </div>
    {total > 0 && <div className="developer-feedback-pagination"><span>共 {total} 条 · 第 {pageIndex}/{pageCount} 页</span><button type="button" className="developer-feedback-page-btn" disabled={offset <= 0} onClick={() => onOffsetChange(Math.max(0, offset - pageSize))}><ChevronLeft size={13} />上一页</button><button type="button" className="developer-feedback-page-btn primary" disabled={offset + pageSize >= total} onClick={() => onOffsetChange(offset + pageSize)}>下一页<ChevronRight size={13} /></button></div>}
    <ConfirmDialog
      open={deleteTarget !== null}
      title={deleteTarget?.bulk ? "删除选中的反馈？" : "删除这条反馈？"}
      description={`${deleteTarget?.label ?? "该用户"} 的反馈及对话记录将被永久删除，此操作无法撤销。`}
      confirmLabel={deleteTarget?.bulk ? (bulkWorking === "delete" ? "正在删除…" : "删除选中反馈") : (deletingId === deleteTarget?.threadIds[0] ? "正在删除…" : "确认删除")}
      cancelLabel="取消"
      onClose={() => { if (!deletingId && !bulkWorking) setDeleteTarget(null); }}
      onConfirm={() => void confirmDelete()}
    />
  </Section>;
}

function RuntimeSettings({ snapshot }: { snapshot: DeveloperSnapshot }) {
  return <><Section title="网络与协议"><JsonBlock value={snapshot.web} /></Section><Section title="Workspace 本地数据权限"><div className="developer-list">{snapshot.workspace.roots.map((root) => <article key={root.name}><Database size={18} /><span><strong>{root.name}</strong><small>{root.path}</small></span><StatusPill ok={root.exists}>{root.exists ? "可用" : "未创建"}</StatusPill></article>)}</div></Section><Section title="敏感配置规则" hint="浏览器只能读取脱敏快照。"><div className="developer-callout"><ShieldCheck /><p>Provider 密钥、MCP headers/env、Cookie secret 和 Authorization 字段不会通过开发者 API 返回。配置写入继续由本地 YAML/.env 管理。</p></div></Section></>;
}

export function ReleaseNotes() {
  const [items, setItems] = useState<ReleaseNoteEntry[] | null>(null);
  const [error, setError] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [version, setVersion] = useState("");
  const [releasedAt, setReleasedAt] = useState("");
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState<"draft" | "published">("published");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setItems(null); setError("");
    try { setItems((await api.listReleaseNotes()).items); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, []);
  useEffect(() => { queueMicrotask(() => void load()); }, [load]);

  const resetForm = () => { setEditingId(null); setVersion(""); setReleasedAt(""); setNotes(""); setStatus("published"); setMessage(""); };
  const startEdit = (item: ReleaseNoteEntry) => { setEditingId(item.id); setVersion(item.version); setReleasedAt(item.released_at.slice(0, 10)); setNotes(item.notes.join("\n")); setStatus(item.status); setMessage(""); };
  const save = async () => {
    const note: Omit<ReleaseNoteEntry, "id"> = {
      version,
      released_at: releasedAt,
      notes: notes.split("\n").map((item) => item.trim()).filter(Boolean),
      status,
    };
    try {
      if (editingId) await api.updateReleaseNote(editingId, note);
      else await api.createReleaseNote(note);
      resetForm(); await load();
    } catch (reason) { setMessage(reason instanceof Error ? reason.message : String(reason)); }
  };
  const remove = async (item: ReleaseNoteEntry) => {
    if (!confirm(`删除发布说明 v${item.version}？`)) return;
    try { await api.deleteReleaseNote(item.id); await load(); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : String(reason)); }
  };

  return <><Section title="发布说明" hint="每个版本一条记录；学生端「版本与更新」只展示已发布的条目。版本号来自构建，这里只需维护更新与修复文案。">
    {error && <div className="developer-error"><ShieldCheck /><strong>无法读取发布说明</strong><p>{error}</p></div>}
    <div className="developer-editor">
      <div className="developer-inline-form"><input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="版本，例如 1.0.0" disabled={Boolean(editingId)} /><input type="date" value={releasedAt} onChange={(event) => setReleasedAt(event.target.value)} aria-label="发布日期" /></div>
      <textarea value={notes} onChange={(event) => setNotes(event.target.value)} spellCheck={false} placeholder="每行一条更新与修复说明" />
      <div><select value={status} onChange={(event) => setStatus(event.target.value as "draft" | "published")} aria-label="发布状态"><option value="draft">草稿</option><option value="published">已发布</option></select><button type="button" onClick={() => void save()} disabled={!version.trim() || !notes.trim()}>{editingId ? "保存修改" : "新建发布说明"}</button>{editingId && <button type="button" onClick={resetForm}>取消编辑</button>}{message && <small>{message}</small>}</div>
    </div>
    <div className="developer-list">{(items ?? []).map((item) => <article key={item.id}><Newspaper size={18} /><span><strong>v{item.version} · {item.released_at.slice(0, 10)}</strong><small>{item.notes.join("；")}</small></span><StatusPill ok={item.status === "published"}>{item.status === "published" ? "已发布" : "草稿"}</StatusPill><button type="button" onClick={() => startEdit(item)}>编辑</button><button className="danger" type="button" onClick={() => void remove(item)}>删除</button></article>)}</div>
    {items && items.length === 0 && <div className="developer-empty"><Newspaper /><strong>暂无发布说明</strong><p>新建一条记录，学生端即可在「版本与更新」中看到。</p></div>}
  </Section></>;
}

export function DeveloperWorkspace({ page: routedPage, onNavigate }: { page?: DeveloperPage; onNavigate?: (page: DeveloperPage) => void }) {
  const [localPage, setPage] = useState<DeveloperPage>(routedPage ?? currentPage);
  const page = routedPage ?? localPage;
  const [snapshot, setSnapshot] = useState<DeveloperSnapshot | null>(null);
  const [snapshotError, setSnapshotError] = useState("");
  const [visiblePages, setVisiblePages] = useState<Set<DeveloperPage>>(new Set());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [feedbackThreads, setFeedbackThreads] = useState<FeedbackThreadSummary[]>([]);
  const [feedbackSelectedId, setFeedbackSelectedId] = useState<string | null>(null);
  const [feedbackTotal, setFeedbackTotal] = useState(0);
  const [feedbackOffset, setFeedbackOffset] = useState(0);
  const [feedbackSearch, setFeedbackSearch] = useState("");
  const [feedbackStatus, setFeedbackStatus] = useState<FeedbackStatus | "">("");
  const [feedbackCategory, setFeedbackCategory] = useState<FeedbackCategory | "">("");
  const [feedbackPriority, setFeedbackPriority] = useState<FeedbackPriority | "">("");
  const [feedbackSort, setFeedbackSort] = useState<"latest" | "oldest" | "unread">("latest");
  const [feedbackLoadError, setFeedbackLoadError] = useState("");
  const [managementRefreshToken, setManagementRefreshToken] = useState(0);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const feedbackQueryRef = useRef({ offset: 0, search: "", status: "" as FeedbackStatus | "", category: "" as FeedbackCategory | "", priority: "" as FeedbackPriority | "", sort: "latest" as "latest" | "oldest" | "unread" });
  useEffect(() => {
    feedbackQueryRef.current = { offset: feedbackOffset, search: feedbackSearch, status: feedbackStatus, category: feedbackCategory, priority: feedbackPriority, sort: feedbackSort };
  }, [feedbackOffset, feedbackSearch, feedbackStatus, feedbackCategory, feedbackPriority, feedbackSort]);
  const updateFeedbackThreads = useCallback((items: FeedbackThreadSummary[]) => {
    setFeedbackThreads(items);
    // Keep the reading pane closed until the developer explicitly chooses a
    // person, like an email inbox. A refresh keeps the current message open
    // only while that thread remains in the current result set.
    setFeedbackSelectedId((current) => current && items.some((item) => item.thread_id === current) ? current : null);
  }, []);
  const fetchFeedback = useCallback(async (query = feedbackQueryRef.current) => {
    setFeedbackLoading(true);
    try {
      const result = await api.listFeedback({ limit: FEEDBACK_PAGE_SIZE, offset: query.offset, q: query.search || undefined, status: query.status || undefined, category: query.category || undefined, priority: query.priority || undefined, sort: query.sort });
      if (result.items.length === 0 && result.total > 0 && query.offset >= result.total) {
        setFeedbackOffset(Math.floor((result.total - 1) / FEEDBACK_PAGE_SIZE) * FEEDBACK_PAGE_SIZE);
        return;
      }
      updateFeedbackThreads(result.items);
      setFeedbackTotal(result.total);
      setFeedbackLoadError("");
    }
    catch (reason) {
      // Keep the last list while offline, but surface the failure instead of
      // letting a 403/500 read as "no feedback yet".
      setFeedbackLoadError(reason instanceof Error ? reason.message : String(reason));
    }
    finally { setFeedbackLoading(false); }
  }, [updateFeedbackThreads]);
  const refreshFeedback = useCallback(async () => { await fetchFeedback(); }, [fetchFeedback]);
  const changeFeedbackSearch = useCallback((value: string) => {
    setFeedbackSearch(value);
    setFeedbackOffset(0);
  }, []);
  const changeFeedbackStatus = useCallback((value: FeedbackStatus | "") => { setFeedbackStatus(value); setFeedbackOffset(0); }, []);
  const changeFeedbackCategory = useCallback((value: FeedbackCategory | "") => { setFeedbackCategory(value); setFeedbackOffset(0); }, []);
  const changeFeedbackPriority = useCallback((value: FeedbackPriority | "") => { setFeedbackPriority(value); setFeedbackOffset(0); }, []);
  const changeFeedbackSort = useCallback((value: "latest" | "oldest" | "unread") => { setFeedbackSort(value); setFeedbackOffset(0); }, []);
  const deleteFeedback = useCallback(async (threadId: string) => {
    await api.deleteFeedback(threadId);
    setFeedbackSelectedId((current) => current === threadId ? null : current);
    await refreshFeedback();
  }, [refreshFeedback]);
  const markFeedbackRead = useCallback(async (threadId: string) => {
    const thread = feedbackThreads.find((item) => item.thread_id === threadId);
    if (!thread?.latest) return;
    await api.markFeedbackRead(threadId, thread.latest.id);
    await refreshFeedback();
  }, [feedbackThreads, refreshFeedback]);
  const markFeedbackThreadsRead = useCallback(async (threadIds: string[]) => {
    await api.markFeedbackThreadsRead(threadIds);
    await refreshFeedback();
  }, [refreshFeedback]);
  const deleteFeedbackThreads = useCallback(async (threadIds: string[]) => {
    await api.deleteFeedbackThreads(threadIds);
    setFeedbackSelectedId((current) => current && threadIds.includes(current) ? null : current);
    await refreshFeedback();
  }, [refreshFeedback]);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      await ensureAuth();
      // Entry and navigation follow the server-side menu projection: a role
      // grants the shell only through visible /developer/* menus, never via a
      // hardcoded role or permission name.
      const menuResult = await api.listVisibleMenus();
      const pages = new Set(
        menuResult.items
          .filter((item) => item.client_scope === "developer")
          .map((item) => pageForMenuRoute(item.route_path))
          .filter((item): item is DeveloperPage => item !== null),
      );
      if (pages.size === 0) throw new Error("当前账户没有开发者工作台访问权限");
      setVisiblePages(pages);
      // The snapshot is control-plane data (SYSTEM_RUNTIME_INSPECT); pages
      // outside SNAPSHOT_PAGES must keep working without it.
      if (SNAPSHOT_PAGES.some((candidate) => pages.has(candidate))) {
        try { setSnapshot(await api.getDeveloperSnapshot()); }
        catch (reason) { setSnapshotError(reason instanceof Error ? reason.message : String(reason)); }
      }
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  }, []);
  const refreshWorkspace = useCallback(async () => {
    setManagementRefreshToken((current) => current + 1);
    await load();
  }, [load]);
  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  useEffect(() => {
    if (page !== "feedback") return;
    queueMicrotask(() => void refreshFeedback());
  }, [page, feedbackOffset, feedbackSearch, feedbackStatus, feedbackCategory, feedbackPriority, feedbackSort, refreshFeedback]);
  useEffect(() => {
    if (page !== "feedback") return undefined;
    let timer: number | undefined;
    const startTimer = () => {
      if (timer === undefined) timer = window.setInterval(() => {
        if (document.visibilityState === "visible") void refreshFeedback();
      }, 10_000);
    };
    const stopTimer = () => {
      if (timer !== undefined) { window.clearInterval(timer); timer = undefined; }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") { void refreshFeedback(); startTimer(); }
      else stopTimer();
    };
    startTimer();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => { document.removeEventListener("visibilitychange", onVisibilityChange); stopTimer(); };
  }, [page, refreshFeedback]);
  const navigate = (next: DeveloperPage) => { if (onNavigate) onNavigate(next); else { history.pushState({}, "", next === "overview" ? "/developer" : `/developer/${next}`); setPage(next); } };
  const content = useMemo(() => {
    // Pages that own their data sources render regardless of the snapshot.
    if (page === "release-notes") return <ReleaseNotes />;
    if (page === "feedback") return <Feedback threads={feedbackThreads} total={feedbackTotal} pageSize={FEEDBACK_PAGE_SIZE} offset={feedbackOffset} search={feedbackSearch} loadError={feedbackLoadError} loading={feedbackLoading} selectedId={feedbackSelectedId} onSelect={(threadId) => setFeedbackSelectedId(threadId)} onSearchChange={changeFeedbackSearch} onOffsetChange={setFeedbackOffset} onDelete={deleteFeedback} onMarkRead={markFeedbackRead} onBulkMarkRead={markFeedbackThreadsRead} onBulkDelete={deleteFeedbackThreads} refresh={refreshFeedback} statusFilter={feedbackStatus} categoryFilter={feedbackCategory} priorityFilter={feedbackPriority} sort={feedbackSort} onStatusFilterChange={changeFeedbackStatus} onCategoryFilterChange={changeFeedbackCategory} onPriorityFilterChange={changeFeedbackPriority} onSortChange={changeFeedbackSort} />;
    if (page === "users") return <UserManagementPage onShellRefresh={load} refreshToken={managementRefreshToken} />;
    if (page === "roles") return <RoleManagementPageV2 onShellRefresh={load} refreshToken={managementRefreshToken} />;
    if (page === "audit") return <AuditLogPageV2 />;
    if (page === "sessions") return <AgentSessionListPageV2 />;
    if (page === "quotas") return <QuotaManagementPage />;
    if (!snapshot) return <div className="developer-error"><ShieldCheck /><strong>无法读取运行时快照</strong><p>{snapshotError || "当前身份可能缺少运行时检查权限；其余页面不受影响。"}</p></div>;
    if (page === "agents") return <Agents snapshot={snapshot} refresh={load} />;
    if (page === "tools") return <Tools snapshot={snapshot} refresh={load} />;
    if (page === "models") return <Models snapshot={snapshot} />;
    if (page === "mcp") return <Mcp snapshot={snapshot} refresh={load} />;
    if (page === "skills") return <Skills snapshot={snapshot} refresh={load} />;
    if (page === "automations") return <Automations snapshot={snapshot} />;
    if (page === "settings") return <RuntimeSettings snapshot={snapshot} />;
    return <Overview snapshot={snapshot} />;
  }, [changeFeedbackCategory, changeFeedbackPriority, changeFeedbackSearch, changeFeedbackSort, changeFeedbackStatus, deleteFeedback, deleteFeedbackThreads, feedbackCategory, feedbackLoadError, feedbackLoading, feedbackOffset, feedbackPriority, feedbackSearch, feedbackSelectedId, feedbackSort, feedbackStatus, feedbackThreads, feedbackTotal, markFeedbackRead, markFeedbackThreadsRead, load, managementRefreshToken, page, refreshFeedback, snapshot, snapshotError]);
  const accessDenied = !loading && visiblePages.size > 0 && !visiblePages.has(page);
  return <div className="developer-shell"><aside className="developer-nav"><div className="developer-brand"><TerminalSquare /><span><strong>NLP Developer</strong><small>Control plane · 8765</small></span></div><nav>{NAV.filter(({ page: itemPage }) => visiblePages.has(itemPage)).map(({ page: itemPage, label, icon: Icon }) => <button className={page === itemPage ? "active" : ""} type="button" key={itemPage} onClick={() => navigate(itemPage)}><Icon size={17} />{label}</button>)}</nav><a href="/"><ChevronLeft size={16} />返回学生模式</a></aside><main className="developer-main"><header className="developer-topbar"><div><Globe2 size={16} /><span>当前开发者</span></div><button type="button" onClick={() => { if (page === "feedback") void refreshFeedback(); void refreshWorkspace(); }} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={16} />刷新</button></header><div className="developer-content">{loading && visiblePages.size === 0 ? <div className="developer-loading"><RefreshCw className="spin" />正在读取运行时…</div> : error ? <div className="developer-error"><ShieldCheck /><strong>无法进入开发者模式</strong><p>{error}</p></div> : accessDenied ? <div className="developer-error"><ShieldCheck /><strong>无权访问该页面</strong><p>当前身份未被授予此菜单；请从左侧导航选择可用的页面。</p></div> : content}</div></main></div>;
}
