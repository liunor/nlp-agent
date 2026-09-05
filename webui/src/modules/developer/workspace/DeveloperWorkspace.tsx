import {
  Activity, AppWindow, Bot, Box, ChevronDown, ChevronLeft, ChevronRight, Clock3, Code2, Database,
  ExternalLink, FileKey2, Gauge, Globe2, Mail, MailOpen, Newspaper, PlugZap,
  Inbox, MessageCircle, RefreshCw, Search, Settings2, ShieldCheck, Sparkles, TerminalSquare, Trash2, User, Wrench,
  Users, LayoutList, WalletCards,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ensureAuth } from "@/platform/http/api";
import type { DeveloperRuntimeHealth, DeveloperSnapshot, FeedbackCategory, FeedbackPriority, FeedbackStatus, FeedbackThread, FeedbackThreadSummary, ReleaseNoteEntry } from "@/shared/types";
import { UserManagementPage } from "@/modules/admin/UserManagementPage";
import { RoleManagementPageV2 } from "@/modules/admin/RoleManagementPageV2";
import { MenuManagementPageV2 } from "@/modules/admin/MenuManagementPageV2";
import { MarkdownContent } from "@/modules/student/components/MarkdownContent";
import { monitorUrl } from "@/monitor/monitor-helpers";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { QuotaManagementPage } from "@/modules/quota/QuotaManagementPage";

export type DeveloperPage = "overview" | "agents" | "tools" | "models" | "mcp" | "skills" | "release-notes" | "automations" | "feedback" | "settings" | "users" | "roles" | "menus" | "quotas";

type NavGroup = "control" | "integrations" | "operations";
const NAV: Array<{ page: DeveloperPage; label: string; icon: typeof Gauge; group: NavGroup }> = [
  { page: "overview", label: "工作台", icon: Gauge, group: "control" },
  { page: "agents", label: "Agent 与 Worker", icon: Bot, group: "control" },
  { page: "tools", label: "工具", icon: Wrench, group: "control" },
  { page: "models", label: "模型与 Provider", icon: Sparkles, group: "control" },
  { page: "mcp", label: "MCP", icon: PlugZap, group: "integrations" },
  { page: "skills", label: "Skills", icon: Code2, group: "integrations" },
  { page: "automations", label: "Apps 与自动化", icon: Clock3, group: "integrations" },
  { page: "release-notes", label: "发布说明", icon: Newspaper, group: "operations" },
  { page: "feedback", label: "意见反馈", icon: Mail, group: "operations" },
  { page: "quotas", label: "额度管理", icon: WalletCards, group: "operations" },
  { page: "settings", label: "运行诊断", icon: Settings2, group: "operations" },
  { page: "users", label: "用户管理", icon: Users, group: "operations" },
  { page: "roles", label: "角色权限", icon: ShieldCheck, group: "operations" },
  { page: "menus", label: "菜单管理", icon: LayoutList, group: "operations" },
];

const NAV_GROUPS: Array<{ key: NavGroup; label: string }> = [
  { key: "control", label: "控制面" },
  { key: "integrations", label: "能力与集成" },
  { key: "operations", label: "运营与治理" },
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
    <section className="developer-hero"><div className="developer-hero-copy"><span className="developer-eyebrow">DEVELOPER CONTROL PLANE · 8765</span><h1>后端基础工作台</h1><p>从一个工作台掌握 Agent、模型、工具和运行时边界。学生界面不会显示这些内部信息。</p><div className="developer-hero-actions"><a href={monitorOrigin} target="_blank" rel="noreferrer"><Gauge size={16} />打开监控平台</a><span><i className={`developer-live-dot ${runtime.status === "ok" ? "on" : ""}`} />{runtime.status === "ok" ? "Runtime 正常" : "Runtime 待检查"}</span></div></div><div className="developer-hero-mark"><ShieldCheck size={30} /><small>CONTROL<br />PLANE</small></div></section>
    <div className="developer-kpis">
      <article><div><Activity /><span>Gateway 状态</span></div><strong>{String(runtime.status ?? "unknown")}</strong><small>运行时心跳</small></article>
      <article><div><Bot /><span>活跃 Turn</span></div><strong>{String(runtime.active_turns ?? 0)}</strong><small>当前请求上下文</small></article>
      <article><div><Database /><span>持久事件</span></div><strong>{Number(runtime.durable_events ?? 0).toLocaleString()}</strong><small>已写入的运行事件</small></article>
      <article><div><PlugZap /><span>工具目录</span></div><strong>v{snapshot.tools.catalog_revision}</strong><small>可供 Agent 发现</small></article>
    </div>
    <Section title="能力状态" hint="未配置的通用工作台能力会明确显示，不伪造可用状态。"><div className="developer-card-grid">{Object.entries(snapshot.features).map(([name, feature]) => <article className="developer-card" key={name}><div><AppWindow size={18} /><strong>{name}</strong></div><StatusPill ok={feature.available}>{feature.available ? "已启用" : "未启用"}</StatusPill><p>{feature.reason}</p><small className="developer-card-link">查看能力详情 <ChevronRight size={14} /></small></article>)}</div></Section>
    <Section title="独立观测平台" hint="Trace、Token、错误和实时事件在隔离端口展示，审计日志也归入这里。"><a className="developer-monitor-link" href={monitorOrigin} target="_blank" rel="noreferrer"><Gauge size={20} /><span><strong>打开 Observability Monitor</strong><small>{new URL(monitorOrigin).host} · 运行链路、会话、审计</small></span><ExternalLink size={16} /></a></Section>
  </div>;
}

type WorkerProfileDraft = {
  description: string;
  model: string;
  execution_mode: "react" | "one_shot";
  requires_native_search: boolean;
  inherit_tool_policy: boolean;
  skills: string[];
  capabilities: string[];
  allowed_tools: string[];
  denied_tools: string[];
};

function workerProfileDraft(value: unknown = {}): WorkerProfileDraft {
  const source = asRecord(value);
  return {
    description: String(source.description ?? ""),
    model: String(source.model ?? ""),
    execution_mode: source.execution_mode === "one_shot" ? "one_shot" : "react",
    requires_native_search: Boolean(source.requires_native_search),
    inherit_tool_policy: source.inherit_tool_policy !== false,
    skills: asStringList(source.skills),
    capabilities: asStringList(source.capabilities),
    allowed_tools: asStringList(source.allowed_tools),
    denied_tools: asStringList(source.denied_tools),
  };
}

function csvValue(value: string[]): string {
  return value.join(", ");
}

function toolGroup(tool: Record<string, unknown>): "nlp" | "sandbox" | "other" {
  const name = String(tool.name ?? "");
  const category = String(tool.category ?? "").toLowerCase();
  const capabilities = asStringList(tool.capabilities);
  if (category === "nlp" || name.startsWith("nlp_") || capabilities.some((item) => item.startsWith("nlp."))) return "nlp";
  if (category === "sandbox" || String(tool.provider ?? "") === "sandbox" || name.startsWith("sandbox_")) return "sandbox";
  return "other";
}

