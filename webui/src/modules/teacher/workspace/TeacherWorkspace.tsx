import { AlertCircle, BarChart3, BookOpen, ChevronLeft, FileQuestion, GraduationCap, LayoutDashboard, MessageCircleQuestion, RefreshCw, Sparkles, Target, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { BlueprintCatalogEditor, GuidedBlueprintCatalogEditor, TopicCatalogEditor } from "@/modules/teacher/workspace/TeacherCatalogEditor";
import { TeacherBookEditor } from "@/modules/teacher/workspace/TeacherBookEditor";
import { StudentQuestionsPage } from "@/modules/teacher/workspace/StudentQuestionsPage";
import { LearningAnalysisPage } from "@/modules/teacher/workspace/LearningAnalysisPage";
import { SchoolLogo } from "@/shared/ui/SchoolLogo";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { api, ensureAuth } from "@/platform/http/api";
import type { TeacherCatalog, TeacherOverview, WeakTopic } from "@/shared/types";
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

type PendingTeacherAction = { kind: "navigate"; page: TeacherPage } | { kind: "reload" } | { kind: "exit" };

const RISK_LABEL: Record<WeakTopic["risk"], string> = { low: "低风险", medium: "中风险", high: "高风险" };

function Overview({ data }: { data: TeacherOverview }) {
  const { summary } = data;
  const weakTopics = (data.weak_topics ?? []).slice(0, 5);
  return <div className="teacher-stack">
    <section className="teacher-welcome"><div><h1>NLP 教师空间</h1><p>近 {data.period_days} 天学生的提问、会话与练习概览；下方列出当前最需要关注的弱知识点。</p></div><GraduationCap size={64} /></section>
    <div className="teacher-kpis">
      <article><Users /><span>学生</span><strong>{summary.students}</strong></article>
      <article><MessageCircleQuestion /><span>会话</span><strong>{summary.sessions}</strong></article>
      <article><FileQuestion /><span>提问</span><strong>{summary.questions}</strong></article>
      <article><Target /><span>练习通过率</span><strong>{summary.exercises ? `${summary.exercise_pass_rate}%` : "—"}</strong></article>
    </div>
    <section className="teacher-panel">
      <header><div><h2>弱知识点</h2><p>近期错误率偏高、建议优先关注的教学内容</p></div></header>
      {weakTopics.length ? <div className="teacher-weak-list">{weakTopics.map((item) => <article key={item.topic_id}><div className="teacher-weak-main"><strong>{item.topic}</strong><small>{item.questions} 次提问 · {item.errors} 次错误{typeof item.pass_rate === "number" ? ` · 通过率 ${item.pass_rate}%` : ""}</small></div><span className={item.risk}>{RISK_LABEL[item.risk]}</span></article>)}</div> : <p className="teacher-empty-inline">本期暂无提问数据，暂无法识别弱知识点。</p>}
    </section>
  </div>;
}

export function TeacherWorkspace({ page: routedPage, onNavigate }: { page?: TeacherPage; onNavigate?: (page: TeacherPage) => void }) {
  const [localPage, setPage] = useState<TeacherPage>(routedPage ?? pageFromPath);
  const [data, setData] = useState<TeacherOverview | null>(null);
  const [catalog, setCatalog] = useState<TeacherCatalog | null>(null);
  const [savedCatalogSnapshot, setSavedCatalogSnapshot] = useState("");
  const [bookDirty, setBookDirty] = useState(false);
  const [bookEditorGeneration, setBookEditorGeneration] = useState(0);
  const [pendingAction, setPendingAction] = useState<PendingTeacherAction | null>(null);
  const [workspaceId, setWorkspaceId] = useState("default");
  const [analyticsDays, setAnalyticsDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saveMessage, setSaveMessage] = useState("");
  const page = routedPage ?? localPage;
  const needsOverview = page === "overview" || page === "questions" || page === "reports";
  const isCatalogEditor = page === "topics" || page === "exercises" || page === "reviews" || page === "guided";
  const catalogDirty = Boolean(catalog && savedCatalogSnapshot && JSON.stringify(catalog) !== savedCatalogSnapshot);
  const dirty = catalogDirty || bookDirty;

  const load = useCallback(async (requestedDays = 30) => {
    setLoading(true);
    setError("");
    try {
      const auth = await ensureAuth();
      if (!auth.roles.some((role) => role === "developer" || role === "teacher")) throw new Error("当前账户没有教师权限");
      const settings = await api.getSettings();
      const selectedWorkspaceId = resolveWorkspaceId(auth, settings.preferences.settings ?? {});
      const [overview, catalogResult] = await Promise.all([needsOverview ? api.getTeacherOverview(selectedWorkspaceId, requestedDays) : Promise.resolve(null), api.getTeacherCatalog(selectedWorkspaceId)]);
      const nextCatalog = { ...catalogResult.catalog, guided_blueprints: catalogResult.catalog.guided_blueprints ?? [] };
      setWorkspaceId(selectedWorkspaceId);
      setData(overview);
      setCatalog(nextCatalog);
      setSavedCatalogSnapshot(JSON.stringify(nextCatalog));
      setBookDirty(false);
      setSaveMessage("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [needsOverview]);

  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  useEffect(() => {
    if (!dirty) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [dirty]);

  const save = useCallback(async () => {
    if (!catalog) return;
    setSaving(true);
    setSaveMessage("");
    try {
      const result = await api.updateTeacherCatalog(workspaceId, { topics: catalog.topics, exercise_blueprints: catalog.exercise_blueprints, review_blueprints: catalog.review_blueprints, guided_blueprints: catalog.guided_blueprints });
      setCatalog(result.catalog);
      setSavedCatalogSnapshot(JSON.stringify(result.catalog));
      setSaveMessage("已保存并同步到后端。");
    } catch (reason) {
      setSaveMessage(`保存失败：${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      setSaving(false);
    }
  }, [catalog, workspaceId]);

  const performNavigation = (next: TeacherPage) => {
    if (onNavigate) onNavigate(next);
    else {
      history.pushState({}, "", next === "overview" ? "/teacher" : `/teacher/${next}`);
      setPage(next);
    }
  };
  const navigate = (next: TeacherPage) => {
    if (next === page) return;
    if (dirty) setPendingAction({ kind: "navigate", page: next });
    else performNavigation(next);
  };
  const requestReload = () => { if (dirty) setPendingAction({ kind: "reload" }); else void load(analyticsDays); };
  const changeAnalyticsPeriod = (days: number) => { setAnalyticsDays(days); void load(days); };
  const discardDraft = () => {
    if (savedCatalogSnapshot) {
      try { setCatalog(JSON.parse(savedCatalogSnapshot) as TeacherCatalog); } catch { /* A server snapshot is always valid JSON. */ }
    }
    setBookDirty(false);
    setSaveMessage("");
  };
  const confirmPendingAction = () => {
    if (!pendingAction) return;
    const action = pendingAction;
    setPendingAction(null);
    discardDraft();
    if (action.kind === "navigate") performNavigation(action.page);
    else if (action.kind === "reload") {
      setBookEditorGeneration((current) => current + 1);
      void load();
    }
    else window.location.assign("/");
  };
  const saveProps = { saving, saveMessage, onSave: () => void save() };
  const updateDraft = (nextCatalog: TeacherCatalog) => { setCatalog(nextCatalog); setSaveMessage(""); };
  const content = page === "book"
    ? <TeacherBookEditor key={bookEditorGeneration} workspaceId={workspaceId} catalog={catalog ?? undefined} onCatalogChange={(nextCatalog) => { setCatalog(nextCatalog); setSavedCatalogSnapshot(JSON.stringify(nextCatalog)); }} onDirtyChange={setBookDirty} />
    : !catalog || (needsOverview && !data) ? null
      : page === "topics" ? <TopicCatalogEditor topics={catalog.topics} onChange={(topics) => updateDraft({ ...catalog, topics })} saveProps={saveProps} />
        : page === "exercises" ? <BlueprintCatalogEditor kind="exercise" topics={catalog.topics} blueprints={catalog.exercise_blueprints} onChange={(exercise_blueprints) => updateDraft({ ...catalog, exercise_blueprints: exercise_blueprints as TeacherCatalog["exercise_blueprints"] })} saveProps={saveProps} />
          : page === "reviews" ? <BlueprintCatalogEditor kind="review" topics={catalog.topics} blueprints={catalog.review_blueprints} onChange={(review_blueprints) => updateDraft({ ...catalog, review_blueprints: review_blueprints as TeacherCatalog["review_blueprints"] })} saveProps={saveProps} />
            : page === "guided" ? <GuidedBlueprintCatalogEditor topics={catalog.topics} blueprints={catalog.guided_blueprints} onChange={(guided_blueprints) => updateDraft({ ...catalog, guided_blueprints })} saveProps={saveProps} />
              : page === "questions" ? <StudentQuestionsPage data={data!} />
              : page === "reports" ? <LearningAnalysisPage data={data!} workspaceId={workspaceId} onPeriodChange={changeAnalyticsPeriod} />
              : <Overview data={data!} />;
  return <div className="teacher-shell"><aside className="teacher-nav"><div className="teacher-brand"><GraduationCap /><span><strong>NLP 教师空间</strong></span><button type="button" onClick={requestReload} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={15} />刷新</button></div><nav>{NAV.map(({ page: itemPage, label, icon: Icon }) => <button className={page === itemPage ? "active" : ""} type="button" key={itemPage} onClick={() => navigate(itemPage)}><Icon size={17} />{label}</button>)}</nav><a href="/" onClick={(event) => { if (!dirty) return; event.preventDefault(); setPendingAction({ kind: "exit" }); }}><ChevronLeft size={16} />返回学生模式</a></aside><main className={['teacher-main', page === "book" && "teacher-book-main", isCatalogEditor && "teacher-catalog-main", page === "questions" && "teacher-questions-main", page === "reports" && "teacher-analysis-main", page === "overview" && "teacher-overview-main"].filter(Boolean).join(" ")}><header className="teacher-topbar"><div><h1>{PAGE_LABELS[page]}</h1><span>{workspaceId} workspace · 目录修改需保存后生效</span></div><div className="teacher-topbar-actions"><SchoolLogo /></div></header><div className={`teacher-content ${page === "book" ? "teacher-content-book" : isCatalogEditor ? "teacher-content-catalog" : ""} ${page === "questions" ? "teacher-content-questions" : ""} ${page === "reports" ? "teacher-content-analysis" : ""} ${page === "overview" ? "teacher-content-overview" : ""}`}>{loading ? <div className="teacher-state"><RefreshCw className="spin" />正在加载教学目录…</div> : error ? <div className="teacher-state error"><AlertCircle /><strong>无法进入教师模式</strong><p>{error}</p></div> : content}</div></main>{pendingAction && <ConfirmDialog open title="有未保存的修改" description="当前页面存在未保存的内容，继续操作会丢弃这些修改。" confirmLabel="继续离开" cancelLabel="留在当前页面" onClose={() => setPendingAction(null)} onConfirm={confirmPendingAction} />}</div>;
}
