import { AlertCircle, AlertTriangle, BarChart3, BookOpen, CheckCircle2, ChevronLeft, FileQuestion, GraduationCap, LayoutDashboard, MessageCircleQuestion, RefreshCw, Save, Sparkles, Target, TrendingUp, Users } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { BlueprintCatalogEditor, GuidedBlueprintCatalogEditor, TopicCatalogEditor } from "@/modules/teacher/workspace/TeacherCatalogManager";
import { TeacherBookEditor } from "@/modules/teacher/workspace/TeacherBookEditor";
import { SchoolLogo } from "@/shared/ui/SchoolLogo";
import { api, ensureAuth } from "@/platform/http/api";
import type { TeacherCatalog, TeacherOverview } from "@/shared/types";
import { resolveWorkspaceId } from "@/shared/utils/workspace";

export type TeacherPage = "overview" | "topics" | "book" | "exercises" | "reviews" | "guided" | "questions" | "reports";
const NAV: Array<{ page: TeacherPage; label: string; icon: typeof LayoutDashboard }> = [
  { page: "overview", label: "教师首页", icon: LayoutDashboard }, { page: "book", label: "教材内容", icon: BookOpen }, { page: "topics", label: "主题与知识点", icon: BookOpen },
  { page: "exercises", label: "出题蓝图", icon: Sparkles }, { page: "reviews", label: "复习蓝图", icon: Target }, { page: "guided", label: "引导模式", icon: MessageCircleQuestion },
  { page: "questions", label: "学生问题", icon: FileQuestion }, { page: "reports", label: "学习分析", icon: BarChart3 },
];
const PAGE_LABELS: Record<TeacherPage, string> = {
  overview: "教师首页", topics: "主题与知识点", book: "教材内容", exercises: "出题蓝图",
  reviews: "复习蓝图", guided: "引导模式", questions: "学生问题", reports: "学习分析",
};
const pageFromPath = (): TeacherPage => {
  const candidate = location.pathname.split("/")[2] as TeacherPage;
  return candidate in PAGE_LABELS ? candidate : "overview";
};

function SaveActions({ saving, message, onSave }: { saving: boolean; message: string; onSave: () => void }) { return <div className="teacher-form-actions"><button type="button" disabled={saving} onClick={onSave}><Save size={16} />{saving ? "正在保存…" : "保存教学目录"}</button>{message && <span role="status">{message}</span>}</div>; }

function Overview({ data, catalog }: { data: TeacherOverview; catalog: TeacherCatalog }) { return <div className="teacher-stack"><section className="teacher-welcome"><div><span>TEACHER MODE</span><h1>NLP 教师空间</h1><p>在一个教学目录里维护主题、知识点与蓝图。学生端的可选主题和后端动态提示词都会使用这一份数据。</p></div><GraduationCap size={64} /></section><div className="teacher-kpis"><article><BookOpen /><span>教学主题</span><strong>{catalog.topics.length}</strong></article><article><Sparkles /><span>出题蓝图</span><strong>{catalog.exercise_blueprints.length}</strong></article><article><Target /><span>复习蓝图</span><strong>{catalog.review_blueprints.length}</strong></article><article><FileQuestion /><span>学生问题</span><strong>{data.summary.questions}</strong></article></div></div>; }

const riskText = { low: "低风险", medium: "需关注", high: "高风险" } as const;

function QuestionsPage({ data }: { data: TeacherOverview }) {
  return <div className="teacher-stack">
    <div className="teacher-page-summary"><div><span className="teacher-eyebrow">QUESTION INSIGHT</span><h2>从提问统计发现教学线索</h2><p>查看近 {data.period_days} 天学生提问的概览与分布，快速定位主题、难度和模式热度。</p></div><MessageCircleQuestion size={46} /></div>
    <div className="teacher-insight-kpis"><article><MessageCircleQuestion /><span>问题总数</span><strong>{data.summary.questions}</strong><small>近 {data.period_days} 天</small></article><article><Users /><span>活跃学生</span><strong>{data.summary.students}</strong><small>{data.summary.sessions} 个会话</small></article><article><BookOpen /><span>涉及主题</span><strong>{data.topic_distribution.length}</strong><small>按学习上下文归类</small></article><article className={data.summary.error_questions ? "warning" : ""}><AlertCircle /><span>异常问题</span><strong>{data.summary.error_questions}</strong><small>执行失败或中断</small></article><article><CheckCircle2 /><span>练习完成</span><strong>{data.summary.exercises}</strong><small>已评分题量</small></article><article><TrendingUp /><span>练习通过率</span><strong>{data.summary.exercises ? `${data.summary.exercise_pass_rate}%` : "—"}</strong><small>得分 ≥ 60</small></article></div>
    <div className="teacher-report-grid"><Distribution title="主题分布" items={data.topic_distribution} /><Distribution title="难度分布" items={data.difficulty_distribution} tone="blue" /><Distribution title="模式分布" items={data.mode_distribution} tone="green" /></div>
    <DailyTrend items={data.daily_questions} />
  </div>;
}

