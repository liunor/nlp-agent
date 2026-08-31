import { useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  BarChart3,
  BookOpen,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  FileQuestion,
  Gauge,
  Layers3,
  MessageCircleQuestion,
  TrendingUp,
  Users,
} from "lucide-react";

import type {
  TeacherDistribution,
  TeacherMonthlyQuestionStatistics,
  TeacherOverview,
  TeacherStudentActivity,
} from "@/shared/types";

const MONTH_COLORS = ["#7868d6", "#4d8fda", "#41a67a", "#e39a4f", "#d46891"];
const WEEKDAY_COLORS = ["#7868d6", "#4d8fda", "#41a67a", "#e39a4f", "#d46891", "#8b72b7", "#8b96a8"];

const formatNumber = (value: number) => Number.isInteger(value) ? String(value) : value.toFixed(2);
const formatPercent = (value: number) => `${formatNumber(value)}%`;
const today = () => {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  return date;
};
const isoDate = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const dateBefore = (daysAgo: number) => {
  const date = today();
  date.setDate(date.getDate() - daysAgo);
  return isoDate(date);
};
const monthBefore = (monthsAgo: number) => {
  const date = today();
  date.setDate(1);
  date.setMonth(date.getMonth() - monthsAgo);
  return date;
};
const daysInMonth = (date: Date) => new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
const monthKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
const monthLabel = (date: Date) => `${date.getFullYear()}年${String(date.getMonth() + 1).padStart(2, "0")}月`;
const distribution = (entries: Array<[string, number]>): TeacherDistribution[] => {
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  return entries.map(([name, count]) => ({ name, count, percentage: total ? Number((count / total * 100).toFixed(2)) : 0 }));
};

const demoDailyCounts = [3, 5, 4, 7, 9, 6, 11, 10, 8, 14, 11, 10, 8, 15, 13, 12, 9, 7, 11, 14, 16, 13, 12, 10, 8, 12, 11, 10, 15, 13];
const demoHourlyCounts = [4, 2, 1, 0, 0, 1, 3, 6, 11, 18, 21, 16, 13, 17, 21, 24, 28, 30, 26, 20, 16, 12, 9, 8];
const demoWeekdayCounts = [42, 48, 51, 46, 55, 38, 27];
const demoStudentQuestions = [24, 28, 26, 24, 22, 21, 20, 19, 18, 17, 16, 15, 13, 12, 11, 10, 8, 3];
const demoTopics = ["Transformer 与注意力机制", "词向量与表示学习", "文本分类", "序列标注", "机器翻译", "预训练模型", "未选择主题"];

function createDemoMonthlyStatistics(): TeacherMonthlyQuestionStatistics[] {
  return Array.from({ length: 5 }, (_, index) => {
    const date = monthBefore(4 - index);
    const days = daysInMonth(date);
    const visibleDays = index === 4 ? Math.min(days, today().getDate()) : days;
    const scale = 0.66 + index * 0.11;
    const daily_questions = Array.from({ length: visibleDays }, (_, dayIndex) => ({
      day: dayIndex + 1,
      date: isoDate(new Date(date.getFullYear(), date.getMonth(), dayIndex + 1)),
      count: Math.max(0, Math.round((demoDailyCounts[(dayIndex + index * 2) % demoDailyCounts.length] + index * 2) * scale)),
    }));
    const hourly_questions = demoHourlyCounts.map((count, hour) => ({
      hour,
      label: `${String(hour).padStart(2, "0")}:00`,
      count: Math.max(0, Math.round(count * scale)),
      percentage: 0,
    }));
    const question_count = daily_questions.reduce((sum, item) => sum + item.count, 0);
    return {
      month: monthKey(date),
      label: monthLabel(date),
      question_count,
      topic_distribution: distribution(demoTopics.map((topic, topicIndex) => [topic, Math.max(1, Math.round((74 - topicIndex * 7 + index * (topicIndex + 2)) * scale))])),
      difficulty_distribution: distribution([["入门", Math.round(80 * scale)], ["进阶", Math.round((143 + index * 8) * scale)], ["深入", Math.round((84 + index * 4) * scale)]]),
      mode_distribution: distribution([["讲解", Math.round((136 + index * 6) * scale)], ["练习", Math.round((78 + index * 4) * scale)], ["引导", Math.round((58 + index * 3) * scale)], ["复习", Math.round((35 + index * 2) * scale)]]),
      daily_questions,
      hourly_questions,
    };
  });
}