export function Agents({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  const agents = asRecord(snapshot.agents);
  const profiles = asRecord(agents.profiles);
  const profileNames = Object.keys(profiles);
  const presetNames = Object.keys(snapshot.models.presets);
  const [selectedName, setSelectedName] = useState(profileNames[0] ?? "");
  const [isNew, setIsNew] = useState(false);
  const [draft, setDraft] = useState<WorkerProfileDraft>(() => workerProfileDraft(profiles[profileNames[0]]));
  const [message, setMessage] = useState("");
  const coordinatorRuntime = asRecord(asRecord(agents.runtime).coordinator);
  const workerRuntime = asRecord(asRecord(agents.runtime).worker);
  const selectedProfile = selectedName && !isNew ? asRecord(profiles[selectedName]) : null;

  const selectProfile = (name: string) => {
    setSelectedName(name);
    setIsNew(false);
    setDraft(workerProfileDraft(profiles[name]));
    setMessage("");
  };
  const newProfile = () => {
    setSelectedName("new-worker");
    setIsNew(true);
    setDraft(workerProfileDraft());
    setMessage("");
  };
  const update = <K extends keyof WorkerProfileDraft>(key: K, value: WorkerProfileDraft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const save = async () => {
    const name = selectedName.trim();
    if (!name) { setMessage("请先填写 Profile 名称"); return; }
    try {
      await api.saveWorkerProfile(name, {
        ...draft,
        model: draft.model.trim() || null,
        skills: draft.skills,
        capabilities: draft.capabilities,
        allowed_tools: draft.allowed_tools,
        denied_tools: draft.denied_tools,
      });
      setIsNew(false);
      setMessage("已保存，新的 Worker 会按此 Profile 运行");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  };
  const remove = async () => {
    if (!selectedName || !confirm(`删除 Worker Profile ${selectedName}？`)) return;
    try {
      await api.deleteWorkerProfile(selectedName);
      setSelectedName("");
      setDraft(workerProfileDraft());
      setMessage("已删除");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  };

  return <div className="developer-control-page">
    <div className="developer-control-heading"><div><span className="developer-eyebrow">AGENT RUNTIME</span><h1>Agent 与 Worker</h1><p>Agent 负责拆解和调度，Worker 按 Profile 执行具体任务。这里配置 Worker 的用途、模型、Skill 和工具边界。</p></div><button className="developer-control-primary" type="button" onClick={newProfile}><Bot size={15} />新建 Worker Profile</button></div>
    <section className="developer-agent-flow"><div className="developer-flow-node"><span>01</span><div><strong>Coordinator Agent</strong><p>理解任务、拆解步骤并调度 Worker。</p></div><small>最多 {String(coordinatorRuntime.max_iterations ?? "-")} 轮 · {String(coordinatorRuntime.max_tool_calls ?? "-")} 次工具调用</small></div><ChevronRight className="developer-flow-arrow" size={18} /><div className="developer-flow-node"><span>02</span><div><strong>Worker Agent</strong><p>根据 Profile 的 SOP、能力和授权工具完成子任务。</p></div><small>注入上限 {String(workerRuntime.max_injections ?? "-")} · 结果回传 Gateway</small></div></section>
    <div className="developer-agents-layout"><aside className="developer-profile-directory"><div className="developer-control-section-heading"><div><h2>Worker Profiles</h2><p>{profileNames.length} 个可用配置</p></div></div>{profileNames.length ? profileNames.map((name) => { const profile = asRecord(profiles[name]); return <button className={`developer-profile-item ${selectedName === name && !isNew ? "active" : ""}`} type="button" aria-pressed={selectedName === name && !isNew} key={name} onClick={() => selectProfile(name)}><span className="developer-profile-item-icon"><Bot size={15} /></span><span><strong>{name}</strong><small>{String(profile.description ?? "未填写用途说明")}</small></span><StatusPill ok={!profile.requires_native_search || Boolean(profile.model)}>{profile.requires_native_search ? "联网" : "标准"}</StatusPill></button>; }) : <div className="developer-control-empty"><Bot size={18} /><strong>还没有 Worker Profile</strong><p>从一个清晰的任务用途开始创建。</p></div>}</aside>
      <section className="developer-profile-editor"><div className="developer-control-section-heading"><div><h2>{isNew ? "新建 Worker Profile" : selectedName || "选择一个 Profile"}</h2><p>{selectedProfile ? "修改后只影响新建的 Worker；已运行任务保留原授权快照。" : "Profile 是 Worker 的可复用运行合同。"}</p></div>{selectedProfile && <StatusPill ok={true}>已加载</StatusPill>}</div>{(selectedName || isNew) && <><label className="developer-control-field">Profile 名称<input aria-label="Profile 名称" value={selectedName} disabled={!isNew} onChange={(event) => setSelectedName(event.target.value)} /></label><label className="developer-control-field">用途说明<textarea aria-label="用途说明" value={draft.description} onChange={(event) => update("description", event.target.value)} placeholder="它解决什么任务？什么时候应该使用？" /></label><div className="developer-control-field-grid"><label className="developer-control-field">模型预设<select aria-label="模型预设" value={draft.model} onChange={(event) => update("model", event.target.value)}><option value="">跟随 Worker 路由</option>{presetNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><label className="developer-control-field">执行方式<select aria-label="执行方式" value={draft.execution_mode} onChange={(event) => update("execution_mode", event.target.value as WorkerProfileDraft["execution_mode"])}><option value="react">React · 可连续使用工具</option><option value="one_shot">One-shot · 一次性生成</option></select></label></div><div className="developer-control-checks"><label><input type="checkbox" checked={draft.inherit_tool_policy} onChange={(event) => update("inherit_tool_policy", event.target.checked)} />继承 Worker 全局工具策略</label><label><input type="checkbox" checked={draft.requires_native_search} onChange={(event) => update("requires_native_search", event.target.checked)} />需要 Provider 原生联网</label></div><div className="developer-control-field-grid"><label className="developer-control-field">Skills<input aria-label="Profile Skills" value={csvValue(draft.skills)} onChange={(event) => update("skills", commaList(event.target.value))} placeholder="research, teacher" /></label><label className="developer-control-field">能力 Capabilities<input aria-label="Profile 能力" value={csvValue(draft.capabilities)} onChange={(event) => update("capabilities", commaList(event.target.value))} placeholder="nlp.analyze, web.fetch" /></label><label className="developer-control-field">额外允许工具<input aria-label="Profile 允许工具" value={csvValue(draft.allowed_tools)} onChange={(event) => update("allowed_tools", commaList(event.target.value))} placeholder="tool_name, another_tool" /></label><label className="developer-control-field">拒绝工具<input aria-label="Profile 拒绝工具" value={csvValue(draft.denied_tools)} onChange={(event) => update("denied_tools", commaList(event.target.value))} placeholder="危险工具名" /></label></div><div className="developer-control-actions"><button className="developer-control-primary" type="button" onClick={() => void save()}>保存 Profile</button>{selectedProfile && <button className="developer-control-danger" type="button" onClick={() => void remove()}>删除 Profile</button>}{message && <small role="status">{message}</small>}</div></>}</section></div>
    <Section title="运行边界" hint="这些是 Agent/Worker 的全局预算，防止单次任务无限循环；具体 Profile 只负责选择模型和授权。"><div className="developer-limit-grid"><article><strong>Coordinator</strong><span>最大迭代 {String(coordinatorRuntime.max_iterations ?? "未配置")}</span><span>最大工具调用 {String(coordinatorRuntime.max_tool_calls ?? "未配置")}</span></article><article><strong>Worker</strong><span>最大注入 {String(workerRuntime.max_injections ?? "未配置")}</span><span>最大结果字符 {String(workerRuntime.max_tool_result_chars ?? "未配置")}</span></article></div></Section>
  </div>;
}

export function Tools({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  const groups = [{ key: "all", label: "全部工具" }, { key: "nlp", label: "NLP 专属" }, { key: "sandbox", label: "Sandbox" }, { key: "other", label: "其他" }] as const;
  const [group, setGroup] = useState<(typeof groups)[number]["key"]>(() => { const value = new URLSearchParams(location.search).get("group"); return groups.some((item) => item.key === value) ? value as (typeof groups)[number]["key"] : "all"; });
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<"coordinator" | "worker">("worker");
  const [selectedName, setSelectedName] = useState("");
  const [policies, setPolicies] = useState<Record<string, unknown>>(() => asRecord(snapshot.tools.policies));
  const [message, setMessage] = useState("");
  const items = snapshot.tools.items;
  const counts = Object.fromEntries(groups.map((item) => [item.key, item.key === "all" ? items.length : items.filter((tool) => toolGroup(tool) === item.key).length]));
  const visibleTools = items.filter((tool) => {
    const matchesGroup = group === "all" || toolGroup(tool) === group;
    const text = `${String(tool.name ?? "")} ${String(tool.description ?? "")} ${String(tool.provider ?? "")}`.toLowerCase();
    return matchesGroup && (!query.trim() || text.includes(query.trim().toLowerCase()));
  });
  const selectedTool = visibleTools.find((tool) => String(tool.name) === selectedName) ?? visibleTools[0];
  const rolePolicy = asRecord(policies[role]);
  const allowedTools = asStringList(rolePolicy.allowed_tools);
  const setGroupAndRoute = (next: typeof group) => {
    setGroup(next);
    const url = new URL(location.href);
    if (next === "all") url.searchParams.delete("group"); else url.searchParams.set("group", next);
    history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  };
  const toggleGrant = (name: string, checked: boolean) => {
    setPolicies((current) => {
      const next = { ...current };
      const policy = { ...asRecord(next[role]) };
      const currentNames = asStringList(policy.allowed_tools).filter((item) => item !== "*");
      policy.allowed_tools = checked ? [...new Set([...currentNames, name])] : currentNames.filter((item) => item !== name);
      next[role] = policy;
      return next;
    });
  };
  const savePolicy = async () => {
    try { await api.updateToolPolicies(policies); setMessage(`${role === "worker" ? "Worker" : "Coordinator"} 权限已保存，新建任务会使用新策略`); await refresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  };
  return <div className="developer-control-page">
    <div className="developer-control-heading"><div><span className="developer-eyebrow">TOOL CATALOG</span><h1>工具</h1><p>工具目录负责“能做什么”，权限策略负责“谁可以用”。按来源和能力分类浏览，再为 Coordinator 或 Worker 配置授权。</p></div><span className="developer-control-revision">CATALOG v{snapshot.tools.catalog_revision}</span></div>
    <div className="developer-tool-stats"><article><strong>{items.length}</strong><span>已注册工具</span></article><article><strong>{counts.nlp}</strong><span>NLP 教学工具</span></article><article><strong>{counts.sandbox}</strong><span>Sandbox 工具</span></article><article><strong>{items.filter((tool) => String(tool.risk) === "high" || String(tool.risk) === "critical").length}</strong><span>高风险入口</span></article></div>
    <div className="developer-tool-toolbar"><div className="developer-control-tabs" role="tablist" aria-label="工具来源分类">{groups.map((item) => <button type="button" role="tab" aria-selected={group === item.key} key={item.key} onClick={() => setGroupAndRoute(item.key)}>{item.label}<span>{counts[item.key]}</span></button>)}</div><label className="developer-tool-search"><Search size={15} /><input aria-label="搜索工具" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工具名、描述或 Provider" /></label></div>
    <div className="developer-tools-layout"><aside className="developer-tool-directory"><div className="developer-control-section-heading"><div><h2>{groups.find((item) => item.key === group)?.label}</h2><p>{visibleTools.length} 个结果</p></div></div>{visibleTools.length ? visibleTools.map((tool) => { const name = String(tool.name); return <button className={`developer-tool-item ${selectedTool?.name === name ? "active" : ""}`} type="button" aria-pressed={selectedTool?.name === name} key={name} onClick={() => setSelectedName(name)}><span><strong>{name}</strong><small>{String(tool.description ?? "暂无说明")}</small></span><StatusPill ok={String(tool.risk ?? "low") === "low"}>{String(tool.risk ?? "low")}</StatusPill></button>; }) : <div className="developer-control-empty"><Wrench size={18} /><strong>没有匹配工具</strong><p>换一个分类或搜索词。</p></div>}</aside>
      <section className="developer-tool-detail">{selectedTool ? <><div className="developer-tool-detail-heading"><div><span className="developer-eyebrow">TOOL DETAIL</span><h2>{String(selectedTool.name)}</h2><p>{String(selectedTool.description ?? "暂无描述")}</p></div><StatusPill ok={String(selectedTool.risk ?? "low") === "low"}>{String(selectedTool.risk ?? "low")} risk</StatusPill></div><div className="developer-tool-facts"><span><strong>来源</strong>{String(selectedTool.source ?? "-")}</span><span><strong>Provider</strong>{String(selectedTool.provider ?? "-")}</span><span><strong>类别</strong>{String(selectedTool.category ?? "general")}</span><span><strong>超时</strong>{String(selectedTool.timeout_s ?? "-")}s</span><span><strong>作用域</strong>{asStringList(selectedTool.scopes).join("、") || "-"}</span><span><strong>能力</strong>{asStringList(selectedTool.capabilities).join("、") || "-"}</span></div><div className="developer-tool-permissions"><div className="developer-control-section-heading"><div><h3>角色权限</h3><p>只修改该角色的允许工具；能力和拒绝规则仍按完整策略一起保存。</p></div><div className="developer-role-tabs">{(["worker", "coordinator"] as const).map((item) => <button type="button" className={role === item ? "active" : ""} key={item} onClick={() => setRole(item)}>{item === "worker" ? "Worker" : "Coordinator"}</button>)}</div></div><div className="developer-tool-grant-list">{visibleTools.map((tool) => { const name = String(tool.name); return <label key={name}><input type="checkbox" aria-label={`授权 ${name}`} checked={allowedTools.includes("*") || allowedTools.includes(name)} onChange={(event) => toggleGrant(name, event.target.checked)} />{name}</label>; })}</div><div className="developer-control-actions"><button className="developer-control-primary" type="button" onClick={() => void savePolicy()}>保存 {role === "worker" ? "Worker" : "Coordinator"} 权限</button>{message && <small role="status">{message}</small>}</div></div></> : <div className="developer-control-empty"><Wrench size={20} /><strong>选择一个工具</strong><p>查看它的来源、风险、能力和角色授权。</p></div>}</section></div>
    <Section title="自定义工具 Provider" hint="自定义 Python 工具仍使用 Manifest 注册；保存扩展配置后需要重启 Runtime 才会加载或卸载模块。"><JsonEditor value={snapshot.tools.custom} label="保存自定义 Tool 配置" onSave={async (value) => { const result = await api.updateCustomTools(value); await refresh(); alert(result.reason); }} /></Section>
  </div>;
}

type ModelProviderDraft = { adapter: string; base_url: string; api_key_env: string };
type ModelPresetDraft = { model: string; thinking_enabled: boolean; thinking_effort: string; max_output_tokens: number; temperature: number | null };

function modelProviderDraft(value: unknown = {}): ModelProviderDraft {
  const source = asRecord(value);
  return { adapter: String(source.adapter ?? "openai_compatible"), base_url: String(source.base_url ?? ""), api_key_env: String(source.api_key_env ?? "") };
}

function modelPresetDraft(value: unknown = {}): ModelPresetDraft {
  const source = asRecord(value);
  const thinking = asRecord(source.thinking);
  const generation = asRecord(source.generation);
  return { model: String(source.model ?? ""), thinking_enabled: Boolean(thinking.enabled), thinking_effort: String(thinking.effort ?? "none"), max_output_tokens: Number(generation.max_output_tokens ?? 16000), temperature: generation.temperature == null ? null : Number(generation.temperature) };
}

function modelPresetConfig(original: unknown, draft: ModelPresetDraft): Record<string, unknown> {
  const source = asRecord(original);
  const thinking = asRecord(source.thinking);
  const generation = asRecord(source.generation);
  return { ...source, model: draft.model, thinking: { ...thinking, enabled: draft.thinking_enabled, effort: draft.thinking_enabled ? draft.thinking_effort : "none" }, generation: { ...generation, max_output_tokens: draft.max_output_tokens, temperature: draft.temperature } };
}

export function Models({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  const providerNames = Object.keys(snapshot.models.providers);
  const presetNames = Object.keys(snapshot.models.presets);
  const routeNames = Object.keys(snapshot.models.routes);
  const modelNames = Object.keys(snapshot.models.models);
  const [view, setView] = useState<"providers" | "presets" | "routes">("providers");
  const [providerName, setProviderName] = useState(providerNames[0] ?? "");
  const [providerDraft, setProviderDraft] = useState<ModelProviderDraft>(() => modelProviderDraft(snapshot.models.providers[providerNames[0]]));
  const [presetName, setPresetName] = useState(presetNames[0] ?? "");
  const [presetDraft, setPresetDraft] = useState<ModelPresetDraft>(() => modelPresetDraft(snapshot.models.presets[presetNames[0]]));
  const [routeName, setRouteName] = useState(routeNames[0] ?? "");
  const [routeDraft, setRouteDraft] = useState(() => { const route = asRecord(snapshot.models.routes[routeNames[0]]); return { primary: String(route.primary ?? ""), fallbacks: asStringList(route.fallbacks) }; });
  const [message, setMessage] = useState("");
  const selectProvider = (name: string) => { setProviderName(name); setProviderDraft(modelProviderDraft(snapshot.models.providers[name])); setMessage(""); };
  const selectPreset = (name: string) => { setPresetName(name); setPresetDraft(modelPresetDraft(snapshot.models.presets[name])); setMessage(""); };
  const selectRoute = (name: string) => { const route = asRecord(snapshot.models.routes[name]); setRouteName(name); setRouteDraft({ primary: String(route.primary ?? ""), fallbacks: asStringList(route.fallbacks) }); setMessage(""); };
  const saveProvider = async () => { try { await api.saveModelProvider(providerName, providerDraft); setMessage("Provider 已保存，新的模型请求会使用最新连接配置"); await refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); } };
  const savePreset = async () => { try { await api.saveModelPreset(presetName, modelPresetConfig(snapshot.models.presets[presetName], presetDraft)); setMessage("模型预设已保存"); await refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); } };
  const saveRoute = async () => { try { await api.saveModelRoute(routeName, routeDraft); setMessage("模型路由已保存，后续请求按新的主备链路选择"); await refresh(); } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); } };
  return <div className="developer-control-page">
    <div className="developer-control-heading"><div><span className="developer-eyebrow">MODEL ROUTING</span><h1>模型与 Provider</h1><p>Provider 是连接和密钥入口，模型是厂商模型目录，预设决定生成参数，路由决定主模型和故障转移顺序。</p></div><div className="developer-model-default"><span>默认模型档案</span><strong>{snapshot.models.default_model_profile || "未指定"}</strong></div></div>
    <div className="developer-control-tabs developer-control-tabs-wide" role="tablist" aria-label="模型配置区块"><button type="button" role="tab" aria-selected={view === "providers"} onClick={() => setView("providers")}>Provider 与模型</button><button type="button" role="tab" aria-selected={view === "presets"} onClick={() => setView("presets")}>模型预设</button><button type="button" role="tab" aria-selected={view === "routes"} onClick={() => setView("routes")}>路由与故障转移</button></div>
    {view === "providers" && <><div className="developer-model-layout"><aside className="developer-model-directory"><div className="developer-control-section-heading"><div><h2>Provider</h2><p>{providerNames.length} 个连接</p></div></div>{providerNames.map((name) => { const provider = asRecord(snapshot.models.providers[name]); return <button className={`developer-model-item ${providerName === name ? "active" : ""}`} type="button" key={name} onClick={() => selectProvider(name)}><span><strong>{name}</strong><small>{String(provider.adapter ?? "-")} · {String(provider.base_url ?? "")}</small></span><StatusPill ok={Boolean(provider.api_key_configured)}>{provider.api_key_configured ? "已就绪" : "缺少密钥"}</StatusPill></button>; })}</aside><section className="developer-model-editor"><div className="developer-control-section-heading"><div><h2>{providerName || "Provider"}</h2><p>密钥值只从环境变量读取，不会写入运行时覆盖文件或返回浏览器。</p></div>{providerName && <StatusPill ok={Boolean(asRecord(snapshot.models.providers[providerName]).api_key_configured)}>{asRecord(snapshot.models.providers[providerName]).api_key_configured ? "API Key 已配置" : "等待 API Key"}</StatusPill>}</div>{providerName && <><div className="developer-control-field-grid"><label className="developer-control-field">适配器<select aria-label={`${providerName} 适配器`} value={providerDraft.adapter} onChange={(event) => setProviderDraft((current) => ({ ...current, adapter: event.target.value }))}><option value="deepseek">DeepSeek</option><option value="qwen">Qwen</option><option value="openai_compatible">OpenAI Compatible</option></select></label><label className="developer-control-field">API Key 环境变量<input aria-label={`${providerName} API Key 环境变量`} value={providerDraft.api_key_env} onChange={(event) => setProviderDraft((current) => ({ ...current, api_key_env: event.target.value }))} /></label><label className="developer-control-field developer-control-field-wide">服务地址<input aria-label={`${providerName} 服务地址`} value={providerDraft.base_url} onChange={(event) => setProviderDraft((current) => ({ ...current, base_url: event.target.value }))} /></label></div><div className="developer-control-actions"><button className="developer-control-primary" type="button" onClick={() => void saveProvider()}>保存 Provider</button>{message && <small role="status">{message}</small>}</div></>}</section></div><Section title="模型目录" hint="模型目录是厂商提供的真实模型 ID；运行行为通过上面的 Provider、模型预设和路由来控制。"><div className="developer-model-catalog">{modelNames.map((name) => { const model = asRecord(snapshot.models.models[name]); const capabilities = asRecord(model.capabilities); return <article key={name}><div><strong>{name}</strong><small>{String(model.provider ?? "-")} · {String(model.model_id ?? "-")}</small></div><span>{Number(model.context_window_tokens ?? 0).toLocaleString()} context</span><span>{capabilities.thinking ? "支持思考" : "标准生成"}</span></article>; })}</div></Section></>}
    {view === "presets" && <section className="developer-model-editor developer-model-single"><div className="developer-control-section-heading"><div><h2>模型预设</h2><p>预设把模型、思考、输出上限和采样参数组合成可复用的运行档位。</p></div></div><label className="developer-control-field">选择预设<select aria-label="模型预设" value={presetName} onChange={(event) => selectPreset(event.target.value)}>{presetNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>{presetName && <><div className="developer-control-field-grid"><label className="developer-control-field">使用模型<select aria-label={`${presetName} 使用模型`} value={presetDraft.model} onChange={(event) => setPresetDraft((current) => ({ ...current, model: event.target.value }))}>{modelNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><label className="developer-control-field">最大输出 Token<input aria-label={`${presetName} 最大输出 Token`} type="number" min="1" value={presetDraft.max_output_tokens} onChange={(event) => setPresetDraft((current) => ({ ...current, max_output_tokens: Number(event.target.value) }))} /></label><label className="developer-control-field">Temperature<input aria-label={`${presetName} Temperature`} type="number" min="0" max="2" step="0.1" value={presetDraft.temperature ?? ""} onChange={(event) => setPresetDraft((current) => ({ ...current, temperature: event.target.value === "" ? null : Number(event.target.value) }))} /></label><label className="developer-control-field">思考强度<select aria-label={`${presetName} 思考强度`} value={presetDraft.thinking_effort} onChange={(event) => setPresetDraft((current) => ({ ...current, thinking_effort: event.target.value }))}><option value="none">关闭</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="max">Max</option></select></label></div><label className="developer-control-checks"><input type="checkbox" checked={presetDraft.thinking_enabled} onChange={(event) => setPresetDraft((current) => ({ ...current, thinking_enabled: event.target.checked }))} />启用模型思考</label><div className="developer-control-actions"><button className="developer-control-primary" type="button" onClick={() => void savePreset()}>保存模型预设</button>{message && <small role="status">{message}</small>}</div></>}</section>}
    {view === "routes" && <section className="developer-model-editor developer-model-single"><div className="developer-control-section-heading"><div><h2>路由与故障转移</h2><p>主预设优先使用；调用失败时按备用预设顺序切换，所有引用必须来自已存在的模型预设。</p></div></div><label className="developer-control-field">选择路由<select aria-label="模型路由" value={routeName} onChange={(event) => selectRoute(event.target.value)}>{routeNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>{routeName && <><label className="developer-control-field">主路由预设<select aria-label={`${routeName} 主路由预设`} value={routeDraft.primary} onChange={(event) => setRouteDraft((current) => ({ ...current, primary: event.target.value }))}>{presetNames.map((name) => <option key={name} value={name}>{name}</option>)}</select></label><label className="developer-control-field">故障转移预设（逗号分隔）<input aria-label={`${routeName} 故障转移预设`} value={csvValue(routeDraft.fallbacks)} onChange={(event) => setRouteDraft((current) => ({ ...current, fallbacks: commaList(event.target.value) }))} placeholder="例如 worker-safe, worker-backup" /></label><div className="developer-route-chain"><span>Primary</span><strong>{routeDraft.primary || "未选择"}</strong>{routeDraft.fallbacks.map((name) => <Fragment key={name}><ChevronRight size={15} /><span>Fallback</span><strong>{name}</strong></Fragment>)}</div><div className="developer-control-actions"><button className="developer-control-primary" type="button" onClick={() => void saveRoute()}>保存模型路由</button>{message && <small role="status">{message}</small>}</div></>}</section>}
    <Section title="模型档案" hint="模型档案把同一组 Provider 下的 Coordinator、Worker、Utility 预设组合起来，学生端会按会话选择它。"><div className="developer-model-profiles">{Object.entries(asRecord(snapshot.models.profiles)).map(([name, profile]) => <article key={name}><div><strong>{String(asRecord(profile).label ?? name)}</strong><small>{name} · {String(asRecord(profile).provider ?? "-")}</small></div><span>Coordinator: {String(asRecord(profile).coordinator ?? "-")}</span><span>Worker: {String(asRecord(profile).worker ?? "-")}</span></article>)}</div></Section>
  </div>;
}

type McpTransport = "stdio" | "sse" | "streamable_http";
type McpDraft = {
  transport: McpTransport;
  command: string;
  args: string[];
  url: string;
  cwd: string;
  enabled_tools: string[];
  scopes: string[];
  timeout_s: number;
  max_concurrency: number;
  allow_private_network: boolean;
};

const MCP_ADVANCED_KEYS = ["env", "headers", "read_only_tools", "idempotent_tools", "high_risk_tools", "session_exclusive_tools", "global_exclusive_tools"] as const;
const MCP_DEFAULT_ADVANCED = { read_only_tools: [], idempotent_tools: [], high_risk_tools: [], session_exclusive_tools: [], global_exclusive_tools: [] };

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}

function commaList(value: string): string[] {
  return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

function newMcpDraft(value: Record<string, unknown> = {}): McpDraft {
  const transport = value.transport === "sse" || value.transport === "streamable_http" ? value.transport : "stdio";
  return {
    transport,
    command: String(value.command ?? ""),
    args: asStringList(value.args),
    url: String(value.url ?? ""),
    cwd: String(value.cwd ?? ""),
    enabled_tools: asStringList(value.enabled_tools).length ? asStringList(value.enabled_tools) : ["*"],
    scopes: asStringList(value.scopes).length ? asStringList(value.scopes) : ["worker"],
    timeout_s: Number(value.timeout_s ?? 30),
    max_concurrency: Number(value.max_concurrency ?? 1),
    allow_private_network: Boolean(value.allow_private_network),
  };
}

function advancedMcpText(value: Record<string, unknown> = {}): string {
  const advanced = Object.fromEntries(MCP_ADVANCED_KEYS.filter((key) => key in value).map((key) => [key, value[key]]));
  return JSON.stringify(Object.keys(advanced).length ? advanced : MCP_DEFAULT_ADVANCED, null, 2);
}

function buildMcpConfig(draft: McpDraft, advancedText: string): Record<string, unknown> {
  const advanced = asRecord(JSON.parse(advancedText));
  return {
    ...advanced,
    transport: draft.transport,
    command: draft.command.trim(),
    args: draft.args,
    url: draft.url.trim(),
    cwd: draft.cwd.trim(),
    enabled_tools: draft.enabled_tools,
    scopes: draft.scopes,
    timeout_s: Number.isFinite(draft.timeout_s) ? draft.timeout_s : 30,
    max_concurrency: Number.isFinite(draft.max_concurrency) ? draft.max_concurrency : 1,
    allow_private_network: draft.allow_private_network,
  };
}

function skillBody(content: string): string {
  return content.replace(/^---\s*\r?\n[\s\S]*?\r?\n---\s*/, "").trim();
}

function replaceSkillName(content: string, name: string): string {
  return content.replace(/^(---\s*\r?\nname:\s*)[^\r\n]*/i, `$1${name || "example"}`);
}

function profileConsumers(snapshot: DeveloperSnapshot, skillName: string): string[] {
  const profiles = asRecord(snapshot.agents.profiles);
  return Object.entries(profiles).filter(([, value]) => asStringList(asRecord(value).skills).includes(skillName)).map(([name]) => name);
}

export function Mcp({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  const entries = Object.entries(snapshot.tools.mcp_servers);
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<McpDraft>(() => newMcpDraft());
  const [advancedText, setAdvancedText] = useState(() => advancedMcpText());
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const updateDraft = <K extends keyof McpDraft>(key: K, value: McpDraft[K]) => setDraft((current) => ({ ...current, [key]: value }));
  const selectServer = (serverName: string, value: Record<string, unknown>) => {
    setName(serverName);
    setDraft(newMcpDraft(value));
    setAdvancedText(advancedMcpText(value));
    setResult("");
  };
  const reset = () => {
    setName("");
    setDraft(newMcpDraft());
    setAdvancedText(advancedMcpText());
    setResult("");
  };
  const submit = async (action: "test" | "save") => {
    if (!name.trim()) { setResult("请先填写连接名称"); return; }
    setBusy(action);
    try {
      const config = buildMcpConfig(draft, advancedText);
      if (action === "test") {
        const value = await api.testMcp(name.trim(), config);
        setResult(`连接成功：${value.tools.join(", ") || "未发现工具"}`);
      } else {
        await api.saveMcp(name.trim(), config);
        setResult("已保存并热重连");
        await refresh();
      }
    } catch (error) {
      setResult(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };
  return <div className="developer-integrations-page">
    <div className="developer-integrations-heading"><div><span className="developer-eyebrow">MODEL CONTEXT PROTOCOL</span><h1>MCP 连接</h1><p>把外部 MCP Server 接入统一工具目录。先测试发现工具，再保存并热重连到 Gateway。</p></div><button type="button" className="developer-integration-secondary" onClick={reset}><PlugZap size={15} />新建连接</button></div>
    <div className="developer-mcp-layout">
      <section className="developer-integration-form"><div className="developer-integration-section-heading"><div><h2>{name ? `编辑 ${name}` : "新建 MCP 连接"}</h2><p>凭据不会从服务端回显；已有凭据在不填写时会保留。</p></div><span className="developer-integration-kicker">SAFE CONNECT</span></div>
        <div className="developer-mcp-field-grid">
          <label>MCP 名称<input aria-label="MCP 名称" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 knowledge-server" /></label>
          <label>传输方式<select aria-label="传输方式" value={draft.transport} onChange={(event) => updateDraft("transport", event.target.value as McpTransport)}><option value="stdio">stdio · 本地进程</option><option value="streamable_http">Streamable HTTP · 远程</option><option value="sse">SSE · 远程</option></select></label>
          {draft.transport === "stdio" ? <label>stdio 命令<input aria-label="stdio 命令" value={draft.command} onChange={(event) => updateDraft("command", event.target.value)} placeholder="例如 python" /></label> : <label>服务 URL<input aria-label="服务 URL" value={draft.url} onChange={(event) => updateDraft("url", event.target.value)} placeholder="https://example.com/mcp" /></label>}
          <label>{draft.transport === "stdio" ? "启动参数" : "工作目录"}<input aria-label={draft.transport === "stdio" ? "启动参数" : "工作目录"} value={draft.transport === "stdio" ? draft.args.join(" ") : draft.cwd} onChange={(event) => updateDraft(draft.transport === "stdio" ? "args" : "cwd", draft.transport === "stdio" ? event.target.value.trim().split(/\s+/).filter(Boolean) : event.target.value)} placeholder={draft.transport === "stdio" ? "server.py --stdio" : "可选"} /></label>
          <label>可用工具<input aria-label="可用工具" value={draft.enabled_tools.join(", ")} onChange={(event) => updateDraft("enabled_tools", commaList(event.target.value))} placeholder="* 或 search, fetch" /></label>
          <label>超时（秒）<input aria-label="超时（秒）" type="number" min="1" max="1800" value={draft.timeout_s} onChange={(event) => updateDraft("timeout_s", Number(event.target.value))} /></label>
          <label>并发上限<input aria-label="并发上限" type="number" min="1" max="100" value={draft.max_concurrency} onChange={(event) => updateDraft("max_concurrency", Number(event.target.value))} /></label>
        </div>
        <div className="developer-mcp-options"><span>允许作用域</span><label><input type="checkbox" checked={draft.scopes.includes("worker")} onChange={(event) => updateDraft("scopes", event.target.checked ? [...new Set([...draft.scopes, "worker"])] : draft.scopes.filter((scope) => scope !== "worker"))} />Worker</label><label><input type="checkbox" checked={draft.scopes.includes("coordinator")} onChange={(event) => updateDraft("scopes", event.target.checked ? [...new Set([...draft.scopes, "coordinator"])] : draft.scopes.filter((scope) => scope !== "coordinator"))} />Coordinator</label><label><input type="checkbox" checked={draft.allow_private_network} onChange={(event) => updateDraft("allow_private_network", event.target.checked)} />允许私有网络（仅可信环境）</label></div>
        <details className="developer-integration-advanced"><summary>凭据与安全标签 <ChevronDown size={15} /></summary><p>可在这里填写 env、headers 以及工具安全分类；服务端不会把它们回显到快照。</p><textarea aria-label="高级 MCP 配置 JSON" value={advancedText} onChange={(event) => setAdvancedText(event.target.value)} spellCheck={false} /></details>
        <div className="developer-integration-actions"><button type="button" onClick={() => void submit("test")} disabled={busy !== null}><Activity size={15} />{busy === "test" ? "测试中…" : "测试连接"}</button><button className="primary" type="button" onClick={() => void submit("save")} disabled={busy !== null}><PlugZap size={15} />{busy === "save" ? "保存中…" : "保存连接"}</button>{result && <small className={result.startsWith("连接成功") || result.startsWith("已保存") ? "success" : "error"} role="status">{result}</small>}</div>
      </section>
      <aside className="developer-integration-list"><div className="developer-integration-section-heading"><div><h2>已配置连接</h2><p>{entries.length ? `${entries.length} 个 MCP Server` : "还没有连接"}</p></div></div>{entries.length ? entries.map(([serverName, serverConfig]) => { const tools = snapshot.tools.items.filter((item) => String(item.source) === "mcp" && String(item.provider) === serverName).map((item) => String(item.name)); const uniqueTools = [...new Set(tools)]; return <article className="developer-mcp-card" key={serverName}><div className="developer-mcp-card-top"><div className="developer-mcp-card-title"><PlugZap size={17} /><span><strong>{serverName}</strong><small>{String(serverConfig.transport ?? "自动识别")} · {serverConfig.credentials_configured ? "凭据已配置" : "无额外凭据"}</small></span></div><StatusPill ok={uniqueTools.length > 0}>{uniqueTools.length > 0 ? "已连接" : "已配置"}</StatusPill></div><p>{uniqueTools.length ? `已发现 ${uniqueTools.length} 个工具` : "保存后会尝试连接并发现工具"}</p>{uniqueTools.length > 0 && <div className="developer-mcp-tools">{uniqueTools.slice(0, 6).map((tool) => <code key={tool}>{tool}</code>)}{uniqueTools.length > 6 && <small>+{uniqueTools.length - 6} 个</small>}</div>}<div className="developer-mcp-card-actions"><button type="button" onClick={() => selectServer(serverName, serverConfig)}>编辑 {serverName}</button><button className="danger" type="button" onClick={() => { if (confirm(`删除 MCP ${serverName}？`)) void api.deleteMcp(serverName).then(refresh); }}>删除</button></div></article>; }) : <div className="developer-integration-empty"><PlugZap size={21} /><strong>从一个 MCP Server 开始</strong><p>连接成功后，发现的工具会进入统一工具目录，并按作用域交给 Agent 使用。</p></div>}</aside>
    </div>
  </div>;
}

export function Skills({ snapshot, refresh }: { snapshot: DeveloperSnapshot; refresh: () => Promise<void> }) {
  const [selectedName, setSelectedName] = useState("");
  const [name, setName] = useState("");
  const [content, setContent] = useState("---\nname: example\ndescription: 用途说明\nallowed_tools: []\ncapabilities: []\n---\n\n写入该 Skill 的操作流程。");
  const [isNew, setIsNew] = useState(true);
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const selectedSkill = snapshot.skills.find((skill) => skill.name === selectedName);
  const consumers = selectedName ? profileConsumers(snapshot, selectedName) : [];
  const selectSkill = async (skill: DeveloperSnapshot["skills"][number]) => {
    setSelectedName(skill.name);
    setName(skill.name);
    setIsNew(false);
    setMode("edit");
    setMessage("读取 Skill…");
    try {
      const value = await api.getSkill(skill.name);
      setName(value.name);
      setContent(value.content);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  };
  const createSkill = () => {
    setSelectedName("");
    setName("");
    setContent("---\nname: example\ndescription: 用途说明\nallowed_tools: []\ncapabilities: []\n---\n\n写入该 Skill 的操作流程。");
    setIsNew(true);
    setMode("edit");
    setMessage("");
  };
  const save = async () => {
    if (!name.trim()) { setMessage("请先填写 Skill 名称"); return; }
    setBusy(true);
    try {
      await api.saveSkill(name.trim(), replaceSkillName(content, name.trim()));
      setSelectedName(name.trim());
      setIsNew(false);
      setMessage("已保存并重载，新的 Worker 会话即可使用");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  return <div className="developer-integrations-page">
    <div className="developer-integrations-heading"><div><span className="developer-eyebrow">WORKER BEHAVIOR</span><h1>Skills</h1><p>用 Markdown 定义 Agent 的工作流程、工具边界和能力要求；保存后会立即重新加载 Skill。</p></div><button type="button" className="developer-integration-secondary" onClick={createSkill}><FileKey2 size={15} />新建 Skill</button></div>
    <div className="developer-skills-layout">
      <aside className="developer-skill-directory"><div className="developer-integration-section-heading"><div><h2>Skill 目录</h2><p>{snapshot.skills.length ? `${snapshot.skills.length} 个已发现` : "还没有 Skill"}</p></div></div>{snapshot.skills.length ? snapshot.skills.map((skill) => <button type="button" className={`developer-skill-item ${skill.name === selectedName ? "active" : ""}`} aria-pressed={skill.name === selectedName} key={skill.path} onClick={() => void selectSkill(skill)}><span className="developer-skill-item-icon"><FileKey2 size={16} /></span><span><strong>{skill.name}</strong><small>{skill.description}</small><em>{skill.source === "workspace" ? "工作区" : "项目内"}</em>{skill.missing_requirements.length > 0 && <em className="developer-skill-item-warning">缺少依赖：{skill.missing_requirements.join("、")}</em>}</span><StatusPill ok={skill.available}>{skill.available ? "可用" : "缺少依赖"}</StatusPill></button>) : <div className="developer-integration-empty"><FileKey2 size={21} /><strong>还没有 Skill</strong><p>新建一个 Skill，把可复用的工作方法交给 Worker。</p></div>}</aside>
      <section className="developer-skill-editor"><div className="developer-integration-section-heading"><div><h2>{isNew ? "新建 Skill" : name || "Skill"}</h2><p>{selectedSkill?.source === "project" ? "项目内 Skill 只读；保存会创建同名的工作区覆盖。" : "工作区 Skill 可编辑、可删除，并会参与 Worker Profile 解析。"}</p></div>{selectedSkill && <StatusPill ok={selectedSkill.available}>{selectedSkill.available ? "可用" : "缺少依赖"}</StatusPill>}</div>{selectedSkill && <div className="developer-skill-meta"><span><strong>来源</strong>{selectedSkill.source === "workspace" ? "工作区" : "项目内"}</span><span><strong>文件</strong>{selectedSkill.path}</span><span><strong>使用</strong>{consumers.length ? consumers.join("、") : "尚未绑定 Worker Profile"}</span></div>}{selectedSkill && !selectedSkill.available && <div className="developer-skill-warning"><ShieldCheck size={16} /><span>缺少依赖：{selectedSkill.missing_requirements.join("、") || "请检查 Skill 配置"}。当前不会被标记为可用。</span></div>}<label className="developer-skill-name-field">Skill 名称<input aria-label="Skill 名称" value={name} onChange={(event) => { const next = event.target.value; setName(next); if (isNew) setContent((current) => replaceSkillName(current, next)); }} placeholder="例如 research" /></label><div className="developer-skill-tabs"><button type="button" aria-pressed={mode === "edit"} onClick={() => setMode("edit")}>编辑</button><button type="button" aria-pressed={mode === "preview"} onClick={() => setMode("preview")}>预览</button></div>{mode === "edit" ? <textarea className="developer-skill-textarea" aria-label="Skill Markdown" value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} /> : <div className="developer-skill-preview"><MarkdownContent>{skillBody(content) || "暂无 SOP 内容"}</MarkdownContent></div>}<div className="developer-integration-actions"><button type="button" onClick={() => void save()} disabled={busy}>{busy ? "保存中…" : selectedSkill?.source === "project" ? "创建工作区覆盖" : isNew ? "保存 Skill" : "保存修改"}</button>{!isNew && selectedSkill?.source === "workspace" && <button className="danger" type="button" onClick={() => { if (confirm(`删除 Skill ${name}？`)) void api.deleteSkill(name).then(async () => { createSkill(); await refresh(); }); }}>删除 Skill</button>}{message && <small className={message.startsWith("已保存") ? "success" : message === "读取 Skill…" ? "pending" : "error"} role="status">{message}</small>}</div></section>
    </div>
  </div>;
}

export function Automations({ snapshot }: { snapshot: DeveloperSnapshot }) {
  const apps = snapshot.features.apps ?? { available: false, reason: "" };
  const automations = snapshot.features.automations ?? { available: false, reason: "" };
  return <div className="developer-integrations-page"><div className="developer-integrations-heading"><div><span className="developer-eyebrow">EXTENSIONS ROADMAP</span><h1>Apps 与自动化</h1><p>这里负责外部应用连接和定时任务。目前版本尚未接入运行时，先把边界说清楚，避免把空页面误认为可配置功能。</p></div></div><div className="developer-apps-grid"><article className="developer-app-roadmap-card"><div className="developer-app-roadmap-icon"><Box size={20} /></div><div className="developer-app-roadmap-top"><div><span>EXTERNAL APPS</span><h2>Apps 注册表</h2></div><StatusPill ok={apps.available}>{apps.available ? "已启用" : "规划中"}</StatusPill></div><p>未来用于管理 OAuth、外部服务授权和应用级连接。当前版本尚未提供 App Registry 或对应的连接接口。</p><small>需要后端注册表、授权生命周期和按工作区隔离后，才会开放配置。</small></article><article className="developer-app-roadmap-card"><div className="developer-app-roadmap-icon"><Clock3 size={20} /></div><div className="developer-app-roadmap-top"><div><span>SCHEDULED WORK</span><h2>自动化任务</h2></div><StatusPill ok={automations.available}>{automations.available ? "已启用" : "规划中"}</StatusPill></div><p>未来用于创建、暂停和追踪 Cron/定时工作流。当前没有 Cron Runtime，不会伪造任务入口。</p><small>接入后会在这里展示任务状态、最近运行和失败原因。</small></article></div><section className="developer-apps-now"><Sparkles size={18} /><div><strong>现在可用的扩展方式</strong><p>MCP 负责连接外部工具，Skills 负责定义 Worker 行为。它们已经有独立的管理台；Apps 和自动化将在具备运行时后再接入。</p></div></section></div>;
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

function runtimeNumber(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function storageState(root: DeveloperSnapshot["workspace"]["roots"][number]): { label: string; tone: "ready" | "pending" | "blocked"; description: string } {
  if (!root.exists) return { label: "按需创建", tone: "pending", description: "尚未产生对应数据，首次使用时会创建。" };
  if (!root.writable) return { label: "不可写", tone: "blocked", description: "目录已存在，但当前进程没有写入权限。" };
  return { label: "可写", tone: "ready", description: "目录已创建，当前进程可以写入。" };
}

export function RuntimeSettings({ snapshot }: { snapshot: DeveloperSnapshot }) {
  const [runtime, setRuntime] = useState<DeveloperSnapshot["runtime"] | DeveloperRuntimeHealth>(snapshot.runtime);
  const [checkedAt, setCheckedAt] = useState(() => Date.now());
  const [checking, setChecking] = useState(false);
  const [healthError, setHealthError] = useState("");

  const checkHealth = useCallback(async () => {
    setChecking(true);
    try {
      setRuntime(await api.getDeveloperHealth());
      setCheckedAt(Date.now());
      setHealthError("");
    } catch (reason) {
      setHealthError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setRuntime(snapshot.runtime);
      setCheckedAt(Date.now());
      setHealthError("");
    });
    return () => { active = false; };
  }, [snapshot.runtime]);
  useEffect(() => {
    const timer = window.setInterval(() => void checkHealth(), 15_000);
    return () => window.clearInterval(timer);
  }, [checkHealth]);

  const web = snapshot.web;
  const protocol = (web.protocol ?? {}) as Record<string, unknown>;
  const host = String(web.host ?? "unknown");
  const port = web.port == null ? "" : `:${String(web.port)}`;
  const endpoint = `${host}${port}`;
  const runtimeStatus = String(runtime.status ?? "unknown");
  const healthy = runtimeStatus === "ok" && runtime.started !== false;
  const accepting = runtime.accepting_turns === true;
  const statusLabel = healthy ? "Runtime 正常" : runtimeStatus === "stopped" ? "Runtime 已停止" : "Runtime 需检查";
  const checkedLabel = new Date(checkedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  return <div className="developer-runtime-page">
    <div className="developer-runtime-heading"><div><span className="developer-eyebrow">RUNTIME DIAGNOSTICS</span><h1>运行诊断</h1><p>实时确认 Gateway 是否在工作、能否接收请求，以及数据目录是否具备写入条件。</p></div><button className="developer-runtime-refresh" type="button" onClick={() => void checkHealth()} disabled={checking}><RefreshCw size={15} className={checking ? "spin" : ""} />{checking ? "检查中…" : "重新检查"}</button></div>
    {healthError && <div className="developer-runtime-error" role="alert"><ShieldCheck size={17} /><span>实时检查失败：{healthError}。仍显示上一次结果。</span></div>}
    <section className={`developer-runtime-health ${healthy ? "ready" : "blocked"}`}>
      <div className="developer-runtime-health-primary"><span className="developer-runtime-health-dot" /><div><strong>{statusLabel}</strong><small>{accepting ? "正在接收请求" : "暂停接收请求"} · 最近检查 {checkedLabel}</small></div></div>
      <div className="developer-runtime-health-actions"><span>{runtime.started === false ? "进程未启动" : accepting ? "Gateway 可用" : "Gateway 未接受新请求"}</span></div>
    </section>
    <div className="developer-runtime-metrics">
      <article><span>接收状态</span><strong>{accepting ? "运行中" : "已暂停"}</strong><small>{accepting ? "允许新的 Turn 进入" : "不会接收新的 Turn"}</small></article>
      <article><span>当前活跃 Turn</span><strong>{runtimeNumber(runtime.active_turns).toLocaleString()}</strong><small>正在处理的请求上下文</small></article>
      <article><span>持久事件</span><strong>{runtimeNumber(runtime.durable_events).toLocaleString()}</strong><small>已写入 Gateway 存储</small></article>
      <article><span>订阅连接</span><strong>{runtimeNumber(runtime.subscribers).toLocaleString()}</strong><small>当前事件订阅者</small></article>
    </div>
    <Section title="服务入口" hint="这些是当前控制面实例实际使用的入口，不是可编辑配置。">
      <div className="developer-runtime-endpoints">
        <article><Globe2 size={17} /><div><span>监听地址</span><strong>{endpoint}</strong><small>控制面 HTTP 服务</small></div></article>
        <article><Activity size={17} /><div><span>HTTP API</span><strong>{String(protocol.http ?? "/api/v1")}</strong><small>浏览器与服务端请求</small></div></article>
        <article><Activity size={17} /><div><span>WebSocket</span><strong>{String(protocol.websocket ?? "/ws/v1")}</strong><small>实时流式事件</small></div></article>
        <article><Database size={17} /><div><span>Gateway 数据库</span><strong>{String(runtime.database ?? "未上报")}</strong><small>会话与运行事件的持久化位置</small></div></article>
      </div>
    </Section>
    <Section title="数据目录" hint="目录不存在不代表故障：sessions、memory 等目录会按需创建；只有已存在但不可写才需要处理。">
      <div className="developer-runtime-storage-list">{snapshot.workspace.roots.map((root) => { const state = storageState(root); return <article key={root.name}><div className="developer-runtime-storage-copy"><Database size={17} /><span><strong>{root.name}</strong><small>{root.path}</small></span></div><div className={`developer-runtime-storage-state ${state.tone}`}><strong>{state.label}</strong><small>{state.description}</small></div></article>; })}</div>
    </Section>
    <Section title="配置边界" hint="运行诊断只读，不会把敏感配置暴露给浏览器。">
      <div className="developer-runtime-callout"><ShieldCheck size={18} /><div><strong>安全配置仍由服务端管理</strong><p>Provider 密钥、MCP headers/env、Cookie secret 和 Authorization 字段不会通过开发者 API 返回。配置写入继续由本地 YAML/.env 管理；模型、工具、MCP 和 Skill 的可修改项在各自页面处理。</p></div></div>
    </Section>
    <details className="developer-runtime-raw"><summary>查看原始快照 <ChevronDown size={15} aria-hidden="true" /></summary><JsonBlock value={{ runtime, web: snapshot.web, workspace: snapshot.workspace }} /></details>
  </div>;
}

export function ReleaseNotes() {
  const [items, setItems] = useState<ReleaseNoteEntry[] | null>(null);
  const [error, setError] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [version, setVersion] = useState("");
  const [releasedAt, setReleasedAt] = useState(todayInputValue);
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState<"draft" | "published">("published");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setItems(null); setError("");
    try { setItems((await api.listReleaseNotes()).items); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, []);
  useEffect(() => { queueMicrotask(() => void load()); }, [load]);

  const resetForm = () => { setEditingId(null); setVersion(""); setReleasedAt(todayInputValue()); setNotes(""); setStatus("published"); setMessage(""); };
  const startEdit = (item: ReleaseNoteEntry) => { setEditingId(item.id); setVersion(item.version); setReleasedAt(item.released_at.slice(0, 10) || todayInputValue()); setNotes(item.notes.join("\n")); setStatus(item.status); setMessage(""); };
  const save = async () => {
    const note: Omit<ReleaseNoteEntry, "id"> = {
      version: version.trim(),
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

  return <><Section title="发布说明" hint="每个版本一条记录；学生端「版本与更新」只展示已发布的条目。保存后会通过公开只读接口同步到主页面。">
    {error && <div className="developer-error"><ShieldCheck /><strong>无法读取发布说明</strong><p>{error}</p></div>}
    <div className="developer-release-editor">
      <div className="developer-release-editor-heading"><div><span className="developer-eyebrow">RELEASE PIPELINE</span><h3>{editingId ? "编辑发布说明" : "准备下一次发布"}</h3></div><StatusPill ok={status === "published"}>{status === "published" ? "学生端可见" : "仅内部草稿"}</StatusPill></div>
      <div className="developer-release-fields"><label><span>版本号</span><input value={version} onChange={(event) => setVersion(event.target.value)} placeholder="版本，例如 1.0.0" disabled={Boolean(editingId)} /></label><label><span>发布日期</span><input type="date" value={releasedAt} onChange={(event) => setReleasedAt(event.target.value)} aria-label="发布日期" /></label><label><span>发布状态</span><select value={status} onChange={(event) => setStatus(event.target.value as "draft" | "published")} aria-label="发布状态"><option value="draft">草稿</option><option value="published">已发布</option></select></label></div>
      <label className="developer-release-notes-field"><span>更新与修复说明 <small>每行一条，学生端按原顺序展示</small></span><textarea value={notes} onChange={(event) => setNotes(event.target.value)} spellCheck={false} placeholder="每行一条更新与修复说明" /></label>
      <div className="developer-release-actions"><button type="button" onClick={() => void save()} disabled={!version.trim() || !releasedAt || !notes.trim()}>{editingId ? "保存修改" : "新建发布说明"}</button>{editingId && <button className="secondary" type="button" onClick={resetForm}>取消编辑</button>}{message && <small className="developer-form-message">{message}</small>}</div>
    </div>
    <div className="developer-release-list">{(items ?? []).map((item, index) => <details className="developer-release-card" key={item.id} open={index === 0}>
      <summary className="developer-release-summary">
        <span className="developer-release-summary-main"><span className="developer-release-version"><Newspaper size={17} /><strong>v{item.version}</strong><small>发布日期 · {item.released_at.slice(0, 10)}</small></span><small>{item.status === "published" ? "这条说明会出现在学生端设置中的“版本与更新”" : "草稿仅开发者可见，发布后才会同步"}</small></span>
        <span className="developer-release-summary-meta"><StatusPill ok={item.status === "published"}>{item.status === "published" ? "已发布" : "草稿"}</StatusPill><ChevronDown className="developer-release-chevron" size={17} aria-hidden="true" /></span>
      </summary>
      <div className="developer-release-card-body"><ul>{item.notes.map((note) => <li key={note}>{note}</li>)}</ul><div className="developer-release-card-actions"><button type="button" onClick={() => startEdit(item)}>编辑</button><button className="danger" type="button" onClick={() => void remove(item)}>删除</button></div></div>
    </details>)}</div>
    {items && items.length === 0 && <div className="developer-empty"><Newspaper /><strong>暂无发布说明</strong><p>新建一条记录，学生端即可在「版本与更新」中看到。</p></div>}
  </Section></>;
}

function todayInputValue() {
  const today = new Date();
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
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
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [managementRefreshToken, setManagementRefreshToken] = useState(0);
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
        setSnapshot(null);
        setSnapshotError("");
        try {
          setSnapshot(await api.getDeveloperSnapshot());
          setSnapshotError("");
        }
        catch (reason) {
          setSnapshot(null);
          setSnapshotError(reason instanceof Error ? reason.message : String(reason));
        }
      } else {
        setSnapshot(null);
        setSnapshotError("");
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
    if (page === "menus") return <MenuManagementPageV2 />;
    if (page === "quotas") return <QuotaManagementPage />;
    if (!snapshot) return <div className="developer-error"><ShieldCheck /><strong>无法读取运行时快照</strong><p>{snapshotError || "当前身份可能缺少运行时检查权限；其余页面不受影响。"}</p></div>;
    if (page === "agents") return <Agents snapshot={snapshot} refresh={load} />;
    if (page === "tools") return <Tools snapshot={snapshot} refresh={load} />;
    if (page === "models") return <Models snapshot={snapshot} refresh={load} />;
    if (page === "mcp") return <Mcp snapshot={snapshot} refresh={load} />;
    if (page === "skills") return <Skills snapshot={snapshot} refresh={load} />;
    if (page === "automations") return <Automations snapshot={snapshot} />;
    if (page === "settings") return <RuntimeSettings snapshot={snapshot} />;
    return <Overview snapshot={snapshot} />;
  }, [changeFeedbackCategory, changeFeedbackPriority, changeFeedbackSearch, changeFeedbackSort, changeFeedbackStatus, deleteFeedback, deleteFeedbackThreads, feedbackCategory, feedbackLoadError, feedbackLoading, feedbackOffset, feedbackPriority, feedbackSearch, feedbackSelectedId, feedbackSort, feedbackStatus, feedbackThreads, feedbackTotal, load, managementRefreshToken, markFeedbackRead, markFeedbackThreadsRead, page, refreshFeedback, snapshot, snapshotError]);
  const accessDenied = !loading && visiblePages.size > 0 && !visiblePages.has(page);
  return <div className="developer-shell"><aside className="developer-nav"><div className="developer-brand"><TerminalSquare /><span><strong>NLP Developer</strong><small>CONTROL PLANE · 8765</small></span></div><nav>{NAV_GROUPS.map((group) => { const items = NAV.filter((item) => item.group === group.key && visiblePages.has(item.page)); return items.length ? <div className="developer-nav-group" key={group.key}><span className="developer-nav-group-label">{group.label}</span>{items.map(({ page: itemPage, label, icon: Icon }) => <button className={page === itemPage ? "active" : ""} type="button" key={itemPage} onClick={() => navigate(itemPage)}><Icon size={17} />{label}</button>)}</div> : null; })}</nav><a href="/"><ChevronLeft size={16} />返回学生模式</a></aside><main className="developer-main"><header className="developer-topbar"><div><Globe2 size={16} /><span>开发者控制面</span><small>仅对当前身份生效</small></div><button type="button" onClick={() => { if (page === "feedback") void refreshFeedback(); void refreshWorkspace(); }} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={16} />刷新数据</button></header><div className="developer-content">{loading && visiblePages.size === 0 ? <div className="developer-loading"><RefreshCw className="spin" />正在读取运行时…</div> : error ? <div className="developer-error"><ShieldCheck /><strong>无法进入开发者模式</strong><p>{error}</p></div> : accessDenied ? <div className="developer-error"><ShieldCheck /><strong>无权访问该页面</strong><p>当前身份未被授予此菜单；请从左侧导航选择可用的页面。</p></div> : content}</div></main></div>;
}
