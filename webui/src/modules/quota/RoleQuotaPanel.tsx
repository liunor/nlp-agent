import { RefreshCw, Save, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/platform/http/api";
import type { QuotaBinding, QuotaPolicy, RbacRole } from "@/shared/types";

const ROLE_PLAN = [
  { code: "guest", name: "游客", daily: 1500000, weekly: 7500000, request: 1000000, priority: 10 },
  { code: "student", name: "学生", daily: 3000000, weekly: 15000000, request: 1000000, priority: 20 },
  { code: "teacher", name: "教师", daily: 2250000, weekly: 11250000, request: 1000000, priority: 30 },
  { code: "developer", name: "开发者", daily: 3000000, weekly: 15000000, request: 1000000, priority: 40 },
] as const;
type RoleCode = typeof ROLE_PLAN[number]["code"];
type Row = { daily: string; weekly: string; request: string; concurrency: string; overdraft: number; profiles: string[]; unlimited: boolean };
type Snapshot = { policies: QuotaPolicy[]; bindings: QuotaBinding[]; roles: RbacRole[] };

function effective(binding: QuotaBinding, now = Date.now()) {
  return binding.status === "active" && Date.parse(binding.effective_from) <= now
    && (!binding.effective_until || Date.parse(binding.effective_until) > now);
}

function currentRole(snapshot: Snapshot, code: RoleCode) {
  const bindings = snapshot.bindings.filter((item) => item.subject_type === "role" && item.subject_id === code && effective(item))
    .sort((a, b) => b.priority - a.priority);
  if (bindings.length > 1 && bindings[0].priority === bindings[1].priority) {
    throw new Error("存在相同优先级的角色绑定，请先在策略绑定中处理。");
  }
  const binding = bindings[0];
  const policy = snapshot.policies.find((item) => item.policy_id === binding?.policy_id);
  return { binding, policy };
}

function recommended(code: RoleCode): Row {
  const plan = ROLE_PLAN.find((item) => item.code === code)!;
  return { daily: String(plan.daily), weekly: String(plan.weekly), request: String(plan.request),
    concurrency: "1", overdraft: 0, profiles: [], unlimited: false };
}

function fromPolicy(code: RoleCode, policy?: QuotaPolicy): Row {
  if (!policy) return recommended(code);
  return { daily: policy.daily_limit_micro == null ? "" : String(policy.daily_limit_micro),
    weekly: policy.weekly_limit_micro == null ? "" : String(policy.weekly_limit_micro),
    request: policy.request_limit_micro == null ? "" : String(policy.request_limit_micro),
    concurrency: policy.concurrency_limit == null ? "" : String(policy.concurrency_limit),
    overdraft: policy.max_overdraft_micro, profiles: policy.allowed_model_profiles, unlimited: policy.unlimited };
}

function numberValue(value: string, label: string, minimum = 0): number | null {
  if (!value.trim()) return null;
  if (!/^\d+$/.test(value.trim()) || !Number.isSafeInteger(Number(value)) || Number(value) < minimum) {
    throw new Error(`${label}必须是${minimum === 1 ? "正" : "非负"}整数。`);
  }
  return Number(value);
}

function policyValues(row: Row, roleName: string) {
  return {
    daily_limit_micro: numberValue(row.daily, `${roleName}每日上限`),
    weekly_limit_micro: numberValue(row.weekly, `${roleName}每周上限`),
    request_limit_micro: numberValue(row.request, `${roleName}单请求上限`),
    concurrency_limit: numberValue(row.concurrency, `${roleName}并发数`, 1),
    max_overdraft_micro: row.overdraft,
    allowed_model_profiles: row.profiles,
    unlimited: row.unlimited,
  };
}

async function readSnapshot(): Promise<Snapshot> {
  const [policies, bindings, roles] = await Promise.all([api.listQuotaPolicies(), api.listQuotaBindings(), api.listRoles()]);
  return { policies: policies.items, bindings: bindings.items, roles: roles.items };
}

function sameBinding(a: ReturnType<typeof currentRole>, b: ReturnType<typeof currentRole>) {
  return a.binding?.binding_id === b.binding?.binding_id && a.binding?.policy_id === b.binding?.policy_id
    && a.binding?.priority === b.binding?.priority;
}

export function RoleQuotaPanel({ onSaved }: { onSaved?: () => Promise<void> }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [rows, setRows] = useState<Record<RoleCode, Row>>(() => Object.fromEntries(ROLE_PLAN.map((role) => [role.code, recommended(role.code)])) as Record<RoleCode, Row>);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const saveLock = useRef(false);

  const refresh = useCallback(() => readSnapshot()
    .then((next) => {
      const nextRows = {} as Record<RoleCode, Row>;
      for (const role of ROLE_PLAN) {
        nextRows[role.code] = fromPolicy(role.code, currentRole(next, role.code).policy);
      }
      setSnapshot(next);
      setRows(nextRows);
      setError("");
    })
    .catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : String(reason));
    })
    .finally(() => {
      setLoading(false);
    }), []);
  useEffect(() => { void refresh(); }, [refresh]);

  const save = async (codes: RoleCode[]) => {
    if (!snapshot || saveLock.current) return;
    saveLock.current = true;
    setSaving(true);
    setError("");
    setMessage("");
    const completed: string[] = [];
    let phase = "校验";
    try {
      // Validate every requested row before issuing any write request.
      const values = new Map(codes.map((code) => [code, policyValues(rows[code], ROLE_PLAN.find((role) => role.code === code)!.name)]));
      const fresh = await readSnapshot();
      for (const code of codes) {
        if (!fresh.roles.some((role) => role.code === code && role.status === "active")) throw new Error("该角色未启用，请刷新角色列表。");
        if (!sameBinding(currentRole(snapshot, code), currentRole(fresh, code))) throw new Error("角色额度已被其他操作修改，请刷新后重新调整。");
      }
      for (const code of codes) {
        const role = ROLE_PLAN.find((item) => item.code === code)!;
        const previous = currentRole(fresh, code);
        const limits = values.get(code)!;
        const unchanged = previous.policy && previous.binding?.priority === role.priority
          && Object.entries(limits).every(([key, value]) => JSON.stringify(previous.policy![key as keyof QuotaPolicy]) === JSON.stringify(value));
        if (unchanged) continue;
        const timestamp = new Date();
        phase = `${role.name}：创建新版本`;
        const draft = await api.createQuotaPolicy({ ...limits, code: `role-${code}`, name: `${role.name}额度`,
          version: `${timestamp.getFullYear()}.${timestamp.getMonth() + 1}.${timestamp.getDate()}.${timestamp.getTime()}.${crypto.randomUUID().slice(0, 8)}`,
          status: "draft", effective_from: timestamp.toISOString(), effective_until: null });
        phase = `${role.name}：发布新版本`;
        const published = await api.publishQuotaPolicy(draft.policy_id);
        phase = `${role.name}：应用角色绑定`;
        const effectiveFrom = new Date(Math.max(Date.now(), Date.parse(published.effective_from))).toISOString();
        try {
          await api.bindQuotaPolicy({ subject_type: "role", subject_id: code, policy_id: draft.policy_id,
            priority: role.priority, effective_from: effectiveFrom, effective_until: null });
        } catch (reason) {
          // A lost response can follow a committed binding. Reconcile before
          // reporting failure so a retry does not duplicate a successful save.
          const result = await api.listQuotaBindings();
          if (!result.items.some((binding) => binding.subject_type === "role" && binding.subject_id === code
            && binding.policy_id === draft.policy_id && binding.status === "active")) throw reason;
        }
        completed.push(role.name);
      }
      setLoading(true);
      await refresh();
      await onSaved?.();
      setMessage(completed.length ? `${completed.join("、")}额度已保存，后续新请求使用新规则。` : "当前额度与填写值一致，无需重复保存。");
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : String(reason);
      setError(`${phase}未完成：${detail}${completed.length ? ` 已保存：${completed.join("、")}。` : ""} 请刷新查看当前配置后再重试。`);
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  };

  const activeCodes = ROLE_PLAN.filter((item) => snapshot?.roles.some((role) => role.code === item.code && role.status === "active")).map((item) => item.code);
  const overrides = snapshot?.bindings.filter((item) => item.subject_type === "user" && effective(item)).length ?? 0;
  const update = (code: RoleCode, field: keyof Pick<Row, "daily" | "weekly" | "request" | "concurrency">, value: string) => {
    setRows((current) => ({ ...current, [code]: { ...current[code], [field]: value } }));
  };
  return <section className="quota-panel quota-route-content quota-role-panel">
    <div className="quota-panel-heading"><div className="quota-section-title"><span className="quota-section-icon"><ShieldCheck size={17} /></span><div><h2>角色额度</h2><p>修改数字并保存，系统自动更新角色策略。单位：μcredits。</p></div></div><button type="button" onClick={() => { setLoading(true); void refresh(); }} disabled={loading || saving}><RefreshCw size={15} />刷新</button></div>
    <p className="quota-form-help">试用方案：每日和每周额度以学生为基准，开发者 100%、教师 75%、游客 50%；四种角色单请求上限均为 1,000,000 μcredits。保存后用于新请求，已消耗额度继续计入当前周期。留空表示该项不单独限制。</p>
    {overrides > 0 && <div className="quota-inline-notice">当前有 {overrides} 个用户专属绑定，会优先于角色规则。需要按角色执行的用户，请在“策略绑定”中结束对应用户绑定。</div>}
    {error && <div className="quota-inline-notice" role="alert">{error}</div>}
    {message && <div className="quota-inline-notice success" role="status">{message}</div>}
    <div className="quota-button-row"><button type="button" disabled={loading || saving} onClick={() => setRows(Object.fromEntries(ROLE_PLAN.map((role) => [role.code, recommended(role.code)])) as Record<RoleCode, Row>)}>填入试用方案</button><button className="quota-primary-button" type="button" disabled={loading || saving || activeCodes.length === 0} onClick={() => void save(activeCodes)}><Save size={15} />{saving ? "正在保存…" : "保存全部角色额度"}</button></div>
    <div className="quota-table-wrap"><table className="quota-role-table"><thead><tr><th>角色</th><th>每日上限</th><th>每周上限</th><th>单请求上限</th><th>并发数</th><th>配置状态</th><th>操作</th></tr></thead><tbody>{ROLE_PLAN.map((role) => {
      const enabled = activeCodes.includes(role.code);
      const current = snapshot ? currentRole(snapshot, role.code) : undefined;
      return <tr key={role.code}><td><strong>{role.name}</strong></td>{(["daily", "weekly", "request", "concurrency"] as const).map((field, index) => <td key={field}><input aria-label={`${role.name}${["每日上限", "每周上限", "单请求上限", "并发数"][index]}`} inputMode="numeric" value={rows[role.code][field]} disabled={loading || saving || !enabled} onChange={(event) => update(role.code, field, event.target.value)} /></td>)}<td>{!snapshot ? "读取中" : !enabled ? "角色未启用" : current?.policy?.unlimited ? "已配置无限额度" : current?.policy ? "已配置" : "待配置"}</td><td><button type="button" disabled={loading || saving || !enabled} onClick={() => void save([role.code])}>保存{role.name}额度</button></td></tr>;
    })}</tbody></table></div>
    <p className="quota-form-help">多角色按开发者、教师、学生、游客的顺序选择一个角色策略。额度执行范围仍由环境配置决定。</p>
  </section>;
}
