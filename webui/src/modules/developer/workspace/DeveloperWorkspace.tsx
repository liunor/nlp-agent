import {
  Activity, AppWindow, Bot, Box, ChevronLeft, Clock3, Code2, Database,
  ExternalLink, FileKey2, Gauge, Globe2, KeyRound, Newspaper, PlugZap,
  RefreshCw, Settings2, ShieldCheck, Sparkles, TerminalSquare, Wrench,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ensureAuth } from "@/platform/http/api";
import type { DeveloperSnapshot, ReleaseNoteEntry } from "@/shared/types";

export type DeveloperPage = "overview" | "agents" | "tools" | "models" | "mcp" | "skills" | "release-notes" | "automations" | "settings";

const NAV: Array<{ page: DeveloperPage; label: string; icon: typeof Gauge }> = [
  { page: "overview", label: "工作台", icon: Gauge },
  { page: "agents", label: "Agent 与 Worker", icon: Bot },
  { page: "tools", label: "工具", icon: Wrench },
  { page: "models", label: "模型与 Provider", icon: Sparkles },
  { page: "mcp", label: "MCP", icon: PlugZap },
  { page: "skills", label: "Skills", icon: Code2 },
  { page: "release-notes", label: "发布说明", icon: Newspaper },
  { page: "automations", label: "Apps 与自动化", icon: Clock3 },
  { page: "settings", label: "运行时设置", icon: Settings2 },
];

function currentPage(): DeveloperPage {
  const value = location.pathname.split("/")[2] as DeveloperPage | undefined;
  return NAV.some((item) => item.page === value) ? value! : "overview";
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="developer-json">{JSON.stringify(value, null, 2)}</pre>;
}

function StatusPill({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <span className={`developer-status ${ok ? "ok" : "idle"}`}>{children}</span>;
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return <section className="developer-section"><header><div><h2>{title}</h2>{hint && <p>{hint}</p>}</div></header>{children}</section>;
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
  return <div className="developer-page-grid">
    <section className="developer-hero"><div><span>DEVELOPER CONTROL PLANE</span><h1>后端基础工作台</h1><p>查看 Agent、工具、模型和本地数据边界。学生界面不会显示这些内部信息。</p></div><ShieldCheck size={54} /></section>
    <div className="developer-kpis">
      <article><Activity /><span>Gateway</span><strong>{String(runtime.status ?? "unknown")}</strong></article>
      <article><Bot /><span>活跃 Turn</span><strong>{String(runtime.active_turns ?? 0)}</strong></article>
      <article><Database /><span>持久事件</span><strong>{Number(runtime.durable_events ?? 0).toLocaleString()}</strong></article>
      <article><PlugZap /><span>工具目录版本</span><strong>{snapshot.tools.catalog_revision}</strong></article>
    </div>
    <Section title="能力状态" hint="未配置的通用工作台能力会明确显示，不伪造可用状态。"><div className="developer-card-grid">{Object.entries(snapshot.features).map(([name, feature]) => <article className="developer-card" key={name}><div><AppWindow size={18} /><strong>{name}</strong></div><StatusPill ok={feature.available}>{feature.available ? "已启用" : "未启用"}</StatusPill><p>{feature.reason}</p></article>)}</div></Section>
    <Section title="独立观测平台" hint="Trace、Token、错误和实时事件在隔离端口展示。"><a className="developer-monitor-link" href={`${location.protocol}//${location.hostname}:8766`} target="_blank" rel="noreferrer"><Gauge size={20} /><span><strong>打开 Observability Monitor</strong><small>127.0.0.1:8766</small></span><ExternalLink size={16} /></a></Section>
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
  const [page, setPage] = useState<DeveloperPage>(routedPage ?? currentPage);
  const [snapshot, setSnapshot] = useState<DeveloperSnapshot | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { const auth = await ensureAuth(); if (!auth.roles.includes("admin")) throw new Error("当前账户没有开发者权限"); setSnapshot(await api.getDeveloperSnapshot()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  const navigate = (next: DeveloperPage) => { if (onNavigate) onNavigate(next); else { history.pushState({}, "", next === "overview" ? "/developer" : `/developer/${next}`); setPage(next); } };
  const content = useMemo(() => {
    if (!snapshot) return null;
    if (page === "agents") return <Agents snapshot={snapshot} refresh={load} />;
    if (page === "tools") return <Tools snapshot={snapshot} refresh={load} />;
    if (page === "models") return <Models snapshot={snapshot} />;
    if (page === "mcp") return <Mcp snapshot={snapshot} refresh={load} />;
    if (page === "skills") return <Skills snapshot={snapshot} refresh={load} />;
    if (page === "release-notes") return <ReleaseNotes />;
    if (page === "automations") return <Automations snapshot={snapshot} />;
    if (page === "settings") return <RuntimeSettings snapshot={snapshot} />;
    return <Overview snapshot={snapshot} />;
  }, [page, snapshot, load]);
  return <div className="developer-shell"><aside className="developer-nav"><div className="developer-brand"><TerminalSquare /><span><strong>NLP Developer</strong><small>Control plane · 8765</small></span></div><nav>{NAV.map(({ page: itemPage, label, icon: Icon }) => <button className={page === itemPage ? "active" : ""} type="button" key={itemPage} onClick={() => navigate(itemPage)}><Icon size={17} />{label}</button>)}</nav><a href="/"><ChevronLeft size={16} />返回学生模式</a></aside><main className="developer-main"><header className="developer-topbar"><div><Globe2 size={16} /><span>本地管理员</span></div><button type="button" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={16} />刷新</button></header><div className="developer-content">{loading && !snapshot ? <div className="developer-loading"><RefreshCw className="spin" />正在读取运行时…</div> : error ? <div className="developer-error"><ShieldCheck /><strong>无法进入开发者模式</strong><p>{error}</p></div> : content}</div></main></div>;
}