function Distribution({ title, items, tone = "purple" }: { title: string; items: TeacherOverview["topic_distribution"]; tone?: "purple" | "blue" | "green" }) { return <section className="teacher-panel"><header><div><h2>{title}</h2><p>按问题数量汇总</p></div></header>{items.length ? <div className="teacher-distribution">{items.map((item) => <article key={item.name}><div><strong>{item.name}</strong><span>{item.count} 次 · {item.percentage}%</span></div><i><b className={tone} style={{ width: `${item.percentage}%` }} /></i></article>)}</div> : <p className="teacher-empty-inline">暂无分布数据</p>}</section>; }

function DailyTrend({ items }: { items: Array<{ date: string; count: number }> }) {
  const max = Math.max(1, ...items.map((item) => item.count));
  return <section className="teacher-panel"><header><div><h2>每日问题量</h2><p>近 {items.length} 天提问趋势</p></div></header>{items.length ? <div className="teacher-daily-trend">{items.map((item) => <div className="teacher-daily-bar" key={item.date} title={`${item.date}：${item.count} 次`}><small>{item.count}</small><span style={{ height: `${Math.max(4, Math.round(item.count / max * 100))}%` }} /></div>)}</div> : <p className="teacher-empty-inline">暂无趋势数据</p>}</section>;
}

function KnowledgePointStats({ items }: { items: TeacherOverview["knowledge_point_stats"] }) {
  return <section className="teacher-panel"><header><div><h2>知识点掌握情况</h2><p>命中率越低，表示该评分点越需要补强</p></div></header>{items.length ? <div className="teacher-risk-list">{items.map((item) => <article key={item.knowledge_point_id}><div className={`teacher-risk-mark ${item.pass_rate != null && item.pass_rate < 60 ? "high" : item.pass_rate != null && item.pass_rate < 80 ? "medium" : "low"}`}><BookOpen size={16} /></div><div><div className="teacher-risk-heading"><strong>{item.name}</strong><span>{item.topic}</span></div><p>{item.exercises} 次练习{item.average_score != null ? ` · 均分 ${item.average_score}` : ""}{item.pass_rate != null ? ` · 通过率 ${item.pass_rate}%` : ""}</p>{item.weak_criteria.length > 0 && <div className="teacher-tags">{item.weak_criteria.map((criterion) => <span key={criterion.criterion}>{criterion.criterion} {criterion.hit_rate}%</span>)}</div>}</div></article>)}</div> : <p className="teacher-empty-state">暂无练习证据。</p>}</section>;
}

function ReportsPage({ data }: { data: TeacherOverview }) {
  const highRisk = data.weak_topics.filter((item) => item.risk === "high").length;
  const lowRisk = data.weak_topics.filter((item) => item.risk === "low").length;
  return <div className="teacher-stack">
    <div className="teacher-page-summary reports"><div><span className="teacher-eyebrow">LEARNING SIGNALS</span><h2>从练习证据发现薄弱项</h2><p>风险综合提问量、练习通过率与引导误解；它用于安排复习，不代表学生成绩。</p></div><BarChart3 size={46} /></div>
    <div className="teacher-insight-kpis"><article><Users /><span>覆盖学生</span><strong>{data.summary.students}</strong><small>{data.summary.sessions} 个会话</small></article><article><CheckCircle2 /><span>练习完成</span><strong>{data.summary.exercises}</strong><small>已评分题量</small></article><article><MessageCircleQuestion /><span>引导会话</span><strong>{data.summary.guided_sessions}</strong><small>苏格拉底式引导</small></article><article className={highRisk ? "warning" : ""}><AlertTriangle /><span>高风险主题</span><strong>{highRisk}</strong><small>建议优先复习</small></article><article><CheckCircle2 /><span>低风险主题</span><strong>{lowRisk}</strong><small>继续观察</small></article></div>
    <section className="teacher-panel"><header><div><h2>主题健康度</h2><p>练习通过率与引导误解越差，越值得优先安排讲解</p></div></header>{data.weak_topics.length ? <div className="teacher-risk-list">{data.weak_topics.map((item) => <article key={item.topic_id}><div className={`teacher-risk-mark ${item.risk}`}><AlertTriangle size={16} /></div><div><div className="teacher-risk-heading"><strong>{item.topic}</strong><span className={item.risk}>{riskText[item.risk]}</span></div><p>{item.questions} 个问题 · {item.exercises} 次练习{item.average_score != null ? ` · 均分 ${item.average_score}` : ""}{item.pass_rate != null ? ` · 通过率 ${item.pass_rate}%` : ""}{item.misconceptions > 0 ? ` · ${item.misconceptions} 处误解` : ""}</p></div></article>)}</div> : <p className="teacher-empty-state">暂无足够学习证据生成薄弱项。</p>}</section>
    <KnowledgePointStats items={data.knowledge_point_stats} />
  </div>;
}

