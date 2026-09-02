import { CircleAlert, Coins, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useOptionalAuth } from "@/platform/auth/AuthContext";
import { api, ApiError } from "@/platform/http/api";
import { StudentSocket } from "@/platform/realtime/client";
import type { QuotaBucketSnapshot, QuotaSnapshot, QuotaUsageBreakdown, QuotaUsageSnapshot } from "@/shared/types";

export const formatMicro = (value: number | null | undefined) => `${Number(value ?? 0).toLocaleString("zh-CN")} μcredits`;
const formatLimit = (value: number | null) => value == null ? "无限" : formatMicro(value);
const ownerLabel = (ownerType: QuotaBucketSnapshot["owner_type"]) => ownerType === "workspace" ? "工作空间" : ownerType === "classroom" ? "课堂" : "个人";
const periodLabel = (bucketType: QuotaBucketSnapshot["bucket_type"]) => bucketType === "daily" ? "今日" : "本周";
const ACTIVITY_DAY_COUNT = 182;
const ACTIVITY_WEEK_COUNT = 26;
const formatTokenCount = (value: number) => {
  if (value >= 100_000_000) return `${(value / 100_000_000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}亿`;
  if (value >= 10_000) return `${(value / 10_000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}万`;
  return value.toLocaleString("zh-CN");
};

const formatActivityDate = (day: string) => {
  const [, month, date] = day.split("-").map(Number);
  return `${month}月${date}日`;
};

function parseActivityDate(value: string) {
  const [year, month, date] = value.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date));
}

function activityDayKey(value: Date) {
  return `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, "0")}-${String(value.getUTCDate()).padStart(2, "0")}`;
}

function mondayOf(value: Date) {
  const date = new Date(value);
  date.setUTCDate(date.getUTCDate() - date.getUTCDay() + (date.getUTCDay() === 0 ? -6 : 1));
  return date;
}

function sundayOf(value: Date) {
  const date = new Date(value);
  date.setUTCDate(date.getUTCDate() + (date.getUTCDay() === 0 ? 0 : 7 - date.getUTCDay()));
  return date;
}

function BucketSummary({ bucket }: { bucket: QuotaBucketSnapshot }) {
  const capacity = bucket.limit_micro == null ? null : Math.max(0, bucket.limit_micro + bucket.grant_micro + bucket.adjustment_micro);
  const used = bucket.consumed_micro + bucket.reserved_micro;
  const progress = capacity == null || capacity === 0 ? 0 : Math.min(100, Math.max(0, used / capacity * 100));
  return <article className={`quota-balance-row${bucket.over_limit ? " is-over-limit" : ""}`}>
    <div className="quota-balance-row-heading"><span>{ownerLabel(bucket.owner_type)} · {periodLabel(bucket.bucket_type)}</span><strong>{bucket.limit_micro == null ? "无限" : formatMicro(bucket.remaining_micro)}</strong></div>
    <div className="quota-progress" aria-label={`${ownerLabel(bucket.owner_type)} ${periodLabel(bucket.bucket_type)}使用进度`}><i style={{ width: `${progress}%` }} /></div>
    <div className="quota-balance-row-meta"><span>{capacity == null ? "无限额度" : `已用 ${formatMicro(used)} / ${formatLimit(capacity)}`}</span>{bucket.over_limit && <em>已超额</em>}</div>
  </article>;
}

