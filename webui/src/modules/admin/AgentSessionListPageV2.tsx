import { useCallback, useEffect, useState } from "react";

import { api } from "@/platform/http/api";
import type { AgentSessionStats, SessionSummary } from "@/shared/types";

const PAGE_SIZE = 50;

function formatDate(value: string | number | undefined | null) {
  if (value == null) return "-";
  const date = new Date(typeof value === "number" && value < 1_000_000_000_000 ? value * 1000 : value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString("zh-CN");
}

export function AgentSessionListPageV2() {
  const [items, setItems] = useState<SessionSummary[]>([]);
  const [stats, setStats] = useState<AgentSessionStats | null>(null);
  const [selected, setSelected] = useState<SessionSummary | null>(null);
  const [turns, setTurns] = useState<Awaited<ReturnType<typeof api.listTurns>>["items"]>([]);
  const [message, setMessage] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [page, summary] = await Promise.all([
        api.listSessions({ limit: PAGE_SIZE, offset }),
        api.getSessionStats(),
      ]);
      setItems(page.items);
      setTotal(page.total ?? page.items.length);
      setHasMore(page.has_more ?? false);
      setStats(summary);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => {
    queueMicrotask(() => void load());
  }, [load]);

  const select = async (item: SessionSummary) => {
    setSelected(item);
    try {
      setTurns((await api.listTurns(item.session_id)).items);
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载会话记录失败");
    }
  };

  const remove = async (item: SessionSummary) => {
    if (!confirm(`删除 Agent 会话 ${item.session_id}？`)) return;
    try {
      await api.deleteSession(item.session_id);
      setSelected(null);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "删除失败");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Agent 会话</h1>
        <p className="text-sm text-gray-500">
          会话、Turn 和 Checkpoint 由服务端按用户与工作区校验；页面只加载当前页的脱敏元数据。
        </p>
      </div>

      {stats && (
        <div className="grid gap-4 sm:grid-cols-4">
          {[
            ["会话总数", stats.sessions_total],
            ["活跃会话", stats.sessions_active],
            ["Turn 总数", stats.turns_total ?? "-"],
            ["近 24 小时 Turn", stats.turns_last_24h ?? "-"],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded-lg border border-gray-200 bg-white p-4">
              <p className="text-xs text-gray-500">{label}</p>
              <p className="mt-1 text-2xl font-bold text-gray-900">{value}</p>
            </div>
          ))}
        </div>
      )}

      {message && <div className="rounded bg-red-50 p-3 text-sm text-red-700">{message}</div>}
      <div className="grid gap-6 lg:grid-cols-[1fr_1.5fr]">
        <div className="space-y-2">
          {loading && <div className="rounded border bg-white p-8 text-center text-sm text-gray-500">加载中...</div>}
          {!loading && items.map((item) => (
            <div key={item.session_id} className={`flex items-center justify-between rounded border bg-white p-3 ${selected?.session_id === item.session_id ? "border-blue-400" : "border-gray-200"}`}>
              <button type="button" className="min-w-0 flex-1 text-left" onClick={() => void select(item)}>
                <strong className="block truncate text-sm">{item.session_id}</strong>
                <span className="block text-xs text-gray-500">工作区 {item.workspace_id} · {item.channel}</span>
                <span className="text-xs text-gray-400">最近活动：{formatDate(item.last_active)}</span>
              </button>
              <button type="button" className="ml-3 text-xs text-red-700" onClick={() => void remove(item)}>删除</button>
            </div>
          ))}
          {!loading && !items.length && <div className="rounded border bg-white p-8 text-center text-sm text-gray-500">暂无 Agent 会话</div>}
          <div className="flex items-center justify-between pt-2 text-sm text-gray-500">
            <span>{total ? `${offset + 1}-${Math.min(offset + PAGE_SIZE, total)} / ${total}` : "0 条"}</span>
            <div className="flex gap-2">
              <button type="button" className="rounded border px-3 py-1 disabled:opacity-40" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>上一页</button>
              <button type="button" className="rounded border px-3 py-1 disabled:opacity-40" disabled={!hasMore || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>下一页</button>
            </div>
          </div>
        </div>

        <section className="rounded border border-gray-200 bg-white p-4">
          {!selected ? <p className="py-10 text-center text-sm text-gray-500">选择会话查看 Turn 元数据</p> : <>
            <h2 className="font-semibold">{selected.session_id}</h2>
            <p className="text-xs text-gray-500">所有权：{selected.user_id} · 工作区：{selected.workspace_id}</p>
            <div className="mt-4 space-y-2">
              {turns.map((turn) => <div key={turn.turn_id} className="rounded bg-gray-50 p-3 text-sm"><div className="flex justify-between"><strong>{turn.turn_id}</strong><span>{turn.status}</span></div><p className="mt-1 text-xs text-gray-500">创建：{turn.created_at} · {turn.completed_at ? `完成：${turn.completed_at}` : "仍在处理或未完成"}</p></div>)}
              {!turns.length && <p className="py-8 text-center text-sm text-gray-500">暂无 Turn</p>}
            </div>
          </>}
        </section>
      </div>
    </div>
  );
}