export function TeacherWorkspace({ page: routedPage, onNavigate }: { page?: TeacherPage; onNavigate?: (page: TeacherPage) => void }) {
  const [localPage, setPage] = useState<TeacherPage>(routedPage ?? pageFromPath); const [data, setData] = useState<TeacherOverview | null>(null); const [catalog, setCatalog] = useState<TeacherCatalog | null>(null); const [workspaceId, setWorkspaceId] = useState("default"); const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [error, setError] = useState(""); const [saveMessage, setSaveMessage] = useState("");
  const page = routedPage ?? localPage;
  const load = useCallback(async () => { setLoading(true); setError(""); try { const auth = await ensureAuth(); if (!auth.roles.some((role) => role === "developer" || role === "teacher")) throw new Error("当前账户没有教师权限"); const settings = await api.getSettings(); const selectedWorkspaceId = resolveWorkspaceId(auth, settings.preferences.settings ?? {}); const [overview, catalogResult] = await Promise.all([page === "book" ? Promise.resolve(null) : api.getTeacherOverview(selectedWorkspaceId), api.getTeacherCatalog(selectedWorkspaceId)]); setWorkspaceId(selectedWorkspaceId); setData(overview); setCatalog({ ...catalogResult.catalog, guided_blueprints: catalogResult.catalog.guided_blueprints ?? [] }); } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); } finally { setLoading(false); } }, [page]);
  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  const save = useCallback(async () => { if (!catalog) return; setSaving(true); setSaveMessage(""); try { const result = await api.updateTeacherCatalog(workspaceId, { topics: catalog.topics, exercise_blueprints: catalog.exercise_blueprints, review_blueprints: catalog.review_blueprints, guided_blueprints: catalog.guided_blueprints }); setCatalog(result.catalog); setSaveMessage("已保存并同步到后端。"); } catch (reason) { setSaveMessage(`保存失败：${reason instanceof Error ? reason.message : String(reason)}`); } finally { setSaving(false); } }, [catalog, workspaceId]);
  const navigate = (next: TeacherPage) => { if (onNavigate) onNavigate(next); else { history.pushState({}, "", next === "overview" ? "/teacher" : `/teacher/${next}`); setPage(next); } };
  const editable = (children: ReactNode) => <>{children}<SaveActions saving={saving} message={saveMessage} onSave={() => void save()} /></>;
  const content = page === "book" ? <TeacherBookEditor workspaceId={workspaceId} catalog={catalog ?? undefined} onCatalogChange={setCatalog} /> : !data || !catalog ? null : page === "topics" ? editable(<TopicCatalogEditor topics={catalog.topics} onChange={(topics) => { setCatalog({ ...catalog, topics }); setSaveMessage(""); }} />) : page === "exercises" ? editable(<BlueprintCatalogEditor kind="exercise" topics={catalog.topics} blueprints={catalog.exercise_blueprints} exerciseBlueprints={catalog.exercise_blueprints} onChange={(exercise_blueprints) => { setCatalog({ ...catalog, exercise_blueprints: exercise_blueprints as TeacherCatalog["exercise_blueprints"] }); setSaveMessage(""); }} />) : page === "reviews" ? editable(<BlueprintCatalogEditor kind="review" topics={catalog.topics} blueprints={catalog.review_blueprints} exerciseBlueprints={catalog.exercise_blueprints} onChange={(review_blueprints) => { setCatalog({ ...catalog, review_blueprints: review_blueprints as TeacherCatalog["review_blueprints"] }); setSaveMessage(""); }} />) : page === "guided" ? editable(<GuidedBlueprintCatalogEditor topics={catalog.topics} blueprints={catalog.guided_blueprints} onChange={(guided_blueprints) => { setCatalog({ ...catalog, guided_blueprints }); setSaveMessage(""); }} />) : page === "questions" ? <QuestionsPage data={data} /> : page === "reports" ? <ReportsPage data={data} /> : <Overview data={data} catalog={catalog} />;
  return <div className="teacher-shell"><aside className="teacher-nav"><div className="teacher-brand"><GraduationCap /><span><strong>NLP 教师空间</strong><small>Teacher workspace</small></span><button type="button" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={15} />刷新</button></div><nav>{NAV.map(({ page: itemPage, label, icon: Icon }) => <button className={page === itemPage ? "active" : ""} type="button" key={itemPage} onClick={() => navigate(itemPage)}><Icon size={17} />{label}</button>)}</nav><a href="/"><ChevronLeft size={16} />返回学生模式</a></aside><main className={['teacher-main', page === "book" && "teacher-book-main"].filter(Boolean).join(" ")}><header className="teacher-topbar"><div><h1>{PAGE_LABELS[page]}</h1><span>{workspaceId} workspace · 目录修改需保存后生效</span></div><div className="teacher-topbar-actions"><SchoolLogo /></div></header><div className={`teacher-content ${page === "book" ? "teacher-content-book" : ""}`}>{loading ? <div className="teacher-state"><RefreshCw className="spin" />正在加载教学目录…</div> : error ? <div className="teacher-state error"><AlertCircle /><strong>无法进入教师模式</strong><p>{error}</p></div> : content}</div></main></div>;
}