function ActivityHeatmap({ breakdown, dailyBreakdown, granularity, onGranularityChange }: { breakdown: QuotaUsageBreakdown[]; dailyBreakdown: QuotaUsageBreakdown[]; granularity: "day" | "week"; onGranularityChange: (value: "day" | "week") => void }) {
  const tokensByPeriod = useMemo(() => {
    const values = new Map<string, number>();
    breakdown.forEach((item) => {
      const period = (item.period_start ?? item.day).slice(0, 10);
      values.set(period, (values.get(period) ?? 0) + item.total_tokens);
    });
    return values;
  }, [breakdown]);
  const dailyTokensByDay = useMemo(() => {
    const values = new Map<string, number>();
    dailyBreakdown.forEach((item) => {
      const day = (item.period_start ?? item.day).slice(0, 10);
      values.set(day, (values.get(day) ?? 0) + item.total_tokens);
    });
    return values;
  }, [dailyBreakdown]);
  const endPeriod = useMemo(() => {
    const latest = breakdown.reduce((current, item) => {
      const period = (item.period_start ?? item.day).slice(0, 10);
      return period > current ? period : current;
    }, "");
    const today = new Date();
    const fallback = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()));
    return granularity === "week"
      ? mondayOf(latest ? parseActivityDate(latest) : fallback)
      : sundayOf(latest ? parseActivityDate(latest) : fallback);
  }, [breakdown, granularity]);
  const cells = useMemo(() => Array.from({ length: granularity === "day" ? ACTIVITY_DAY_COUNT : ACTIVITY_WEEK_COUNT }, (_, index) => {
    const date = new Date(endPeriod);
    if (granularity === "week") {
      date.setUTCDate(endPeriod.getUTCDate() - (ACTIVITY_WEEK_COUNT - 1 - index) * 7);
    } else {
      date.setUTCDate(endPeriod.getUTCDate() - (ACTIVITY_DAY_COUNT - 1 - index));
    }
    const day = activityDayKey(date);
    if (granularity === "week") {
      const days = Array.from({ length: 7 }, (_, dayIndex) => {
        const weekDay = new Date(date);
        weekDay.setUTCDate(date.getUTCDate() + dayIndex);
        const weekDayKey = activityDayKey(weekDay);
        return { day: weekDayKey, tokens: dailyTokensByDay.get(weekDayKey) ?? 0 };
      });
      return { day, tokens: tokensByPeriod.get(day) ?? days.reduce((total, item) => total + item.tokens, 0), days };
    }
    return { day, tokens: tokensByPeriod.get(day) ?? 0 };
  }), [dailyTokensByDay, endPeriod, granularity, tokensByPeriod]);
  const maxTokens = granularity === "week"
    ? Math.max(1, ...(dailyTokensByDay.size > 0 ? [...dailyTokensByDay.values()] : cells.map((item) => item.tokens)))
    : Math.max(1, ...cells.map((item) => item.tokens));
  const monthLabels = useMemo(() => {
    const labels: Array<{ key: string; column: number; label: string }> = [];
    const columns = granularity === "day"
      ? cells.filter((_item, index) => index % 7 === 0)
      : cells;
    columns.forEach((item, column) => {
      const date = parseActivityDate(item.day);
      const key = `${date.getUTCFullYear()}-${date.getUTCMonth()}`;
      if (labels.at(-1)?.key !== key) {
        labels.push({ key, column, label: `${date.getUTCMonth() + 1}月` });
      }
    });
    return labels;
  }, [cells, granularity]);
  return <section className="quota-activity-panel" aria-label="Token 活动">
    <div className="quota-activity-heading"><h2>Token 活动</h2><div className="quota-activity-heading-actions"><div className="quota-activity-tabs" role="group" aria-label="Token 活动粒度"><button type="button" aria-pressed={granularity === "day"} onClick={() => onGranularityChange("day")}>日</button><button type="button" aria-pressed={granularity === "week"} onClick={() => onGranularityChange("week")}>周</button></div><span>{granularity === "day" ? "近 6 个月" : "近 26 周"}</span></div></div>
    <div className={`quota-activity-grid is-${granularity}`} role="grid" aria-label={`按${granularity === "day" ? "日" : "周"}查看 Token 活动`}>{cells.map((item) => {
      const start = parseActivityDate(item.day);
      const end = new Date(start);
      end.setUTCDate(end.getUTCDate() + (granularity === "week" ? 6 : 0));
      const range = granularity === "week" ? `${formatActivityDate(item.day)}–${formatActivityDate(activityDayKey(end))}` : formatActivityDate(item.day);
      const tooltip = `${range} 使用了 ${formatTokenCount(item.tokens)} 个 Token`;
      if (granularity === "week") {
        return <div className="quota-activity-week-column" key={item.day} role="gridcell" tabIndex={0} aria-label={tooltip} data-tooltip={tooltip}>
          {item.days?.map((day) => {
            const level = day.tokens === 0 ? 0 : Math.min(4, Math.ceil(day.tokens / maxTokens * 4));
            return <i className={`quota-activity-week-square level-${level}`} key={day.day} aria-hidden="true" />;
          })}
        </div>;
      }
      const level = item.tokens === 0 ? 0 : Math.min(4, Math.ceil(item.tokens / maxTokens * 4));
      return <span className={`quota-activity-cell level-${level}`} key={item.day} role="gridcell" tabIndex={0} aria-label={tooltip} data-tooltip={tooltip} />;
    })}</div>
    <div className={`quota-activity-axis is-${granularity}`} aria-hidden="true">{monthLabels.map((item) => <span key={`${item.key}-${item.column}`} style={{ gridColumnStart: item.column + 1 }}>{item.label}</span>)}</div>
    <div className="quota-activity-footer"><span>少</span><div className="quota-activity-legend" aria-hidden="true"><i className="level-0" /><i className="level-1" /><i className="level-2" /><i className="level-3" /><i className="level-4" /></div><span>多</span></div>
  </section>;
}