function createLocalDemoData(base: TeacherOverview): TeacherOverview {
  const questions = demoDailyCounts.reduce((sum, count) => sum + count, 0);
  const daily_questions = demoDailyCounts.map((count, index) => ({ date: dateBefore(demoDailyCounts.length - index - 1), count }));
  const hourly_questions = demoHourlyCounts.map((count, hour) => ({ hour, label: `${String(hour).padStart(2, "0")}:00`, count, percentage: Number((count / questions * 100).toFixed(2)) }));
  const weekday_questions = demoWeekdayCounts.map((count, weekday) => ({ weekday, label: ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][weekday], count, percentage: Number((count / questions * 100).toFixed(2)) }));
  const student_activity: TeacherStudentActivity[] = demoStudentQuestions.map((count, index) => {
    const sessions = Math.max(1, Math.round(count / (2.5 + index % 3 * 0.4)));
    const error_questions = index % 5 === 0 ? 2 : index % 4 === 0 ? 1 : 0;
    return {
      user_id: `demo-student-${index + 1}`,
      display_name: ["张三", "李四", "王五", "赵六", "陈晨", "杨帆", "周宁", "吴越", "郑好", "孙悦", "何苗", "林溪", "黄河", "徐安", "胡可", "高远", "罗琳", "谢意"][index],
      username: `student${String(index + 1).padStart(2, "0")}`,
      questions: count,
      sessions,
      active_days: 4 + (index * 3) % 18,
      error_questions,
      error_rate: Number((error_questions / count * 100).toFixed(2)),
      questions_per_session: Number((count / sessions).toFixed(2)),
      last_active: dateBefore(index % 6),
      top_topic: demoTopics[index % demoTopics.length],
    };
  });
  const peakDay = daily_questions.reduce((peak, item) => item.count > peak.count ? item : peak, daily_questions[0]);
  const peakHour = hourly_questions.reduce((peak, item) => item.count > peak.count ? item : peak, hourly_questions[0]);

  return {
    ...base,
    period_days: 30,
    summary: {
      ...base.summary,
      questions,
      sessions: 86,
      students: student_activity.length,
      active_days: 24,
      error_questions: 12,
      error_rate: 3.91,
      questions_per_student: Number((questions / student_activity.length).toFixed(2)),
      questions_per_session: Number((questions / 86).toFixed(2)),
      contextualized_questions: 286,
      context_coverage_rate: 93.16,
      exercises: 74,
      exercise_pass_rate: 71.62,
      guided_sessions: 39,
    },
    topic_distribution: distribution([["Transformer 与注意力机制", 74], ["词向量与表示学习", 57], ["文本分类", 46], ["序列标注", 40], ["机器翻译", 36], ["预训练模型", 29], ["未选择主题", 25]]),
    difficulty_distribution: distribution([["入门", 80], ["进阶", 143], ["深入", 84]]),
    mode_distribution: distribution([["讲解", 136], ["练习", 78], ["引导", 58], ["复习", 35]]),
    daily_questions,
    hourly_questions,
    weekday_questions,
    peak_day: peakDay,
    peak_hour: peakHour,
    monthly_statistics: createDemoMonthlyStatistics(),
    student_activity,
  };
}

function MetricCard({ icon: Icon, label, value, detail, tone = "purple" }: { icon: typeof Activity; label: string; value: string; detail: string; tone?: "purple" | "blue" | "green" | "orange" }) {
  return <article className={`teacher-question-metric ${tone}`}><div className="teacher-question-metric-icon"><Icon size={17} /></div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>;
}

