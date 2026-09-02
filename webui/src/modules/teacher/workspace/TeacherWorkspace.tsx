import { AlertCircle, BarChart3, BookOpen, ChevronLeft, Clock3, Coins, FileQuestion, GraduationCap, LayoutDashboard, MessageCircleQuestion, RefreshCw, Sparkles, Target, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { BlueprintCatalogEditor, GuidedBlueprintCatalogEditor, TopicCatalogEditor } from "@/modules/teacher/workspace/TeacherCatalogEditor";
import { TeacherBookEditor } from "@/modules/teacher/workspace/TeacherBookEditor";
import { StudentQuestionsPage } from "@/modules/teacher/workspace/StudentQuestionsPage";
import { LearningAnalysisPage } from "@/modules/teacher/workspace/LearningAnalysisPage";
import { SchoolLogo } from "@/shared/ui/SchoolLogo";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { api, ensureAuth } from "@/platform/http/api";
import type { ClassroomSummary, QuotaClassroomUsage, TeacherCatalog, TeacherOverview } from "@/shared/types";
import { resolveWorkspaceId } from "@/shared/utils/workspace";

export type TeacherPage = "overview" | "topics" | "book" | "exercises" | "reviews" | "guided" | "questions" | "reports" | "quota";
const NAV: Array<{ page: TeacherPage; label: string; icon: typeof LayoutDashboard }> = [
  { page: "overview", label: "教师首页", icon: LayoutDashboard }, { page: "book", label: "教材内容", icon: BookOpen }, { page: "topics", label: "主题与知识点", icon: BookOpen },
  { page: "exercises", label: "出题蓝图", icon: Sparkles }, { page: "reviews", label: "复习蓝图", icon: Target }, { page: "guided", label: "引导模式", icon: MessageCircleQuestion },
  { page: "questions", label: "学生问题", icon: FileQuestion }, { page: "reports", label: "学习分析", icon: BarChart3 },
  { page: "quota", label: "班级用量", icon: Users },
];
const PAGE_LABELS: Record<TeacherPage, string> = {
  overview: "教师首页", topics: "主题与知识点", book: "教材内容", exercises: "出题蓝图",
  reviews: "复习蓝图", guided: "引导模式", questions: "学生问题", reports: "学习分析", quota: "班级用量",
};
const pageFromPath = (): TeacherPage => {
  const candidate = location.pathname.split("/")[2] as TeacherPage;
  return candidate in PAGE_LABELS ? candidate : "overview";
};

type PendingTeacherAction = { kind: "navigate"; page: TeacherPage } | { kind: "reload" } | { kind: "exit" };

function Overview({ data, catalog }: { data: TeacherOverview; catalog: TeacherCatalog }) { return <div className="teacher-stack"><section className="teacher-welcome"><div><h1>NLP 教师空间</h1><p>在一个教学目录里维护主题、知识点与蓝图。学生端的可选主题和后端动态提示词都会使用这一份数据。</p></div><GraduationCap size={64} /></section><div className="teacher-kpis"><article><BookOpen /><span>教学主题</span><strong>{catalog.topics.length}</strong></article><article><Sparkles /><span>出题蓝图</span><strong>{catalog.exercise_blueprints.length}</strong></article><article><Target /><span>复习蓝图</span><strong>{catalog.review_blueprints.length}</strong></article><article><FileQuestion /><span>学生问题</span><strong>{data.summary.questions}</strong></article></div></div>; }

const quotaCredits = (micro: number) => `${(micro / 1_000_000).toFixed(2)} credits`;

export function ClassroomQuotaPage({ workspaceId }: { workspaceId: string }) {
  const [classrooms, setClassrooms] = useState<ClassroomSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [usage, setUsage] = useState<QuotaClassroomUsage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const selected = classrooms.find((item) => item.id === selectedId);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => { if (active) setLoading(true); });
    void api.listClassrooms().then((result) => {
      if (!active) return;
      const items = result.items.filter((item) => item.workspace_id === workspaceId);
      setClassrooms(items);
      setSelectedId((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? "");
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [workspaceId]);

  useEffect(() => {
    if (!selected) { queueMicrotask(() => setUsage(null)); return; }
    let active = true;
    queueMicrotask(() => { if (active) setLoading(true); });
    void api.getTeacherClassroomUsage(selected.id, workspaceId, 30).then((result) => { if (active) setUsage(result); }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [selected, workspaceId]);

  return <div className="teacher-stack teacher-quota-page">
    <div className="teacher-page-summary quota"><div><span className="teacher-eyebrow">CLASSROOM USAGE</span><h2>班级额度用量</h2><p>仅聚合当前工作空间内的课堂，帮助教师观察学生的模型消费和待对账事件。</p></div><Coins size={46} /></div>
    <section className="teacher-panel teacher-quota-toolbar"><label htmlFor="teacher-classroom-select">选择班级<select id="teacher-classroom-select" value={selectedId} onChange={(event) => setSelectedId(event.target.value)} disabled={!classrooms.length}>{classrooms.length ? classrooms.map((item) => <option key={item.id} value={item.id}>{item.name}</option>) : <option>暂无可查看班级</option>}</select></label><span>统计范围：近 30 天</span></section>
    {error ? <div className="teacher-state error"><AlertCircle /><p>{error}</p></div> : loading ? <div className="teacher-state"><RefreshCw className="spin" />正在加载班级用量…</div> : !usage ? <div className="teacher-state"><Users /><p>当前工作空间暂无可查看班级。</p></div> : <>
      <div className="teacher-insight-kpis teacher-quota-kpis"><article><Users /><span>活跃学生</span><strong>{usage.students}</strong><small>{usage.active_student_ids.length} 个有效成员</small></article><article><Coins /><span>已计价额度</span><strong>{quotaCredits(usage.priced_credits_micro)}</strong><small>{usage.priced_events} 条已计价事件</small></article><article className={usage.pending_events || usage.unavailable_events ? "warning" : ""}><Clock3 /><span>待处理事件</span><strong>{usage.pending_events + usage.unavailable_events}</strong><small>{usage.pending_events ? `有 ${usage.pending_events} 条用量待对账` : usage.unavailable_events ? `${usage.unavailable_events} 条无法计价` : "当前已完成对账"}</small></article></div>
      <section className="teacher-panel"><header><div><h2>{selected?.name ?? "班级"} · 学生用量</h2><p>基于原始 UsageEvent 聚合，不修改个人和班级 Ledger</p></div></header>{usage.by_user.length ? <div className="teacher-table"><table><thead><tr><th>用户</th><th>事件</th><th>Token</th><th>Credits</th><th>状态</th></tr></thead><tbody>{usage.by_user.map((item) => <tr key={item.user_id}><td>{item.user_id}</td><td>{item.events}</td><td>{item.total_tokens.toLocaleString()}</td><td>{quotaCredits(item.priced_credits_micro)}</td><td>{item.pending_events ? `待对账 ${item.pending_events}` : item.unavailable_events ? `无法计价 ${item.unavailable_events}` : "已完成"}</td></tr>)}</tbody></table></div> : <p className="teacher-empty-state">近 30 天暂无模型调用记录。</p>}</section>
    </>}
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
                : page === "quota" ? <ClassroomQuotaPage workspaceId={workspaceId} />
                : <Overview data={data!} catalog={catalog} />;
  return <div className="teacher-shell"><aside className="teacher-nav"><div className="teacher-brand"><GraduationCap /><span><strong>NLP 教师空间</strong></span><button type="button" onClick={requestReload} disabled={loading}><RefreshCw className={loading ? "spin" : ""} size={15} />刷新</button></div><nav>{NAV.map(({ page: itemPage, label, icon: Icon }) => <button className={page === itemPage ? "active" : ""} type="button" key={itemPage} onClick={() => navigate(itemPage)}><Icon size={17} />{label}</button>)}</nav><a href="/" onClick={(event) => { if (!dirty) return; event.preventDefault(); setPendingAction({ kind: "exit" }); }}><ChevronLeft size={16} />返回学生模式</a></aside><main className={['teacher-main', page === "book" && "teacher-book-main", isCatalogEditor && "teacher-catalog-main", page === "questions" && "teacher-questions-main", page === "reports" && "teacher-analysis-main"].filter(Boolean).join(" ")}><header className="teacher-topbar"><div><h1>{PAGE_LABELS[page]}</h1><span>{workspaceId} workspace · 目录修改需保存后生效</span></div><div className="teacher-topbar-actions"><SchoolLogo /></div></header><div className={`teacher-content ${page === "book" ? "teacher-content-book" : isCatalogEditor ? "teacher-content-catalog" : ""} ${page === "questions" ? "teacher-content-questions" : ""} ${page === "reports" ? "teacher-content-analysis" : ""}`}>{loading ? <div className="teacher-state"><RefreshCw className="spin" />正在加载教学目录…</div> : error ? <div className="teacher-state error"><AlertCircle /><strong>无法进入教师模式</strong><p>{error}</p></div> : content}</div></main>{pendingAction && <ConfirmDialog open title="有未保存的修改" description="当前页面存在未保存的内容，继续操作会丢弃这些修改。" confirmLabel="继续离开" cancelLabel="留在当前页面" onClose={() => setPendingAction(null)} onConfirm={confirmPendingAction} />}</div>;
}