function UsageSummary({ breakdown, granularity }: { breakdown: QuotaUsageBreakdown[]; granularity: "day" | "week" }) {
  const periodTotals = useMemo(() => {
    const values = new Map<string, number>();
    breakdown.forEach((item) => {
      const period = (item.period_start ?? item.day).slice(0, 10);
      values.set(period, (values.get(period) ?? 0) + item.total_tokens);
    });
    return values;
  }, [breakdown]);
  const latestPeriod = [...periodTotals.keys()].sort().at(-1);
  const peakTokens = Math.max(0, ...periodTotals.values());
  return <section className="quota-usage-insight" aria-label="用量概览">
    <div className="quota-usage-insight-heading"><h2>用量概览</h2><span>{granularity === "day" ? "按日" : "按周"}</span></div>
    <div className="quota-usage-insight-grid">
      <div><strong>{periodTotals.size}</strong><span>{granularity === "day" ? "活跃天数" : "活跃周数"}</span></div>
      <div><strong>{formatTokenCount(peakTokens)}</strong><span>{granularity === "day" ? "单日峰值" : "单周峰值"}</span></div>
      <div><strong>{latestPeriod ? formatActivityDate(latestPeriod) : "暂无"}</strong><span>最近使用</span></div>
    </div>
  </section>;
}

