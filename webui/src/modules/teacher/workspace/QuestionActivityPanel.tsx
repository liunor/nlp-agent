import { useEffect, useRef, useState, type FormEvent } from "react";
import { Bot } from "lucide-react";
import { api } from "@/platform/http/api";
import type { TeacherQuestionActivity } from "@/shared/types";
import { CollapsibleText } from "@/shared/ui/CollapsibleText";

export function initialQuestionPeriod() {
  const end = new Date();
  const start = new Date(end); start.setDate(start.getDate() - 29);
  const format = (value: Date) => `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,"0")}-${String(value.getDate()).padStart(2,"0")}`;
  return { start: format(start), end: format(end), search: "" };
}
const formatTime = (value: string) => value.replace("T", " ").slice(0,19);

function QuestionPagination({ page, total, disabled, onChange }: { page: number; total: number; disabled: boolean; onChange: (page: number) => void }) {
  const pages = Math.max(1,Math.ceil(total/10));
  return <footer className="teacher-question-pagination"><span>共 {total} 条 · 每页 10 条</span><div>
    <button type="button" disabled={disabled || page<=1} onClick={() => onChange(page-1)}>上一页</button><strong>{page} / {pages}</strong>
    <button type="button" disabled={disabled || page>=pages} onClick={() => onChange(page+1)}>下一页</button>
  </div></footer>;
}

