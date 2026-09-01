import { AlertCircle, ArrowDownRight, ArrowUpRight, BarChart3, Check, ChevronDown, CircleHelp, Eye, Flag, MessageSquare, RefreshCw, Sparkles, Undo2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/platform/http/api";
import type { LearningAnalysisDiagnosis, TeacherAIDiagnosis, TeacherAIAnalysisResult, TeacherLearningAnalysis, TeacherOverview } from "@/shared/types";

const PROBLEM_COLORS = ["#6758c9", "#4d8fd9", "#e2913d", "#d36a91", "#3ca579", "#929aaa"];
const MAX_NOTE_LENGTH = 2000;
const EMPTY_ANALYSIS: TeacherLearningAnalysis = {
  scope: { period_days: 30, period_label: "近 30 天", role_label: "学生", student_count: 0, attempt_count: 0 },
  conclusions: { weak: null, declining: null, good: null },
  diagnoses: [],
  problem_distribution: [],
  mastery_trend: { months: [], series: [] },
};
type AIState = "not-generated" | "loading" | "completed" | "failed" | "expired";
type SaveState = "idle" | "saving" | "saved" | "error";

function rateText(value: number | null) {
  return value == null ? "—" : `${value}%`;
}

function deltaText(current: number | null, previous: number | null) {
  if (current == null || previous == null) return "暂无对比";
  const delta = Math.round((current - previous) * 100) / 100;
  return `${delta > 0 ? "+" : ""}${delta}%`;
}

function TrendBadge({ item }: { item: LearningAnalysisDiagnosis }) {
  if (item.trend === "down") return <span className="teacher-analysis-trend down"><ArrowDownRight size={13} />下降</span>;
  if (item.trend === "up") return <span className="teacher-analysis-trend up"><ArrowUpRight size={13} />上升</span>;
  return <span className="teacher-analysis-trend stable">{item.previous_mastery_rate == null ? "暂无对比" : item.mastery_rate != null && item.mastery_rate >= 80 ? "稳定" : "稳定偏低"}</span>;
}

function ConclusionCard({ label, item, tone, onView }: { label: string; item: LearningAnalysisDiagnosis | null; tone: string; onView: (id: string) => void }) {
  return <article className={`teacher-analysis-conclusion-card ${tone}`}>
    <div className="teacher-analysis-conclusion-label"><span>{label}</span><Sparkles size={15} /></div>
    {item ? <>
      <strong title={item.knowledge_point_name}>{item.knowledge_point_name}</strong>
      <small>{item.content_name} · {item.student_count} 名学生涉及</small>
      <div className="teacher-analysis-conclusion-meta"><b>{rateText(item.mastery_rate)}</b><TrendBadge item={item} /></div>
    </> : <p className="teacher-analysis-empty-inline">暂无足够数据</p>}
    {item && <><span className="teacher-analysis-conclusion-hint">与上期 {deltaText(item.mastery_rate, item.previous_mastery_rate)}</span><button className="teacher-analysis-conclusion-view" type="button" aria-label={`查看详情 ${item.knowledge_point_name}`} onClick={() => onView(item.knowledge_point_id)}>查看详情</button></>}
  </article>;
}

function buildPath(values: Array<number | null>, x: (index: number) => number, y: (value: number) => number) {
  let path = "";
  values.forEach((value, index) => {
    if (value == null) return;
    const previous = index > 0 ? values[index - 1] : null;
    path += previous == null ? `M ${x(index)} ${y(value)}` : ` L ${x(index)} ${y(value)}`;
  });
  return path;
}

function MasteryTrendChart({ analysis }: { analysis: TeacherLearningAnalysis }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const months = analysis.mastery_trend.months;
  const series = analysis.mastery_trend.series;
  const values = series.flatMap((item) => item.values).filter((value): value is number => value != null);
  const rawMax = values.length ? Math.max(...values) : 0;
  const axisMax = rawMax === 0 ? 100 : Math.min(100, Math.max(10, Math.ceil((rawMax + 5) / 10) * 10));
  const chartHeight = Math.min(480, Math.max(300, 270 + series.length * 22));
  const left = 58;
  const right = 22;
  const top = 20;
  const bottom = 42;
  const plotWidth = 900 - left - right;
  const plotHeight = chartHeight - top - bottom;
  const x = (index: number) => months.length <= 1 ? left + plotWidth / 2 : left + (index / (months.length - 1)) * plotWidth;
  const y = (value: number) => top + plotHeight - (value / axisMax) * plotHeight;
  const yTicks = [0, axisMax / 4, axisMax / 2, axisMax * 0.75, axisMax];
  return <div className="teacher-analysis-chart-wrap">
    <div className="teacher-analysis-chart-scroll">
      <svg className="teacher-analysis-line-chart" role="img" aria-label="内容掌握趋势折线图" viewBox={`0 0 900 ${chartHeight}`} data-raw-max={rawMax} data-axis-max={axisMax}>
        <title>最近五个月知识点掌握率趋势</title>
        {yTicks.map((tick) => <g key={tick}><line className="teacher-analysis-grid" x1={left} x2={900 - right} y1={y(tick)} y2={y(tick)} /><text className="teacher-analysis-axis-label" x={left - 10} y={y(tick) + 4} textAnchor="end">{Math.round(tick)}%</text></g>)}
        {months.map((month, index) => <text key={month.month} className="teacher-analysis-axis-label" x={x(index)} y={chartHeight - 13} textAnchor="middle">{month.label.slice(5)}</text>)}
        {series.map((item, index) => <path key={item.knowledge_point_id} className={`teacher-analysis-line-series ${index === 0 ? "primary" : ""}`} stroke={PROBLEM_COLORS[index % PROBLEM_COLORS.length]} d={buildPath(item.values, x, y)} />)}
        {months.map((month, index) => <rect key={month.month} className="teacher-analysis-hover-target" x={x(index) - Math.max(24, plotWidth / Math.max(months.length, 1) / 2)} y={top} width={Math.max(48, plotWidth / Math.max(months.length, 1))} height={plotHeight} tabIndex={0} role="button" aria-label={`查看${month.label}掌握率`} onMouseEnter={() => setHoverIndex(index)} onFocus={() => setHoverIndex(index)} onMouseLeave={() => setHoverIndex(null)} onBlur={() => setHoverIndex(null)} />)}
        {hoverIndex != null && <line className="teacher-analysis-hover-line" x1={x(hoverIndex)} x2={x(hoverIndex)} y1={top} y2={top + plotHeight} />}
      </svg>
    </div>
    <div className="teacher-analysis-line-legend">{series.map((item, index) => <span key={item.knowledge_point_id}><i style={{ background: PROBLEM_COLORS[index % PROBLEM_COLORS.length] }} />{item.name}</span>)}</div>
    {hoverIndex != null && <div className="teacher-analysis-chart-tooltip" role="tooltip"><strong>{months[hoverIndex]?.label}</strong>{series.map((item, index) => <span key={item.knowledge_point_id}><i style={{ background: PROBLEM_COLORS[index % PROBLEM_COLORS.length] }} />{item.name} <b>{item.values[hoverIndex] == null ? "暂无数据" : `${item.values[hoverIndex]}%`}</b></span>)}</div>}
  </div>;
}

function ProblemDistributionChart({ items }: { items: TeacherLearningAnalysis["problem_distribution"] }) {
  const visible = items.length ? items : ["概念掌握不足", "解题方法不熟", "易错点集中", "练习覆盖不足", "学习参与不足", "数据不足，暂不判断"].map((name) => ({ name, count: 0, percentage: 0 }));
  const max = Math.max(1, ...visible.map((item) => item.count));
  const chartHeight = Math.max(270, 62 + visible.length * 39);
  return <div className="teacher-analysis-bar-chart-wrap"><svg className="teacher-analysis-bar-chart" role="img" aria-label="内容问题类型分布横向条形图" viewBox={`0 0 900 ${chartHeight}`}>
    <title>内容问题类型分布</title>
    {visible.map((item, index) => { const y = 25 + index * 39; const width = item.count ? Math.max(6, item.count / max * 650) : 0; return <g key={item.name}><text className="teacher-analysis-bar-label" x="0" y={y + 14}>{item.name}</text><rect className="teacher-analysis-bar-track" x="180" y={y} width="650" height="17" rx="8" /><rect className="teacher-analysis-bar" x="180" y={y} width={width} height="17" rx="8" fill={PROBLEM_COLORS[index % PROBLEM_COLORS.length]} /><text className="teacher-analysis-bar-value" x="850" y={y + 14} textAnchor="end">{item.count} 个 · {item.percentage}%</text></g>; })}
  </svg></div>;
}

function DiagnosisDetail({ item, focused, expanded, onToggle, onFocus, onIgnore, note, onNote, onNoteCommit }: { item: LearningAnalysisDiagnosis; focused: boolean; expanded: boolean; onToggle: () => void; onFocus: () => void; onIgnore: () => void; note: string; onNote: (value: string) => void; onNoteCommit: () => void }) {
  const [noteOpen, setNoteOpen] = useState(false);
  return <article className={`teacher-analysis-diagnosis ${focused ? "focused" : ""}`}>
    <div className="teacher-analysis-diagnosis-main">
      <div className="teacher-analysis-diagnosis-content"><small>{item.content_name}</small><strong>{item.knowledge_point_name}</strong></div>
      <div className="teacher-analysis-diagnosis-rate"><b>{rateText(item.mastery_rate)}</b>{item.mastery_basis === "exercise" && <em className="teacher-analysis-basis" title="掌握率为整题级归因，非评分点粒度的评价">整题级</em>}<span>{item.student_count} 人 · 问题 {item.question_count}</span><small>作答 {item.attempt_count} · 正确 {item.correct_count} · 上期 {rateText(item.previous_mastery_rate)}</small></div>
      <TrendBadge item={item} />
      <div className="teacher-analysis-problem-cell"><span className={`teacher-analysis-problem ${item.problem_type === "—" ? "none" : ""}`}>{item.problem_type}</span><small className={`teacher-analysis-sufficiency ${item.data_sufficiency}`}>{item.data_sufficiency === "sufficient" ? "样本充足" : "样本不足"}</small></div>
      <button className="teacher-analysis-view" type="button" aria-expanded={expanded} aria-label={`${expanded ? "收起建议" : "查看建议"} ${item.knowledge_point_name}`} onClick={onToggle}>{expanded ? "收起" : "查看建议"}<ChevronDown size={14} /></button>
    </div>
    {expanded && <div className="teacher-analysis-diagnosis-detail">
      <div className="teacher-analysis-evidence"><div><span>分析结论</span><p>{item.recommendation.conclusion}</p></div><div><span>统计证据</span><p>{item.correct_count} / {item.attempt_count} 次作答正确，错误 {item.error_count ?? item.attempt_count - item.correct_count} 次；重复错误学生 {item.repeated_error_student_count ?? 0} 人；平均得分 {item.average_score == null ? "暂无" : item.average_score} 分；上期掌握率 {rateText(item.previous_mastery_rate)}。</p></div><div><span>建议动作</span><p>{item.recommendation.action}</p></div></div>
      {item.weak_criteria.length > 0 && <div className="teacher-analysis-criteria"><span>重复错误评分点</span>{item.weak_criteria.map((criterion) => <em key={criterion.criterion}>{criterion.criterion} · 错误率 {criterion.error_rate}%</em>)}</div>}
      {item.question_examples?.length ? <div className="teacher-analysis-question-examples"><span>题目示例</span>{item.question_examples.map((example) => <p key={example.question_id}>{example.question}</p>)}</div> : null}
      <div className="teacher-analysis-actions"><button type="button" onClick={onFocus}>{focused ? <><Check size={14} />取消关注</> : <><Flag size={14} />标记关注</>} {item.knowledge_point_name}</button><button type="button" onClick={onIgnore}><Eye size={14} />忽略 {item.knowledge_point_name}</button><button type="button" onClick={() => setNoteOpen((value) => !value)}><MessageSquare size={14} />添加备注 {item.knowledge_point_name}</button></div>
      {noteOpen && <><textarea aria-label={`${item.knowledge_point_name}备注`} value={note} maxLength={MAX_NOTE_LENGTH} onChange={(event) => onNote(event.target.value)} onBlur={onNoteCommit} placeholder="记录课堂观察或后续跟进计划…" /><small className="teacher-analysis-note-count">{note.length}/{MAX_NOTE_LENGTH}</small></>}
    </div>}
  </article>;
}

function priorityLabel(level: TeacherAIDiagnosis["level"]) {
  return level === "high" ? "高关注" : level === "medium" ? "建议关注" : "持续观察";
}

function AIDiagnosisCard({ item, base, focused, onFocus, onIgnore }: { item: TeacherAIDiagnosis; base: LearningAnalysisDiagnosis; focused: boolean; onFocus: () => void; onIgnore: () => void }) {
  const [showEvidence, setShowEvidence] = useState(false);
  const examples = item.question_examples ?? [];
  return <article className={`teacher-analysis-ai-diagnosis ${item.level}`}>
    <header><div><span>{item.knowledge_point_name}</span><strong>{priorityLabel(item.level)}</strong></div><small>{item.error_type} · 置信度 {item.confidence === "high" ? "高" : item.confidence === "low" ? "低" : "中"}</small></header>
    <div className="teacher-analysis-ai-metrics"><div><span>掌握率</span><strong>{rateText(base.mastery_rate)}</strong></div><div><span>较上期</span><strong>{deltaText(base.mastery_rate, base.previous_mastery_rate)}</strong></div><div><span>涉及学生</span><strong>{base.student_count} 人</strong></div><div><span>作答次数</span><strong>{base.attempt_count} 次</strong></div></div>
    <section><span>AI 判断</span><p>{item.problem}</p></section>
    <section><span>教学参考建议</span><dl><div><dt>问题表现</dt><dd>{item.problem}</dd></div><div><dt>可能原因</dt><dd>{item.cause || item.problem}</dd></div><div><dt>参考做法</dt><dd><ol>{item.suggestions.map((suggestion) => <li key={suggestion}>{suggestion}</li>)}</ol></dd></div><div><dt>建议优先级</dt><dd>{priorityLabel(item.level)}</dd></div></dl></section>
    {showEvidence && <div className="teacher-analysis-ai-evidence"><strong>判断依据（以后端统计为准）</strong>{item.evidence.map((evidence) => <p key={evidence}>{evidence}</p>)}{examples.length > 0 && <><strong>对应题目</strong>{examples.map((example) => <p key={example.question_id}>{example.question}</p>)}</>}</div>}
    <footer><button type="button" onClick={() => setShowEvidence((value) => !value)}>{showEvidence ? "收起证据" : "查看证据"}</button><button type="button" onClick={onFocus}>{focused ? <><Check size={14} />取消关注</> : <><Flag size={14} />标记关注</>}</button><button type="button" onClick={onIgnore}><Eye size={14} />忽略</button></footer>
  </article>;
}

function generatedTime(value: string | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function scopedConclusions(items: LearningAnalysisDiagnosis[]) {
  return {
    weak: items.find((item) => item.data_sufficiency === "sufficient" && item.mastery_rate != null && item.mastery_rate < 60) ?? null,
    declining: items.find((item) => item.trend === "down") ?? null,
    good: [...items].reverse().find((item) => item.data_sufficiency === "sufficient" && item.mastery_rate != null && item.mastery_rate >= 80 && item.trend !== "down") ?? null,
  };
}

function scopeAnalysis(analysis: TeacherLearningAnalysis, items: LearningAnalysisDiagnosis[]): TeacherLearningAnalysis {
  const counts = new Map<string, number>(items.map((item) => [item.problem_type, 0]));
  const pointIds = new Set(items.map((item) => item.knowledge_point_id));
  const pointNames = new Set(items.map((item) => item.knowledge_point_name));
  items.forEach((item) => counts.set(item.problem_type, (counts.get(item.problem_type) ?? 0) + 1));
  const total = [...counts.values()].reduce((sum, count) => sum + (count > 0 ? count : 0), 0);
  return {
    ...analysis,
    diagnoses: items,
    conclusions: scopedConclusions(items),
    problem_distribution: analysis.problem_distribution.map((item) => {
      const count = counts.get(item.name) ?? 0;
      return { ...item, count, percentage: total ? Math.round((count / total) * 10000) / 100 : 0 };
    }),
    mastery_trend: {
      ...analysis.mastery_trend,
      series: analysis.mastery_trend.series.filter((item) => pointIds.has(item.knowledge_point_id) || pointNames.has(item.name)),
    },
  };
}

function AIAnalysisPanel({ state, result, error, onGenerate, analysis, ignored, focused, onFocus, onIgnore }: { state: AIState; result: TeacherAIAnalysisResult | null; error: string; onGenerate: () => void; analysis: TeacherLearningAnalysis; ignored: Set<string>; focused: Set<string>; onFocus: (id: string) => void; onIgnore: (id: string) => void }) {
  const aiDiagnoses = (result?.diagnoses ?? []).filter((item) => !ignored.has(item.knowledge_point_id)).slice(0, 5);
  const baseById = new Map(analysis.diagnoses.map((item) => [item.knowledge_point_id, item]));
  const statusText = state === "loading" ? "正在分析" : state === "completed" ? "分析完成" : state === "failed" ? "分析失败 · 已展示规则版诊断" : state === "expired" ? "已过期" : "未生成";
  return <section className={`teacher-analysis-ai-panel ${state}`} aria-label="AI 内容分析">
    <header className="teacher-analysis-ai-header"><div><h2>AI 内容分析</h2><p>AI 只解释后端统计识别出的内容问题，数字、证据和学生范围以系统统计为准。</p></div><div className="teacher-analysis-ai-header-actions"><span className={`teacher-analysis-ai-status ${state}`}>{state === "loading" && <RefreshCw size={13} className="spin" />}{statusText}</span><button type="button" onClick={onGenerate} disabled={state === "loading"}>{state === "not-generated" ? <Sparkles size={15} /> : <RefreshCw size={15} />}{state === "not-generated" ? "生成 AI 内容分析" : "重新生成分析"}</button></div></header>
    {state === "not-generated" && <div className="teacher-analysis-ai-placeholder"><Sparkles size={23} /><div><strong>尚未生成 AI 内容分析</strong><p>统计数据已经准备好。点击按钮后，系统会把前 5 个重点知识点交给 DeepSeek 解释，不会在页面进入时自动调用模型。</p></div></div>}
    {state === "loading" && <div className="teacher-analysis-ai-placeholder"><RefreshCw size={23} className="spin" /><div><strong>正在分析学生在各知识点上的学习表现</strong><p>正在整理证据并等待 DeepSeek 返回结构化诊断，请稍候。</p></div></div>}
    {state === "expired" && <div className="teacher-analysis-ai-expired"><AlertCircle size={18} /><div><strong>当前筛选条件已变化，请重新生成分析</strong><p>上一版 AI 结果对应旧的内容范围或时间范围，暂不作为当前判断。</p></div><button type="button" onClick={onGenerate}>重新生成分析</button></div>}
    {(state === "completed" || state === "failed") && result && <>
      <div className="teacher-analysis-ai-summary"><p>{result.summary}</p><div className="teacher-analysis-ai-meta"><span>分析范围：{analysis.scope.period_label}</span><span>学生：{analysis.scope.student_count} 人</span><span>作答：{analysis.scope.attempt_count} 次</span><span>生成：{generatedTime(result.generated_at)}</span><span>模型：{result.source === "deepseek" ? result.model : `${result.model} · 规则兜底`}</span></div></div>
      {result.message && <div className="teacher-analysis-ai-message"><AlertCircle size={15} />{result.message}</div>}
      {aiDiagnoses.length > 0 ? <div className="teacher-analysis-ai-diagnoses">{aiDiagnoses.map((item) => { const base = baseById.get(item.knowledge_point_id); return base ? <AIDiagnosisCard key={item.knowledge_point_id} item={item} base={base} focused={focused.has(item.knowledge_point_id)} onFocus={() => onFocus(item.knowledge_point_id)} onIgnore={() => onIgnore(item.knowledge_point_id)} /> : null; })}</div> : <div className="teacher-analysis-ai-no-diagnosis">当前范围没有通过证据校验的 AI 诊断；请先补充学生练习数据，或继续使用下方规则统计。</div>}
    </>}
    {state === "failed" && error && <div className="teacher-analysis-ai-message"><AlertCircle size={15} />{error}</div>}
  </section>;
}

export function LearningAnalysisPage({ data, workspaceId = "default", onPeriodChange }: { data: TeacherOverview; workspaceId?: string; onPeriodChange?: (days: number) => void }) {
  const analysis = data.learning_analysis ?? EMPTY_ANALYSIS;
  const truncated = data.truncated ?? data.data_completeness?.complete === false;
  const completenessMessage = data.data_completeness?.message ?? "统计达到数据读取上限，更早的历史记录未能全部纳入分析；当前周期的数据完整，但上期对比与历史趋势可能不完整。";
  const [focused, setFocused] = useState<Set<string>>(() => new Set(data.annotations?.focused ?? []));
  const [ignored, setIgnored] = useState<Set<string>>(() => new Set(data.annotations?.ignored ?? []));
  const [notes, setNotes] = useState<Record<string, string>>(() => data.annotations?.notes ?? {});
  const [expandedDiagnosis, setExpandedDiagnosis] = useState<string | null>(null);
  const [selectedCourse, setSelectedCourse] = useState("all");
  const [selectedPoint, setSelectedPoint] = useState("all");
  const [aiState, setAIState] = useState<AIState>("not-generated");
  const [aiResult, setAIResult] = useState<TeacherAIAnalysisResult | null>(null);
  const [aiError, setAIError] = useState("");
  const [showInsufficient, setShowInsufficient] = useState(false);
  const [showIgnored, setShowIgnored] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState("");
  const courseOptions = Array.from(new Set(analysis.diagnoses.map((item) => item.content_name)));
  const pointOptions = Array.from(new Set(analysis.diagnoses.map((item) => item.knowledge_point_name)));
  const visibleDiagnoses = analysis.diagnoses.filter((item) => !ignored.has(item.knowledge_point_id) && (selectedCourse === "all" || item.content_name === selectedCourse) && (selectedPoint === "all" || item.knowledge_point_name === selectedPoint));
  const ignoredDiagnoses = analysis.diagnoses.filter((item) => ignored.has(item.knowledge_point_id));
  const scopedAnalysis = scopeAnalysis(analysis, visibleDiagnoses);
  const evidenceDiagnoses = scopedAnalysis.diagnoses.filter((item) => item.data_sufficiency === "sufficient");
  const insufficientDiagnoses = scopedAnalysis.diagnoses.filter((item) => item.data_sufficiency !== "sufficient");
  const expireAI = () => setAIState((current) => current === "not-generated" ? current : "expired");
  const persistAnnotations = async (nextFocused: string[], nextIgnored: string[], nextNotes: Record<string, string>) => {
    setSaveState("saving");
    setSaveError("");
    try {
      await api.updateTeacherAnalysisAnnotations(workspaceId, { focused: nextFocused, ignored: nextIgnored, notes: nextNotes });
      setSaveState("saved");
    } catch (reason) {
      setSaveState("error");
      setSaveError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  const focus = (id: string) => {
    const next = new Set(focused);
    if (next.has(id)) next.delete(id); else next.add(id);
    setFocused(next);
    void persistAnnotations([...next], [...ignored], notes);
  };
  const ignore = (id: string) => {
    const next = new Set(ignored);
    if (next.has(id)) next.delete(id); else next.add(id);
    setIgnored(next);
    void persistAnnotations([...focused], [...next], notes);
  };
  const handleNote = (id: string, value: string) => setNotes((current) => ({ ...current, [id]: value }));
  const commitNotes = () => void persistAnnotations([...focused], [...ignored], notes);
  const retrySave = () => void persistAnnotations([...focused], [...ignored], notes);
  const viewDiagnosis = (id: string) => { setSelectedCourse("all"); setSelectedPoint("all"); setExpandedDiagnosis(id); window.requestAnimationFrame(() => document.getElementById(`teacher-analysis-diagnosis-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" })); };
  const generate = async () => {
    setAIState("loading");
    setAIError("");
    try {
      const result = await api.generateTeacherAIAnalysis(workspaceId, { course_id: selectedCourse, content_scope: selectedPoint, period_days: analysis.scope.period_days, force_refresh: aiResult != null });
      setAIResult(result);
      setAIState(result.status === "completed" ? "completed" : "failed");
    } catch (reason) {
      setAIState("failed");
      setAIError(reason instanceof Error ? reason.message : String(reason));
    }
  };
  return <div className="teacher-analysis-stack">
    <section className="teacher-analysis-hero"><div><h2>基于学生学习表现，定位需要重点关注的教学内容</h2><p>统计结果只作为教学判断证据，不自动生成教案、不修改教学计划。</p></div><BarChart3 size={47} /></section>
    {(truncated || saveState !== "idle") && <div className="teacher-analysis-alerts">{truncated && <div className="teacher-analysis-alert truncation" role="status"><AlertCircle size={16} /><div><strong>分析数据可能不完整</strong><p>{completenessMessage}</p></div></div>}{saveState === "error" && <div className="teacher-analysis-alert error" role="alert"><AlertCircle size={16} /><div><strong>保存失败</strong><p>{saveError || "关注、忽略或备注未能保存，刷新后可能丢失。"}<button type="button" onClick={retrySave}>重试</button></p></div></div>}{saveState === "saving" && <div className="teacher-analysis-save-status" role="status"><RefreshCw size={13} className="spin" />正在保存关注/忽略/备注…</div>}{saveState === "saved" && <div className="teacher-analysis-save-status saved" role="status"><Check size={13} />已保存</div>}</div>}
    <section className="teacher-analysis-filters" aria-label="学习分析筛选条件"><label>教材/课程<select aria-label="教材/课程" value={selectedCourse} onChange={(event) => { setSelectedCourse(event.target.value); expireAI(); }}><option key="all" value="all">全部教材内容</option>{courseOptions.map((option) => <option key={`course-${option}`} value={option}>{option}</option>)}</select></label><label>内容范围<select aria-label="内容范围" value={selectedPoint} onChange={(event) => { setSelectedPoint(event.target.value); expireAI(); }}><option key="all" value="all">全部主题与知识点</option>{pointOptions.map((option) => <option key={`point-${option}`} value={option}>{option}</option>)}</select></label><label>时间范围<select aria-label="时间范围" value={String(analysis.scope.period_days)} onChange={(event) => { expireAI(); onPeriodChange?.(Number(event.target.value)); }}><option key="30" value="30">近 30 天</option><option key="60" value="60">近 60 天</option><option key="90" value="90">近 90 天</option></select></label><div className="teacher-analysis-fixed-role"><span>学生角色</span><strong>学生</strong></div><div className="teacher-analysis-sample"><CircleHelp size={15} /><span>当前分析范围：{analysis.scope.period_label} · {selectedCourse === "all" ? "全部教材内容" : selectedCourse} · 学生角色 · {analysis.scope.student_count} 名学生 · {analysis.scope.attempt_count} 次作答</span></div></section>
    <AIAnalysisPanel state={aiState} result={aiResult} error={aiError} onGenerate={() => void generate()} analysis={scopedAnalysis} ignored={ignored} focused={focused} onFocus={focus} onIgnore={ignore} />
    <section className="teacher-analysis-conclusions" aria-label="内容分析结论"><ConclusionCard label="重点薄弱内容" item={scopedAnalysis.conclusions.weak} tone="weak" onView={viewDiagnosis} /><ConclusionCard label="近期下降内容" item={scopedAnalysis.conclusions.declining} tone="declining" onView={viewDiagnosis} /><ConclusionCard label="掌握较好内容" item={scopedAnalysis.conclusions.good} tone="good" onView={viewDiagnosis} /></section>
    <section className="teacher-analysis-panel teacher-analysis-diagnosis-panel"><header><div><h2>知识点诊断</h2><p>优先展示有练习证据的知识点；数据不足的目录项收纳在下方，不参与判断。</p></div><span className="teacher-analysis-count">{evidenceDiagnoses.length} 个有证据知识点{insufficientDiagnoses.length ? ` · ${insufficientDiagnoses.length} 个数据不足` : ""}</span></header>{evidenceDiagnoses.length ? <><div className="teacher-analysis-diagnosis-head"><span>内容 / 知识点</span><span>掌握情况</span><span>变化趋势</span><span>问题类型 / 样本</span><span>操作</span></div><div className="teacher-analysis-diagnosis-list">{evidenceDiagnoses.map((item) => <div id={`teacher-analysis-diagnosis-${item.knowledge_point_id}`} key={item.knowledge_point_id}><DiagnosisDetail item={item} focused={focused.has(item.knowledge_point_id)} expanded={expandedDiagnosis === item.knowledge_point_id} onToggle={() => setExpandedDiagnosis((current) => current === item.knowledge_point_id ? null : item.knowledge_point_id)} onFocus={() => focus(item.knowledge_point_id)} onIgnore={() => ignore(item.knowledge_point_id)} note={notes[item.knowledge_point_id] ?? ""} onNote={(value) => handleNote(item.knowledge_point_id, value)} onNoteCommit={commitNotes} /></div>)}</div></> : <div className="teacher-analysis-empty"><AlertCircle size={18} />暂无足够的学生练习证据生成诊断。</div>}{insufficientDiagnoses.length > 0 && <div className="teacher-analysis-insufficient"><button type="button" aria-expanded={showInsufficient} onClick={() => setShowInsufficient((value) => !value)}>{showInsufficient ? "收起" : "展开"} {insufficientDiagnoses.length} 个数据不足知识点<ChevronDown size={14} /></button>{showInsufficient && <div className="teacher-analysis-insufficient-list">{insufficientDiagnoses.map((item) => <div className="teacher-analysis-insufficient-item" key={item.knowledge_point_id}><div><small>{item.content_name}</small><strong>{item.knowledge_point_name}</strong></div><span>暂无练习证据 · 补充作答后再判断</span></div>)}</div>}</div>}{ignoredDiagnoses.length > 0 && <div className="teacher-analysis-ignored"><button type="button" aria-expanded={showIgnored} onClick={() => setShowIgnored((value) => !value)}>{showIgnored ? "收起" : "展开"} {ignoredDiagnoses.length} 个已忽略知识点<ChevronDown size={14} /></button>{showIgnored && <div className="teacher-analysis-ignored-list">{ignoredDiagnoses.map((item) => <div className="teacher-analysis-ignored-item" key={item.knowledge_point_id}><div><small>{item.content_name}</small><strong>{item.knowledge_point_name}</strong></div><button type="button" onClick={() => ignore(item.knowledge_point_id)}><Undo2 size={13} />恢复</button></div>)}</div>}</div>}</section>
    <section className="teacher-analysis-chart-grid"><article className="teacher-analysis-panel"><header><div><h2>内容掌握趋势</h2><p>默认展示最需要关注的 5 个知识点，悬浮查看每月掌握率。</p></div></header><MasteryTrendChart analysis={scopedAnalysis} /></article><article className="teacher-analysis-panel"><header><div><h2>内容问题分布</h2><p>按问题类型比较诊断数量，数据不足不会被强行归类。</p></div></header><ProblemDistributionChart items={scopedAnalysis.problem_distribution} /></article></section>
    <section className="teacher-analysis-note"><CircleHelp size={16} /><span>本页只统计 RBAC 角色为“学生”的学习记录；判断权留给教师，页面不会自动调整课程或发布练习。</span></section>
  </div>;
}
