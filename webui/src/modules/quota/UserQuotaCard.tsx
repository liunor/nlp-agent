import { useEffect, useState } from "react";
import { api } from "@/platform/http/api";
import type { QuotaPolicyExplanation, QuotaSnapshot } from "@/shared/types";
import "./user-quota-card.css";

type Balance = { quota: QuotaSnapshot; policy: QuotaPolicyExplanation | null };
const credits = (value: number) => (value / 1_000_000).toLocaleString("zh-CN", { maximumFractionDigits: 6 });

export function UserQuotaCard({ userId }: { userId: string }) {
  const [reload, setReload] = useState(0);
  const [response, setResponse] = useState<{ userId: string; reload: number; data?: Balance; error?: string }>();
  useEffect(() => {
    let active = true;
    void api.getUserQuota(userId).then(
      (data) => { if (active) setResponse({ userId, reload, data }); },
      (error: unknown) => { if (active) setResponse({ userId, reload, error: error instanceof Error ? error.message : "额度读取失败" }); },
    );
    return () => { active = false; };
  }, [userId, reload]);
  const current = response?.userId === userId && response.reload === reload ? response : undefined;
  const buckets = current?.data?.quota.buckets.filter((bucket) => bucket.owner_type === "user") ?? [];
  return <section className="quota-user-card">
    <div className="quota-button-row"><h3>用户额度</h3><button type="button" onClick={() => setReload((value) => value + 1)}>刷新额度</button></div>
    {!current && <p role="status">正在读取额度…</p>}
    {current?.error && <p role="alert">{current.error}</p>}
    {current?.data?.policy && <p>生效策略：{current.data.policy.base.code} / {current.data.policy.base.version}</p>}
    {current?.data && !buckets.length && <p>尚无本周期用量记录</p>}
    {!!buckets.length && <div className="quota-table-wrap"><table><thead><tr><th>周期</th><th>剩余额度</th><th>已消费</th><th>预占中</th></tr></thead><tbody>
      {buckets.map((bucket) => <tr key={`${bucket.bucket_type}:${bucket.reset_at}`}><td>{bucket.bucket_type === "daily" ? "今日" : "本周"}</td><td>{credits(bucket.remaining_micro)}</td><td>{credits(bucket.consumed_micro)}</td><td>{credits(bucket.reserved_micro)}</td></tr>)}
    </tbody></table></div>}
  </section>;
}