export function QuestionActivityPanel({ workspaceId, classroomId, onPeriodApplied }: { workspaceId: string; classroomId?: string; onPeriodApplied?: (value: { start: string; end: string; search: string }) => void }) {
  const [draft,setDraft] = useState(initialQuestionPeriod);
  const [period,setPeriod] = useState(draft);
  const [page,setPage] = useState(1);
  const [frequentPage,setFrequentPage] = useState(1);
  const [student,setStudent] = useState<{ id: string; name: string } | null>(null);
  const [group,setGroup] = useState<{ id: string; name: string } | null>(null);
  const [data,setData] = useState<TeacherQuestionActivity | null>(null);
  const [expandedAnswers, setExpandedAnswers] = useState<Set<string>>(() => new Set());
  const [loading,setLoading] = useState(true);
  const [validationError,setValidationError] = useState("");
  const [error,setError] = useState("");
  const [revision,setRevision] = useState(0);
  const [highlightDetails, setHighlightDetails] = useState(false);
  const detailsSectionRef = useRef<HTMLElement>(null);
  const pendingScrollToDetailsRef = useRef(false);
  const highlightTimerRef = useRef<number | null>(null);

  const triggerDetailsHighlight = () => {
    setHighlightDetails(true);
    if (highlightTimerRef.current !== null) window.clearTimeout(highlightTimerRef.current);
    highlightTimerRef.current = window.setTimeout(() => {
      setHighlightDetails(false);
      highlightTimerRef.current = null;
    }, 1400);
  };

  const focusDetailsSection = () => {
    pendingScrollToDetailsRef.current = true;
    triggerDetailsHighlight();
    window.requestAnimationFrame(() => {
      detailsSectionRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    });
  };

  useEffect(() => () => {
    if (highlightTimerRef.current !== null) window.clearTimeout(highlightTimerRef.current);
  }, []);

  useEffect(() => {
    if (!loading && pendingScrollToDetailsRef.current) {
      pendingScrollToDetailsRef.current = false;
      detailsSectionRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    }
  }, [loading, data]);

  const toggleAnswer = (id: string) => {
    setExpandedAnswers((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const params = new URLSearchParams({workspace_id:workspaceId,start_date:period.start,end_date:period.end,search:period.search,page:String(page),frequent_page:String(frequentPage)});
    if(classroomId) params.set("classroom_id",classroomId);
    if(student) params.set("student_id",student.id);
    if(group) params.set("group_id",group.id);
    queueMicrotask(() => {
      if (!active) return;
      setLoading(true); setError("");
      void api.getTeacherQuestionActivity(params,controller.signal).then((result) => {
          if (!active) return;
          const validPage = Math.min(page, Math.max(1, Math.ceil(result.total/10)));
          const validFrequentPage = Math.min(frequentPage, Math.max(1, Math.ceil(result.frequent_total/10)));
          if (validPage !== page || validFrequentPage !== frequentPage) {
            setPage(validPage); setFrequentPage(validFrequentPage); return;
          }
          setData(result);
        })
        .catch((reason: unknown) => { if(active) setError(reason instanceof Error ? reason.message : "读取提问失败"); })
        .finally(() => { if(active) setLoading(false); });
    });
    return () => { active=false; controller.abort(); };
  },[workspaceId,classroomId,period,page,frequentPage,student,group,revision]);
  const apply = (event: FormEvent) => {
    event.preventDefault();
    const days = (Date.parse(draft.end)-Date.parse(draft.start))/86400000;
    if(!draft.start || !draft.end || days<0 || days>=365) { setValidationError("请选择不超过365天的有效时间段"); return; }
    setValidationError("");
    setPeriod({...draft});
    onPeriodApplied?.({...draft}); setPage(1); setFrequentPage(1); setStudent(null); setGroup(null); setRevision((value) => value+1);
  };
  return <section className="teacher-analysis-panel teacher-question-explorer" aria-label="提问分析">
    <header><div><h2>提问分析</h2><p>查看所选班级在指定日期内的提问明细与共同高频问题。</p></div></header>
    <form className="teacher-question-query" onSubmit={apply}>
      <label>开始日期<input type="date" aria-label="提问开始日期" required value={draft.start} onChange={(event) => setDraft({...draft,start:event.target.value})} /></label>
      <label>结束日期<input type="date" aria-label="提问结束日期" required value={draft.end} onChange={(event) => setDraft({...draft,end:event.target.value})} /></label>
      <label>问题内容<input aria-label="搜索提问内容" placeholder="输入关键词" maxLength={200} value={draft.search} onChange={(event) => setDraft({...draft,search:event.target.value})} /></label>
      <button type="submit" disabled={loading}>查询</button>
    </form>
    {validationError && <p className="teacher-question-validation" role="alert">{validationError}</p>}
    <div className="teacher-question-query-summary" aria-live="polite">{loading ? "正在读取提问…" : error ? <><span role="alert">{error}</span><button type="button" onClick={() => setRevision((value) => value+1)}>重试</button></> : data && <span>{data.start_date} 至 {data.end_date} · {data.summary.question_count} 次提问 · {data.summary.user_count} 名提问用户 · {data.timezone}</span>}</div>
    <section ref={detailsSectionRef} className={highlightDetails ? "teacher-question-details is-highlighted" : "teacher-question-details"} aria-label="提问明细">
      <div className="teacher-question-table-heading"><h3>提问明细</h3><div>{student && <span>用户：{student.name}（共提问 {data?.total ?? 0} 个问题）</span>}{group && <span>问题：{group.name.slice(0,30)}</span>}{(student || group) && <button type="button" onClick={() => {setStudent(null);setGroup(null);setPage(1);}}>清除明细筛选</button>}</div></div>
      <div className="teacher-question-records"><table><thead><tr><th>提问时间</th><th>提问用户</th><th>问题原文</th></tr></thead><tbody>
        {loading || error ? <tr><td colSpan={3}>{loading ? "正在加载…" : "读取失败，请重试"}</td></tr> : data?.items.length ? data.items.map((item) => <tr key={item.id}><td>{formatTime(item.created_at)}</td><td><button type="button" title="查看该用户的提问" onClick={() => {setStudent({id:item.user_id,name:item.display_name || item.username});setPage(1);}}>{item.display_name || item.username}</button><small>@{item.username}</small></td><td className="question-text"><div className="teacher-question-cell-wrap"><CollapsibleText text={item.question} maxLines={3} maxLength={120} />{item.answer ? <div className="teacher-question-answer-area"><button type="button" className="teacher-question-answer-btn" aria-expanded={expandedAnswers.has(item.id)} onClick={() => toggleAnswer(item.id)}><Bot size={12} /><span>{expandedAnswers.has(item.id) ? "收起模型答复" : "查看模型答复"}</span></button>{expandedAnswers.has(item.id) && <div className="teacher-question-answer-card"><div className="teacher-question-answer-header"><span className="teacher-question-model-tag">AI 模型答复</span>{item.status && <span className={`teacher-question-status-tag ${item.status}`}>{item.status === "completed" ? "已完成" : item.status}</span>}</div><CollapsibleText text={item.answer} maxLines={6} maxLength={200} /></div>}</div> : null}</div></td></tr>) : <tr><td colSpan={3}>当前范围暂无提问。</td></tr>}
      </tbody></table></div>
      <QuestionPagination page={page} total={data?.total ?? 0} disabled={loading || Boolean(error)} onChange={setPage} />
    </section>
    <section aria-label="共同高频问题">
      <div className="teacher-question-table-heading"><div><h3>共同高频问题</h3><p>至少两人提问才收录；仅合并空白、标点和大小写差异，按涉及人数、提问次数排序。此表不受个人明细筛选影响。</p></div></div>
      <div className="teacher-question-records"><table><thead><tr><th>代表问题</th><th>涉及人数</th><th>提问次数</th><th>操作</th></tr></thead><tbody>
        {loading || error ? <tr><td colSpan={4}>{loading ? "正在加载…" : "读取失败，请重试"}</td></tr> : data?.frequent.length ? data.frequent.map((item) => <tr key={item.group_id}><td className="question-text"><CollapsibleText text={item.question} maxLines={3} maxLength={120} /></td><td>{item.user_count}</td><td>{item.question_count}</td><td><button type="button" onClick={() => {setGroup({id:item.group_id,name:item.question});setStudent(null);setPage(1);focusDetailsSection();}}>查看原始提问</button></td></tr>) : <tr><td colSpan={4}>当前范围暂无多人重复提问。</td></tr>}
      </tbody></table></div>
      <QuestionPagination page={frequentPage} total={data?.frequent_total ?? 0} disabled={loading || Boolean(error)} onChange={setFrequentPage} />
    </section>
  </section>;
}