function MonthTabs({ months, activeMonth, onChange }: { months: TeacherMonthlyQuestionStatistics[]; activeMonth: string; onChange: (month: string) => void }) {
  return <section className="teacher-question-month-overview"><div><h2>月度统计 · 近 5 个月</h2><p>选择月份查看主题、难度和模式分布；问题量与小时趋势会同时对比这 5 个月。</p></div><div className="teacher-question-month-tabs" role="tablist" aria-label="统计月份">{months.map((item) => <button key={item.month} type="button" role="tab" aria-selected={item.month === activeMonth} className={item.month === activeMonth ? "active" : ""} onClick={() => onChange(item.month)}><strong>{item.label}</strong><small>{item.question_count} 个问题</small></button>)}</div></section>;
}

function DistributionPanel({ title, description, items, tone = "purple" }: { title: string; description: string; items: TeacherDistribution[]; tone?: "purple" | "blue" | "green" }) {
  const visibleItems = items.slice(0, 5);
  const rows = Array.from({ length: 5 }, (_, index) => visibleItems[index] ?? null);
  return <section className="teacher-panel teacher-question-panel teacher-question-distribution-panel">
    <header><div><h2>{title}</h2><p>{description}</p></div><BarChart3 size={17} /></header>
    {items.length ? <>
      <div className="teacher-question-distribution">{rows.map((item, index) => item ? <article key={item.name}><div className="teacher-question-distribution-name" title={item.name}><strong>{item.name}</strong></div><span>{item.count} 个 · {formatPercent(item.percentage)}</span><i><b className={tone} style={{ width: `${Math.min(100, item.percentage)}%` }} /></i></article> : <article className="is-empty" key={`empty-${index}`}><div className="teacher-question-distribution-name"><strong>暂无第 {index + 1} 类</strong></div><span>—</span><i><b className={tone} style={{ width: "0%" }} /></i></article>)}</div>
      <footer className="teacher-question-distribution-footer"><span>显示前 5 类</span><span>共 {items.length} 类</span></footer>
    </> : <p className="teacher-empty-inline">暂无分布数据</p>}
  </section>;
}

type LineSeries = { label: string; color: string; values: Array<number | null> };

function buildLinePath(values: Array<number | null>, x: (index: number) => number, y: (value: number) => number) {
  let path = "";
  let connected = false;
  values.forEach((value, index) => {
    if (value == null) {
      connected = false;
      return;
    }
    path += `${connected ? "L" : "M"}${x(index).toFixed(2)},${y(value).toFixed(2)} `;
    connected = true;
  });
  return path.trim();
}

function chartAxisMax(rawMax: number) {
  if (rawMax <= 0) return 1;
  const step = rawMax < 10 ? 1 : rawMax < 100 ? 5 : 10 ** Math.max(0, Math.floor(Math.log10(rawMax)) - 1);
  const rounded = Math.ceil(rawMax / step) * step;
  return rounded === rawMax ? rounded + step : rounded;
}

