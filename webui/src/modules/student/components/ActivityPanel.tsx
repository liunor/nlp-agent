import {
  Brain,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Globe,
  LoaderCircle,
  MessageSquareText,
  Search,
  Sparkles,
  Users,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { ActivityItem, ActivityKind } from "@/shared/types";

const icons: Record<ActivityKind, typeof Search> = {
  thinking: Brain,
  tool: Wrench,
  worker: Users,
  recovery: CircleAlert,
  search: Search,
  fetch: Globe,
  commentary: MessageSquareText,
  synthesis: Sparkles,
};

function elapsedSeconds(from?: string, until?: string, now = Date.now()) {
  const started = from ? Date.parse(from) : NaN;
  const ended = until ? Date.parse(until) : now;
  if (!Number.isFinite(started) || !Number.isFinite(ended)) return 0;
  return Math.max(0, Math.round((ended - started) / 1000));
}

function durationLabel(seconds: number) {
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function ActivityPanel({
  activities,
  summary,
  reasoning,
  showReasoning,
  running,
  hasContent = false,
  startedAt: turnStartedAt,
  completedAt: turnCompletedAt,
}: {
  activities: ActivityItem[];
  summary?: string;
  reasoning?: string;
  showReasoning: boolean;
  running: boolean;
  hasContent?: boolean;
  startedAt?: string;
  completedAt?: string;
}) {
  const [userExpanded, setUserExpanded] = useState<boolean | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const validActivities = useMemo(
    () =>
      activities.filter(
        (item): item is ActivityItem =>
          !!item && typeof item.label === "string" && typeof item.status === "string",
      ),
    [activities],
  );
  const activityRunning = validActivities.some((item) => item.status === "running");
  const startedAt =
    turnStartedAt ??
    validActivities
      .map((item) => item.startedAt)
      .filter((value): value is string => !!value)
      .sort()[0];
  const completedAt =
    turnCompletedAt ??
    validActivities
      .map((item) => item.completedAt)
      .filter((value): value is string => !!value)
      .sort()
      .at(-1);
  const seconds = elapsedSeconds(startedAt, running ? undefined : completedAt, now);

  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [running]);

  // Once the final answer begins streaming (or finishes), release manual override
  // only if user hasn't explicitly toggled after content arrived.
  useEffect(() => {
    if (hasContent && userExpanded === null) return;
  }, [hasContent, userExpanded]);

  if (!validActivities.length && !reasoning && !summary) return null;

  const autoExpanded = (running || activityRunning) && !hasContent;
  const expanded = userExpanded ?? autoExpanded;

  const latestRunning = [...validActivities].reverse().find((item) => item.status === "running");
  const latestCommentary = [...validActivities].reverse().find((item) => item.kind === "commentary");
  const liveHeadline = summary?.trim() || latestRunning?.label || latestCommentary?.label || "运行中";
  const stepCount = validActivities.filter((item) => item.kind !== "commentary").length;
  const triggerText = running
    ? `${liveHeadline} · ${durationLabel(seconds)}`
    : stepCount > 1
      ? `已处理 ${durationLabel(seconds)} · ${stepCount} 个步骤`
      : `已处理 ${durationLabel(seconds)}`;

  return (
    <section className="activity-panel" aria-label="处理过程">
      <button
        type="button"
        className="activity-trigger"
        aria-expanded={expanded}
        onClick={() => setUserExpanded((prev) => !(prev ?? autoExpanded))}
      >
        {running ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />}
        <span className="activity-trigger-label">{triggerText}</span>
        <ChevronDown size={14} className={expanded ? "rotate" : ""} />
      </button>
      {expanded && (validActivities.length > 0 || (showReasoning && reasoning)) && (
        <div className="activity-list" role="list">
          {validActivities.map((item, index) => {
            const BaseIcon = icons[item.kind] ?? Search;
            const RowIcon =
              item.status === "running"
                ? LoaderCircle
                : item.status === "error"
                  ? CircleAlert
                  : BaseIcon;
            return (
              <div
                className={`activity-row ${item.status} is-${item.kind}`}
                key={`${item.id}:${index}`}
                role="listitem"
              >
                <RowIcon size={14} className={item.status === "running" ? "spin" : undefined} />
                <span className="activity-row-label">{item.label}</span>
                {item.detail && <small className="activity-row-detail">{item.detail}</small>}
              </div>
            );
          })}
          {showReasoning && reasoning && (
            <details className="reasoning-detail">
              <summary>查看模型思考内容</summary>
              <pre>{reasoning}</pre>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