export function QuotaUsagePage({ embedded = false, userId, workspaceIds: providedWorkspaceIds }: { embedded?: boolean; userId?: string; workspaceIds?: string[] }) {
  const auth = useOptionalAuth();
  const authUser = auth?.user;
  const resolvedUserId = userId ?? authUser?.user_id;
  const workspaceIds = useMemo(() => (providedWorkspaceIds ?? authUser?.workspace_ids ?? []).filter((item) => item !== "*"), [authUser, providedWorkspaceIds]);
  const hasAuthContext = Boolean(authUser || userId || providedWorkspaceIds);
  // Keep the first workspace as an internal API scope until workspace selection is
  // introduced. The personal page intentionally does not expose workspace concepts.
  const selectedWorkspaceId = workspaceIds[0];
  const [quota, setQuota] = useState<QuotaSnapshot | null>(null);
  const [recentUsage, setRecentUsage] = useState<QuotaUsageSnapshot | null>(null);
  const [dailyUsage, setDailyUsage] = useState<QuotaUsageSnapshot | null>(null);
  const [weeklyUsage, setWeeklyUsage] = useState<QuotaUsageSnapshot | null>(null);
  const [activityGranularity, setActivityGranularity] = useState<"day" | "week">("day");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    if (!hasAuthContext || !resolvedUserId || (workspaceIds.length > 0 && !selectedWorkspaceId)) return;
    setError("");
    const results = await Promise.allSettled([
      api.getQuota(selectedWorkspaceId),
      api.getUsage(7, selectedWorkspaceId, "day"),
      api.getUsage(182, selectedWorkspaceId, "day"),
      api.getUsage(182, selectedWorkspaceId, "week"),
    ]);
    const [quotaResult, recentResult, dailyResult, weeklyResult] = results;
    if (quotaResult.status === "fulfilled") setQuota(quotaResult.value.quota);
    if (recentResult.status === "fulfilled") setRecentUsage(recentResult.value);
    if (dailyResult.status === "fulfilled") setDailyUsage(dailyResult.value);
    if (weeklyResult.status === "fulfilled") setWeeklyUsage(weeklyResult.value);

    const rejected = results.filter((result) => result.status === "rejected");
    if (rejected.length === 0) {
      setError("");
    } else if (quotaResult.status === "rejected") {
      const reason = quotaResult.reason;
      setError(reason instanceof ApiError && reason.code === "quota_schema_outdated"
        ? "额度服务需要先完成数据库迁移，请执行 alembic upgrade head 后重启服务。"
        : reason instanceof ApiError && reason.status === 403
          ? "当前账号暂时无法读取额度，请稍后重试。"
          : reason instanceof Error ? reason.message : String(reason));
    } else {
      setError(`部分用量数据加载失败，额度余额仍可用。失败 ${rejected.length} 项，请稍后重试。`);
    }
    setLoading(false);
  }, [hasAuthContext, resolvedUserId, selectedWorkspaceId, workspaceIds]);
  useEffect(() => {
    if (!hasAuthContext) {
      queueMicrotask(() => setLoading(false));
      return;
    }
    queueMicrotask(() => void load());
  }, [hasAuthContext, load]);
  useEffect(() => {
    if (!hasAuthContext) return undefined;
    const socket = new StudentSocket((event) => {
      if (event.type === "usage.snapshot") void load();
    }, () => undefined);
    socket.connect();
    return () => socket.close();
  }, [hasAuthContext, load]);
  const buckets = useMemo(() => quota?.buckets ?? [], [quota]);
  const personalBuckets = useMemo(() => buckets.filter((item) => item.owner_type === "user"), [buckets]);
  // Admission applies every returned constraint at the same time. The
  // displayed balance must therefore be the smallest remaining bucket, even
  // though the compact detail list only shows personal daily/weekly rows.
  const finiteBuckets = buckets.filter((item) => item.limit_micro != null);
  const effectiveRemaining = finiteBuckets.length > 0 ? Math.min(...finiteBuckets.map((item) => item.remaining_micro)) : null;
  const effectiveRemainingLabel = buckets.length === 0 ? "暂无" : finiteBuckets.length === 0 ? "无限" : formatMicro(effectiveRemaining);
  const activityUsage = activityGranularity === "day" ? dailyUsage : weeklyUsage;
  const totalTokens = recentUsage?.tokens?.total_tokens ?? 0;
  if (loading && !quota) return <main className={`quota-page${embedded ? " quota-page-embedded" : ""}`}><div className="quota-loading"><RefreshCw className="spin" />正在读取额度…</div></main>;
  return <main className={`quota-page${embedded ? " quota-page-embedded" : ""}`}>
    <div className="quota-page-toolbar"><button className="quota-refresh-button" type="button" onClick={() => void load()} disabled={loading} aria-label="刷新额度" title="刷新额度"><RefreshCw size={16} className={loading ? "spin" : ""} /></button></div>
    {error && <div className="quota-error" role="alert"><CircleAlert size={17} /><span>{error}</span><button type="button" onClick={() => void load()}>重试</button></div>}
    <section className="quota-stat-strip"><div><strong>{effectiveRemainingLabel}</strong><span>当前可用</span></div><div><strong>{Number(recentUsage?.events ?? 0).toLocaleString("zh-CN")}</strong><span>近 7 天请求</span></div><div><strong>{totalTokens.toLocaleString("zh-CN")}</strong><span>近 7 天 Token</span></div><div><strong>{recentUsage?.credits_complete ? "正常" : "待处理"}</strong><span>账务状态</span></div></section>
    <UsageSummary breakdown={activityUsage?.breakdown ?? []} granularity={activityGranularity} />
    <section className="quota-panel quota-balances-panel"><div className="quota-panel-heading"><h2>额度</h2><span>{personalBuckets.length ? `${personalBuckets.length} 项周期额度` : "暂无额度"}</span></div><div className="quota-balance-list">{personalBuckets.map((bucket) => <BucketSummary bucket={bucket} key={`${bucket.owner_type}-${bucket.owner_id}-${bucket.bucket_type}`} />)}{personalBuckets.length === 0 && <div className="quota-empty quota-empty-compact"><Coins size={18} /><span>当前周期暂无可用额度。</span></div>}</div></section>
    <ActivityHeatmap breakdown={activityUsage?.breakdown ?? []} dailyBreakdown={dailyUsage?.breakdown ?? []} granularity={activityGranularity} onGranularityChange={setActivityGranularity} />
  </main>;
}

export default QuotaUsagePage;