function LineChart({ title, description, ariaLabel, labels, series, tooltipLabel, xLabelStep }: { title: string; description: string; ariaLabel: string; labels: string[]; series: LineSeries[]; tooltipLabel?: (label: string) => string; xLabelStep?: number }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const width = 1280;
  const values = series.flatMap((item) => item.values.filter((value): value is number => value != null));
  const rawMax = Math.max(0, ...values);
  const axisMax = chartAxisMax(rawMax);
  const chartHeight = Math.min(520, Math.max(340, 280 + Math.ceil(Math.log10(axisMax + 1)) * 42));
  const padding = { top: 22, right: 24, bottom: 38, left: 50 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = chartHeight - padding.top - padding.bottom;
  const x = (index: number) => labels.length <= 1 ? padding.left + plotWidth / 2 : padding.left + index / (labels.length - 1) * plotWidth;
  const y = (value: number) => padding.top + plotHeight - value / axisMax * plotHeight;
  const labelStep = xLabelStep ?? (labels.length > 20 ? 5 : labels.length > 10 ? 2 : 1);
  const yTicks = Array.from({ length: 5 }, (_, index) => axisMax * index / 4);
  const hoverWidth = labels.length > 1 ? plotWidth / (labels.length - 1) : plotWidth;
  const tooltipWidth = 220;
  const tooltipHeight = 50 + series.length * 17;
  const tooltipLeft = hoverIndex === null ? 0 : Math.min(Math.max(padding.left, x(hoverIndex) + 14), width - padding.right - tooltipWidth);
  const tooltipTop = padding.top + 8;
  return <section className="teacher-panel teacher-question-panel teacher-question-line-panel">
    <header><div><h2>{title}</h2><p>{description}</p></div><TrendingUp size={17} /></header>
    <div className="teacher-question-line-legend">{series.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}</div>
    <div className="teacher-question-line-chart-scroll"><svg className="teacher-question-line-chart" data-chart-height={chartHeight} data-label-step={labelStep} data-raw-max={rawMax} data-axis-max={axisMax} style={{ height: chartHeight }} viewBox={`0 0 ${width} ${chartHeight}`} role="img" aria-label={ariaLabel}>
      {yTicks.map((tick) => <g key={tick}><line className="teacher-question-line-grid" x1={padding.left} x2={width - padding.right} y1={y(tick)} y2={y(tick)} /><text className="teacher-question-line-y-label" x={padding.left - 10} y={y(tick) + 3} textAnchor="end">{Math.round(tick)}</text></g>)}
      {series.map((item, index) => <path key={item.label} className={`teacher-question-line-series${index === series.length - 1 ? " current" : ""}`} d={buildLinePath(item.values, x, y)} stroke={item.color} />)}
      {hoverIndex !== null && <>
        <line className="teacher-question-line-hover-line" x1={x(hoverIndex)} x2={x(hoverIndex)} y1={padding.top} y2={padding.top + plotHeight} />
        {series.map((item) => item.values[hoverIndex] == null ? null : <circle key={`hover-${item.label}`} className="teacher-question-line-point" cx={x(hoverIndex)} cy={y(item.values[hoverIndex] as number)} fill={item.color} r="4" />)}
        <g className="teacher-question-line-tooltip" transform={`translate(${tooltipLeft},${tooltipTop})`} role="tooltip" aria-label={`${tooltipLabel?.(labels[hoverIndex]) ?? labels[hoverIndex]}数据明细`}>
          <rect width={tooltipWidth} height={tooltipHeight} rx="10" />
          <text className="teacher-question-line-tooltip-title" x="14" y="22">{tooltipLabel?.(labels[hoverIndex]) ?? labels[hoverIndex]}</text>
          {series.map((item, index) => <text key={item.label} className="teacher-question-line-tooltip-value" x="14" y={43 + index * 17}><tspan fill={item.color}>●</tspan><tspan dx="5">{item.label}</tspan><tspan dx="8" fontWeight="700">{item.values[hoverIndex] == null ? "—" : `${item.values[hoverIndex]} 个`}</tspan></text>)}
        </g>
      </>}
      {labels.map((label, index) => index % labelStep === 0 ? <text key={label} className="teacher-question-line-x-label" x={x(index)} y={chartHeight - 12} textAnchor="middle">{label}</text> : null)}
      {labels.map((label, index) => {
        const start = index === 0 ? padding.left : x(index) - hoverWidth / 2;
        const end = index === labels.length - 1 ? width - padding.right : x(index) + hoverWidth / 2;
        return <rect key={`hover-target-${label}`} className="teacher-question-line-hover-target" x={start} y={padding.top} width={end - start} height={plotHeight} tabIndex={0} aria-label={`查看 ${label} 数据`} onMouseEnter={() => setHoverIndex(index)} onMouseLeave={() => setHoverIndex(null)} onFocus={() => setHoverIndex(index)} onBlur={() => setHoverIndex(null)} />;
      })}
    </svg></div>
  </section>;
}

function MonthlyDailyQuestions({ months }: { months: TeacherMonthlyQuestionStatistics[] }) {
  const labels = Array.from({ length: Math.max(1, ...months.map((item) => item.daily_questions.length)) }, (_, index) => String(index + 1));
  const series = months.map((month, index) => ({
    label: month.label,
    color: MONTH_COLORS[index % MONTH_COLORS.length],
    values: labels.map((_, dayIndex) => month.daily_questions.find((item) => item.day === dayIndex + 1)?.count ?? null),
  }));
  return <LineChart title="问题量趋势" description="按每月日期叠加对比前 5 个月；每 5 天显示一个刻度，悬浮查看当天明细" ariaLabel="问题量趋势折线图" labels={labels} series={series} tooltipLabel={(label) => `第 ${label} 天`} xLabelStep={5} />;
}

function MonthlyHourlyQuestions({ months }: { months: TeacherMonthlyQuestionStatistics[] }) {
  const labels = Array.from({ length: 24 }, (_, index) => `${String(index).padStart(2, "0")}时`);
  const series = months.map((month, index) => ({
    label: month.label,
    color: MONTH_COLORS[index % MONTH_COLORS.length],
    values: labels.map((_, hour) => month.hourly_questions.find((item) => item.hour === hour)?.count ?? 0),
  }));
  return <LineChart title="小时分布" description="按 00–23 时叠加显示前 5 个月；每 2 小时显示一个刻度，悬浮查看时段明细" ariaLabel="小时提问趋势折线图" labels={labels} series={series} xLabelStep={2} />;
}

function polarPoint(cx: number, cy: number, radius: number, angle: number) {
  const radians = (angle - 90) * Math.PI / 180;
  return { x: cx + radius * Math.cos(radians), y: cy + radius * Math.sin(radians) };
}

function pieSlicePath(cx: number, cy: number, radius: number, startAngle: number, endAngle: number) {
  const start = polarPoint(cx, cy, radius, startAngle);
  const end = polarPoint(cx, cy, radius, endAngle);
  const largeArc = endAngle - startAngle > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${start.x.toFixed(2)} ${start.y.toFixed(2)} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)} Z`;
}

function WeekdayPie({ items }: { items: TeacherOverview["weekday_questions"] }) {
  const total = items.reduce((sum, item) => sum + item.count, 0);
  const cx = 165;
  const cy = 155;
  const radius = 100;
  const segments = items.map((item, index) => {
    const startAngle = items.slice(0, index).reduce((sum, previous) => sum + (total ? previous.count / total * 360 : 0), 0);
    const endAngle = startAngle + (total ? item.count / total * 360 : 0);
    const middleAngle = startAngle + (endAngle - startAngle) / 2;
    const lineStart = polarPoint(cx, cy, radius + 4, middleAngle);
    const lineEnd = polarPoint(cx, cy, radius + 22, middleAngle);
    const right = Math.cos((middleAngle - 90) * Math.PI / 180) >= 0;
    const elbowX = right ? 310 : 20;
    const textX = right ? 320 : 10;
    return { item, index, startAngle, endAngle, lineStart, lineEnd, elbowX, textX, right };
  });
  return <svg className="teacher-question-pie-chart" viewBox="0 0 640 310" role="img" aria-label="星期问题分布饼图">
    <circle cx={cx} cy={cy} r={radius} fill="#edf0f5" />
    {segments.map(({ item, index, startAngle, endAngle }) => <path key={`slice-${item.weekday}`} className="teacher-question-pie-slice" d={pieSlicePath(cx, cy, radius, startAngle, endAngle)} fill={WEEKDAY_COLORS[index % WEEKDAY_COLORS.length]} />)}
    {segments.map(({ item, lineStart, lineEnd, elbowX, textX, right }) => <g key={`callout-${item.weekday}`} className="teacher-question-pie-callout">
      <path className="teacher-question-pie-callout-line" d={`M ${lineStart.x.toFixed(2)} ${lineStart.y.toFixed(2)} L ${lineEnd.x.toFixed(2)} ${lineEnd.y.toFixed(2)} L ${elbowX} ${lineEnd.y.toFixed(2)}`} />
      <text className="teacher-question-pie-label" x={textX} y={lineEnd.y - 3} textAnchor={right ? "start" : "end"}><tspan>{item.label}</tspan><tspan x={textX} dy="14">{item.count} 个 · {formatPercent(item.percentage)}</tspan></text>
    </g>)}
    {!total && <text className="teacher-question-pie-empty" x={cx} y={cy + 4} textAnchor="middle">暂无数据</text>}
  </svg>;
}

function WeekdayQuestions({ data }: { data: TeacherOverview }) {
  return <section className="teacher-panel teacher-question-panel teacher-question-weekday-panel">
    <header><div><h2>星期分布</h2><p>当前统计周期内一周各天的提问占比，不切换月份</p></div><CalendarDays size={17} /></header>
    <div className="teacher-question-pie-layout"><WeekdayPie items={data.weekday_questions} /></div>
  </section>;
}

function StudentActivity({ items }: { items: TeacherStudentActivity[] }) {
  const pageSize = 8;
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const activePage = Math.min(page, pageCount - 1);
  const pageItems = items.slice(activePage * pageSize, (activePage + 1) * pageSize);
  const first = items.length ? activePage * pageSize + 1 : 0;
  const last = Math.min((activePage + 1) * pageSize, items.length);
  return <section className="teacher-panel teacher-question-panel teacher-question-activity-panel">
    <header><div><h2>学生参与度</h2><p>仅统计 RBAC=学生的账号，分页查看问题量、会话和异常情况</p></div><Users size={17} /></header>
    {items.length ? <>
      <div className="teacher-question-activity-table"><table><thead><tr><th>学生</th><th>问题量</th><th>会话</th><th>活跃天数</th><th>异常问题</th><th>最近活跃</th><th>主要主题</th></tr></thead><tbody>{pageItems.map((item) => <tr key={item.user_id}>
        <td><strong>{item.display_name}</strong>{item.username && <small>@{item.username}</small>}</td>
        <td><strong>{item.questions}</strong><small>每会话 {formatNumber(item.questions_per_session)}</small></td>
        <td>{item.sessions}</td><td>{item.active_days}</td>
        <td className={item.error_questions ? "is-warning" : ""}><strong>{item.error_questions}</strong><small>{formatPercent(item.error_rate)}</small></td>
        <td>{item.last_active ?? "—"}</td><td>{item.top_topic}</td>
      </tr>)}</tbody></table></div>
      <footer className="teacher-question-pagination"><span>显示 {first}–{last} / 共 {items.length} 名学生</span><div><button type="button" aria-label="上一页" disabled={activePage === 0} onClick={() => setPage(Math.max(0, activePage - 1))}><ChevronLeft size={15} /></button><strong>{activePage + 1} / {pageCount}</strong><button type="button" aria-label="下一页" disabled={activePage >= pageCount - 1} onClick={() => setPage(Math.min(pageCount - 1, activePage + 1))}><ChevronRight size={15} /></button></div></footer>
    </> : <p className="teacher-empty-state">暂无学生问题记录。</p>}
  </section>;
}

export function StudentQuestionsPage({ data }: { data: TeacherOverview }) {
  const demo = import.meta.env.DEV && new URLSearchParams(window.location.search).get("demo") === "1";
  const displayData = useMemo(() => demo ? createLocalDemoData(data) : data, [data, demo]);
  const monthlyStatistics = useMemo(() => {
    if (displayData.monthly_statistics?.length) return displayData.monthly_statistics;
    return [{
      month: "current",
      label: "当前月份",
      question_count: displayData.summary.questions,
      topic_distribution: displayData.topic_distribution,
      difficulty_distribution: displayData.difficulty_distribution,
      mode_distribution: displayData.mode_distribution,
      daily_questions: displayData.daily_questions.map((item, index) => ({ day: index + 1, ...item })),
      hourly_questions: displayData.hourly_questions,
    }];
  }, [displayData]);
  const [selectedMonth, setSelectedMonth] = useState("");
  const activeMonth = monthlyStatistics.some((item) => item.month === selectedMonth) ? selectedMonth : monthlyStatistics.at(-1)?.month ?? "";
  const selectedMonthData = monthlyStatistics.find((item) => item.month === activeMonth) ?? monthlyStatistics.at(-1);
  const { summary } = displayData;
  return <div className="teacher-stack teacher-question-stack">
    <section className="teacher-page-summary teacher-question-hero"><div><h2>学生问题全景</h2><p>只统计 RBAC=学生的账号，从问题量、学习上下文、时间分布和参与度判断班级学习信号。</p>{demo && <span className="teacher-question-demo-badge">本地演示数据 · 仅用于布局验收</span>}</div><MessageCircleQuestion size={46} /></section>

    <section className="teacher-question-metrics" aria-label="问题量概览">
      <MetricCard icon={MessageCircleQuestion} label="问题总数" value={formatNumber(summary.questions)} detail={`近 ${displayData.period_days} 天`} />
      <MetricCard icon={Users} label="提问学生" value={formatNumber(summary.students)} detail="仅统计 RBAC=学生账号" tone="blue" />
      <MetricCard icon={Layers3} label="活跃会话" value={formatNumber(summary.sessions)} detail={`平均每会话 ${formatNumber(summary.questions_per_session)} 个问题`} tone="blue" />
      <MetricCard icon={Gauge} label="人均问题" value={formatNumber(summary.questions_per_student)} detail="按有提问记录的学生计算" tone="green" />
      <MetricCard icon={CalendarDays} label="活跃天数" value={formatNumber(summary.active_days)} detail={`占统计周期 ${formatPercent(displayData.period_days ? summary.active_days / displayData.period_days * 100 : 0)}`} tone="green" />
      <MetricCard icon={BookOpen} label="上下文完整度" value={formatPercent(summary.context_coverage_rate)} detail={`${summary.contextualized_questions} 个问题已关联主题`} tone="purple" />
      <MetricCard icon={AlertCircle} label="异常问题" value={formatNumber(summary.error_questions)} detail={`异常率 ${formatPercent(summary.error_rate)}`} tone="orange" />
      <MetricCard icon={CheckCircle2} label="练习完成" value={formatNumber(summary.exercises)} detail={`通过率 ${summary.exercises ? formatPercent(summary.exercise_pass_rate) : "—"}`} tone="green" />
    </section>

    <section className="teacher-question-signal-strip"><div><Activity size={16} /><span>问题高峰</span><strong>{displayData.peak_day ? `${displayData.peak_day.date} · ${displayData.peak_day.count} 个` : "暂无"}</strong></div><div><Clock3 size={16} /><span>高峰时段</span><strong>{displayData.peak_hour ? `${displayData.peak_hour.label} · ${displayData.peak_hour.count} 个` : "暂无"}</strong></div><div><FileQuestion size={16} /><span>问题类型</span><strong>{displayData.mode_distribution[0] ? `${displayData.mode_distribution[0].name}占比 ${formatPercent(displayData.mode_distribution[0].percentage)}` : "暂无"}</strong></div><div><BookOpen size={16} /><span>最热主题</span><strong>{displayData.topic_distribution[0] ? `${displayData.topic_distribution[0].name} · ${displayData.topic_distribution[0].count} 个` : "暂无"}</strong></div></section>

    <MonthTabs months={monthlyStatistics} activeMonth={activeMonth} onChange={setSelectedMonth} />
    <div className="teacher-question-grid teacher-question-grid-three"><DistributionPanel title="主题分布" description={`${selectedMonthData?.label ?? "当前月份"} · 固定显示前 5 类`} items={selectedMonthData?.topic_distribution ?? []} /><DistributionPanel title="难度分布" description={`${selectedMonthData?.label ?? "当前月份"} · 固定显示前 5 类`} items={selectedMonthData?.difficulty_distribution ?? []} tone="blue" /><DistributionPanel title="模式分布" description={`${selectedMonthData?.label ?? "当前月份"} · 固定显示前 5 类`} items={selectedMonthData?.mode_distribution ?? []} tone="green" /></div>
    <div className="teacher-question-grid teacher-question-grid-trend"><MonthlyDailyQuestions months={monthlyStatistics} /><MonthlyHourlyQuestions months={monthlyStatistics} /></div>
    <div className="teacher-question-grid teacher-question-grid-weekday"><WeekdayQuestions data={displayData} /></div>
    <StudentActivity items={displayData.student_activity} />
  </div>;
}
