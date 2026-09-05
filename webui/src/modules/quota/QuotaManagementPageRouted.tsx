import { AlertTriangle, Check, CircleAlert, Coins, Database, FileCheck2, History, RefreshCw, RotateCcw, Save, ShieldCheck, Sparkles, WalletCards } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";

import { api } from "@/platform/http/api";
import type { QuotaAdjustment, QuotaAlert, QuotaArchiveBatch, QuotaBillingRecord, QuotaBinding, QuotaBucketCandidate, QuotaBucketReplay, QuotaCreditOperation, QuotaCreditOperationInput, QuotaDailyRollup, QuotaGrant, QuotaPolicy, QuotaPricingRule, QuotaRoleCreditOperationInput, RbacRole } from "@/shared/types";
import { formatMicro } from "./QuotaUsagePage";

type Tab = "control" | "operations" | "recovery";
type ControlRoute = "policies" | "pricing" | "bindings" | "grants" | "adjustments";
type OperationsRoute = "credits" | "billing" | "alerts";
type RecoveryRoute = "ledger" | "rollups";
type CreditOwnerType = QuotaCreditOperationInput["owner_type"] | "role";
type PolicyForm = { code: string; version: string; name: string; daily: string; weekly: string; request: string; concurrency: string; overdraft: string; profiles: string; unlimited: boolean };
type BindingForm = { subject_type: string; subject_id: string; policy_id: string; priority: string };
type AllocationForm = { owner_type: string; owner_id: string; bucket_type: string; amount: string; period_start: string; period_end: string; expires_at: string; idempotency_key: string; reason: string };
type AdjustmentForm = AllocationForm & { reason: string };
type PricingRuleForm = {
  pricing_key: string;
  version: string;
  effective_from: string;
  effective_until: string;
  ordinary_input: string;
  cached_input: string;
  cache_write: string;
  output: string;
  reasoning_output: string;
};

const localInput = (offsetMs = 0) => localInputFromDate(new Date(Date.now() + offsetMs));
const localInputFromDate = (value: Date) => {
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}`;
};
const periodEndInput = (periodStart: string, bucketType: "daily" | "weekly") => {
  const value = new Date(periodStart);
  if (Number.isNaN(value.getTime())) return localInput(bucketType === "weekly" ? 7 * 86_400_000 : 86_400_000);
  value.setDate(value.getDate() + (bucketType === "weekly" ? 7 : 1));
  return localInputFromDate(value);
};
const toUtc = (value: string) => new Date(value).toISOString();
const dateInput = (offsetDays = 0) => {
  const value = new Date(Date.now() + offsetDays * 86_400_000);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
};
const displayMicro = (value: number | null | undefined) => value == null ? "—" : formatMicro(value);
const reasonOf = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const policyNumberError = (value: string, label: string) => {
  const raw = value.trim();
  if (!raw) return "";
  if (!/^\d+$/.test(raw)) return `${label}必须是非负整数`;
  if (!Number.isSafeInteger(Number(raw))) return `${label}不能超过 ${Number.MAX_SAFE_INTEGER.toLocaleString("zh-CN")}`;
  return "";
};

function Field({ label, children, className = "", error }: { label: string; children: ReactNode; className?: string; error?: string }) {
  return <label className={`quota-field ${className}`}><span>{label}</span>{children}{error && <small className="quota-field-error" role="alert">{error}</small>}</label>;
}

function Notice({ message, tone = "error" }: { message: string; tone?: "error" | "success" }) {
  return message ? <div className={`quota-inline-notice ${tone}`} role={tone === "error" ? "alert" : undefined}><CircleAlert size={14} />{message}</div> : null;
}

function SectionHeading({ icon: Icon, title, hint, action }: { icon: typeof Coins; title: string; hint: string; action?: ReactNode }) {
  const shortHints: Record<string, string> = {
    "策略版本": "定义每日 / 每周基础规则，发布后用于新请求。",
    "价格规则": "按 Runtime pricing_key 配置 Token 到 μcredits 的版本化单价。",
    "策略绑定": "把策略应用到默认、角色、用户或工作空间。",
    "Quota Grant 分配": "临时增加指定周期额度，不改变基础策略。",
    "手工调整": "对指定周期做人工加减，必须留下原因。",
    "Credits 赠送 / 重置": "赠送增加指定周期额度；重置只替换旧 Grant。",
    "Provider 账单对账": "用 Provider 账单核对本地用量，定位差异。",
    "告警中心": "确认或解决异常用量提示，不改原始流水。",
    "Ledger 重放与 UsageEvent 归档": "检查余额并安全归档历史用量，不删除原始事件。",
    "Daily Rollup": "查看按日汇总，用于趋势和运营核对。",
  };
  return <div className="quota-panel-heading"><div className="quota-section-title"><span className="quota-section-icon"><Icon size={17} /></span><div><h2>{title}</h2><p>{shortHints[title] ?? hint}</p></div></div>{action}</div>;
}

function SubRouteNav({ value, items, onChange }: { value: string; items: Array<{ value: string; label: string }>; onChange: (value: string) => void }) {
  return <nav className="quota-subroute-tabs" aria-label="额度管理子模块">{items.map((item) => <button key={item.value} type="button" className={value === item.value ? "active" : ""} aria-current={value === item.value ? "page" : undefined} onClick={() => onChange(item.value)}>{item.label}</button>)}</nav>;
}

function PolicyPanel({ policies, form, setForm, onSave, onPublish, onArchive }: { policies: QuotaPolicy[]; form: PolicyForm; setForm: Dispatch<SetStateAction<PolicyForm>>; onSave: (policyId: string | null) => Promise<void>; onPublish: (policyId: string) => Promise<void>; onArchive: (policyId: string) => Promise<void> }) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const editPolicy = (policy: QuotaPolicy) => {
    setEditingId(policy.policy_id);
    setForm({
      code: policy.code,
      version: policy.version,
      name: policy.name,
      daily: policy.daily_limit_micro == null ? "" : String(policy.daily_limit_micro),
      weekly: policy.weekly_limit_micro == null ? "" : String(policy.weekly_limit_micro),
      request: policy.request_limit_micro == null ? "" : String(policy.request_limit_micro),
      concurrency: policy.concurrency_limit == null ? "" : String(policy.concurrency_limit),
      overdraft: String(policy.max_overdraft_micro),
      profiles: policy.allowed_model_profiles.join(", "),
      unlimited: policy.unlimited,
    });
  };
  const save = async () => {
    if ([
      policyNumberError(form.daily, "每日上限"),
      policyNumberError(form.weekly, "每周上限"),
      policyNumberError(form.request, "单请求上限"),
      policyNumberError(form.concurrency, "并发数"),
      policyNumberError(form.overdraft, "有限透支"),
    ].some(Boolean)) return;
    await onSave(editingId);
    setEditingId(null);
  };
  return <section className="quota-panel quota-route-content"><SectionHeading icon={ShieldCheck} title="策略版本" hint="草稿可编辑和归档；发布后的版本不可修改，变更请创建新版本。" /><div className="quota-form-grid"><Field label="策略代码"><input aria-label="策略代码" value={form.code} onChange={(event) => setForm({ ...form, code: event.target.value })} /></Field><Field label="版本"><input aria-label="版本" value={form.version} onChange={(event) => setForm({ ...form, version: event.target.value })} placeholder="2026.08.30" /></Field><Field label="名称"><input aria-label="名称" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field><Field label="每日上限" error={policyNumberError(form.daily, "每日上限")}><input aria-label="每日上限" inputMode="numeric" value={form.daily} onChange={(event) => setForm({ ...form, daily: event.target.value })} /></Field><Field label="每周上限" error={policyNumberError(form.weekly, "每周上限")}><input aria-label="每周上限" inputMode="numeric" value={form.weekly} onChange={(event) => setForm({ ...form, weekly: event.target.value })} /></Field><Field label="单请求上限" error={policyNumberError(form.request, "单请求上限")}><input aria-label="单请求上限" inputMode="numeric" value={form.request} onChange={(event) => setForm({ ...form, request: event.target.value })} /></Field><Field label="并发数" error={policyNumberError(form.concurrency, "并发数")}><input aria-label="并发数" inputMode="numeric" value={form.concurrency} onChange={(event) => setForm({ ...form, concurrency: event.target.value })} /></Field><Field label="有限透支" error={policyNumberError(form.overdraft, "有限透支")}><input aria-label="有限透支" inputMode="numeric" value={form.overdraft} onChange={(event) => setForm({ ...form, overdraft: event.target.value })} placeholder="0 表示不允许透支" /></Field><Field label="允许模型 Profile" className="quota-field-wide"><input aria-label="允许模型 Profile" value={form.profiles} onChange={(event) => setForm({ ...form, profiles: event.target.value })} placeholder="留空表示不限制；多个值用逗号分隔" /></Field><label className="quota-checkbox-field"><input type="checkbox" checked={form.unlimited} onChange={(event) => setForm({ ...form, unlimited: event.target.checked })} /><span>无限额度策略</span></label></div><div className="quota-button-row"><button className="quota-primary-button" type="button" onClick={() => void save()} disabled={!form.code || !form.version || !form.name}><Save size={15} />{editingId ? "保存策略草稿" : "创建策略草稿"}</button>{editingId && <button type="button" onClick={() => setEditingId(null)}>取消编辑</button>}</div><div className="quota-table-wrap"><table><thead><tr><th>策略</th><th>版本</th><th>状态</th><th>每日</th><th>每周</th><th>操作</th></tr></thead><tbody>{policies.map((policy) => <tr key={policy.policy_id}><td>{policy.name}<small>{policy.code}</small></td><td>v{policy.version}</td><td><span className={`quota-status-pill ${policy.status}`}>{policy.status}</span></td><td>{policy.daily_limit_micro == null ? "无限" : displayMicro(policy.daily_limit_micro)}</td><td>{policy.weekly_limit_micro == null ? "无限" : displayMicro(policy.weekly_limit_micro)}</td><td className="quota-row-actions">{policy.status === "draft" && <><button type="button" onClick={() => editPolicy(policy)}>编辑</button><button type="button" onClick={() => void onArchive(policy.policy_id)}>归档</button><button type="button" onClick={() => void onPublish(policy.policy_id)}>发布</button></>}{policy.status === "active" && <span className="quota-row-note">已发布 · 新版本替换</span>}{policy.status === "archived" && <span className="quota-row-note">已归档</span>}</td></tr>)}</tbody></table>{policies.length === 0 && <div className="quota-empty quota-empty-compact"><ShieldCheck size={18} />暂无策略，先创建一个草稿</div>}</div></section>;
}

function PricingPanel({ rules, onSave, onRetire }: { rules: QuotaPricingRule[]; onSave: (form: PricingRuleForm) => Promise<void>; onRetire: (pricingRuleId: string) => Promise<void> }) {
  const [form, setForm] = useState<PricingRuleForm>({ pricing_key: "", version: "", effective_from: localInput(), effective_until: "", ordinary_input: "", cached_input: "", cache_write: "", output: "", reasoning_output: "" });
  const rateError = (value: string, label: string, optional = false) => optional && !value.trim() ? "" : policyNumberError(value, label) || (!value.trim() ? `${label}不能为空` : "");
  const save = async () => {
    const errors = [
      rateError(form.ordinary_input, "普通输入单价"),
      rateError(form.cached_input, "缓存命中单价"),
      rateError(form.cache_write, "缓存写入单价"),
      rateError(form.output, "普通输出单价"),
      rateError(form.reasoning_output, "推理输出单价", true),
    ];
    if (!form.pricing_key.trim() || !form.version.trim() || errors.some(Boolean)) return;
    await onSave(form);
    setForm({ pricing_key: "", version: "", effective_from: localInput(), effective_until: "", ordinary_input: "", cached_input: "", cache_write: "", output: "", reasoning_output: "" });
  };
  const rateFields: Array<[keyof PricingRuleForm, string]> = [["ordinary_input", "普通输入"], ["cached_input", "缓存命中输入"], ["cache_write", "缓存写入"], ["output", "普通输出"]];
  return <section className="quota-panel quota-route-content"><SectionHeading icon={Coins} title="价格规则" hint="按 Runtime pricing_key 配置每百万 Token 的 μcredits 单价；已创建规则不可编辑，只能新建版本。" /><div className="quota-form-grid"><Field label="Pricing Key"><input aria-label="Pricing Key" value={form.pricing_key} onChange={(event) => setForm({ ...form, pricing_key: event.target.value })} placeholder="deepseek/deepseek-v4-pro" /></Field><Field label="版本"><input aria-label="价格规则版本" value={form.version} onChange={(event) => setForm({ ...form, version: event.target.value })} placeholder="2026-09-02" /></Field><Field label="生效时间"><input aria-label="价格规则生效时间" type="datetime-local" value={form.effective_from} onChange={(event) => setForm({ ...form, effective_from: event.target.value })} /></Field><Field label="结束时间（可选）"><input aria-label="价格规则结束时间" type="datetime-local" value={form.effective_until} onChange={(event) => setForm({ ...form, effective_until: event.target.value })} /></Field>{rateFields.map(([key, label]) => <Field key={key} label={`${label}（μcredits / 百万 Token）`} error={rateError(form[key], `${label}单价`)}><input aria-label={`${label}单价`} inputMode="numeric" value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} placeholder="例如 1000000" /></Field>)}<Field label="推理输出（可选）（μcredits / 百万 Token）" error={rateError(form.reasoning_output, "推理输出单价", true)}><input aria-label="推理输出单价" inputMode="numeric" value={form.reasoning_output} onChange={(event) => setForm({ ...form, reasoning_output: event.target.value })} placeholder="留空则按普通输出计价" /></Field></div><p className="quota-form-help">计价会在一次请求结算时按实际 Token 统一向上取整；缺少规则的调用会保持待处理，不会按 0 计费。</p><button className="quota-primary-button" type="button" onClick={() => void save()} disabled={!form.pricing_key || !form.version || rateFields.some(([key]) => !form[key])}><Save size={15} />创建价格规则</button><div className="quota-table-wrap"><table><thead><tr><th>Pricing Key / 版本</th><th>输入</th><th>输出</th><th>生效区间</th><th>状态</th><th>操作</th></tr></thead><tbody>{rules.map((rule) => <tr key={rule.pricing_rule_id}><td><code>{rule.pricing_key}</code><small>v{rule.version}</small></td><td>{rule.ordinary_input_credits_micro_per_million_tokens.toLocaleString("zh-CN")} / {rule.cached_input_credits_micro_per_million_tokens.toLocaleString("zh-CN")}</td><td>{rule.output_credits_micro_per_million_tokens.toLocaleString("zh-CN")}</td><td>{new Date(rule.effective_from).toLocaleString("zh-CN")} · {rule.effective_until ? new Date(rule.effective_until).toLocaleString("zh-CN") : "持续"}</td><td><span className={`quota-status-pill ${rule.status}`}>{rule.status}</span></td><td>{rule.status === "active" && <button type="button" onClick={() => void onRetire(rule.pricing_rule_id)}>停用</button>}</td></tr>)}</tbody></table>{rules.length === 0 && <div className="quota-empty quota-empty-compact"><Coins size={18} />暂无价格规则，请先为 Runtime 的 Pricing Key 配置单价</div>}</div></section>;
}

function BindingPanel({ bindings, activePolicies, form, setForm, onSave, onRetire }: { bindings: QuotaBinding[]; activePolicies: QuotaPolicy[]; form: BindingForm; setForm: Dispatch<SetStateAction<BindingForm>>; onSave: () => Promise<void>; onRetire: (bindingId: string) => Promise<void> }) { const changeSubjectType = (subject_type: string) => setForm({ ...form, subject_type, subject_id: subject_type === "default" ? "*" : form.subject_id === "*" ? "" : form.subject_id }); return <section className="quota-panel quota-route-content"><SectionHeading icon={ShieldCheck} title="策略绑定" hint="默认、角色、用户、工作空间和课堂均可绑定；多角色由服务端择一。" /><div className="quota-form-grid"><Field label="主体类型"><select aria-label="主体类型" value={form.subject_type} onChange={(event) => changeSubjectType(event.target.value)}><option value="default">默认</option><option value="role">角色</option><option value="user">用户</option><option value="workspace">工作空间</option><option value="classroom">课堂</option></select></Field><Field label="主体 ID"><input aria-label="主体 ID" value={form.subject_id} readOnly={form.subject_type === "default"} onChange={(event) => setForm({ ...form, subject_id: event.target.value })} placeholder={form.subject_type === "default" ? "默认主体（系统自动使用）" : "填写具体主体 ID"} /></Field><Field label="策略"><select aria-label="绑定策略" value={form.policy_id} onChange={(event) => setForm({ ...form, policy_id: event.target.value })}>{activePolicies.map((item) => <option key={item.policy_id} value={item.policy_id}>{item.code} · v{item.version}</option>)}</select></Field><Field label="优先级"><input aria-label="优先级" inputMode="numeric" value={form.priority} onChange={(event) => setForm({ ...form, priority: event.target.value })} /></Field></div><button className="quota-primary-button" type="button" onClick={() => void onSave()} disabled={!form.policy_id || !form.subject_id}><ShieldCheck size={15} />发布绑定</button><div className="quota-table-wrap"><table><thead><tr><th>主体</th><th>策略</th><th>状态</th><th>优先级</th><th>生效时间</th><th>操作</th></tr></thead><tbody>{bindings.map((binding) => <tr key={binding.binding_id}><td>{binding.subject_type} / {binding.subject_id}</td><td>{binding.policy_code} · v{binding.policy_version}</td><td><span className={"quota-status-pill " + binding.status}>{binding.status}</span></td><td>{binding.priority}</td><td>{new Date(binding.effective_from).toLocaleString("zh-CN")}</td><td>{binding.status === "active" ? <button type="button" onClick={() => void onRetire(binding.binding_id)}>结束绑定</button> : <span className="quota-row-note">已结束</span>}</td></tr>)}</tbody></table>{bindings.length === 0 && <div className="quota-empty quota-empty-compact"><ShieldCheck size={18} />暂无绑定记录</div>}</div></section>; }
function GrantPanel({ grants, form, setForm, onSave, onRevoke }: { grants: QuotaGrant[]; form: AllocationForm; setForm: Dispatch<SetStateAction<AllocationForm>>; onSave: () => Promise<void>; onRevoke: (grantId: string) => Promise<void> }) {
  const end = periodEndInput(form.period_start, form.bucket_type as "daily" | "weekly");
  return <section className="quota-panel quota-route-content"><SectionHeading icon={Coins} title="Quota Grant 分配" hint="用户、工作空间和课堂 Grant 支持有效期、撤销与审计。" /><div className="quota-form-grid"><Field label="归属类型"><select aria-label="Grant归属类型" value={form.owner_type} onChange={(event) => setForm({ ...form, owner_type: event.target.value })}><option value="user">用户</option><option value="workspace">工作空间</option><option value="classroom">课堂</option></select></Field><Field label="归属 ID"><input aria-label="Grant归属 ID" value={form.owner_id} onChange={(event) => setForm({ ...form, owner_id: event.target.value })} /></Field><Field label="周期"><select aria-label="Grant周期" value={form.bucket_type} onChange={(event) => setForm({ ...form, bucket_type: event.target.value })}><option value="daily">每日</option><option value="weekly">每周</option></select></Field><Field label="额度（μcredits）"><input aria-label="Grant额度" inputMode="numeric" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} /></Field><Field label="周期开始"><input aria-label="Grant周期开始" type="datetime-local" value={form.period_start} onChange={(event) => setForm({ ...form, period_start: event.target.value })} /></Field><Field label="周期结束（自动）"><input aria-label="Grant周期结束" type="datetime-local" value={end} readOnly /></Field><Field label="过期时间"><input aria-label="Grant过期时间" type="datetime-local" value={form.expires_at} onChange={(event) => setForm({ ...form, expires_at: event.target.value })} /></Field><Field label="幂等键"><input aria-label="Grant幂等键" value={form.idempotency_key} onChange={(event) => setForm({ ...form, idempotency_key: event.target.value })} placeholder="业务单号或工单号" /></Field><Field label="原因"><input aria-label="Grant原因" value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} placeholder="例如：新学期额度" /></Field></div><button className="quota-primary-button" type="button" onClick={() => void onSave()} disabled={!form.owner_id || !form.amount || !form.reason || !form.idempotency_key}><Coins size={15} />分配 Grant</button><div className="quota-table-wrap"><table><thead><tr><th>归属</th><th>额度</th><th>状态</th><th>有效期</th><th>操作</th></tr></thead><tbody>{grants.map((grant) => <tr key={grant.grant_id}><td>{grant.owner_type} / {grant.owner_id}</td><td>{displayMicro(grant.allocated_micro)}</td><td><span className={`quota-status-pill ${grant.status}`}>{grant.status}</span></td><td>{grant.expires_at ? new Date(grant.expires_at).toLocaleString("zh-CN") : "周期结束"}</td><td>{grant.status === "active" && <button type="button" onClick={() => void onRevoke(grant.grant_id)}>撤销</button>}</td></tr>)}</tbody></table>{grants.length === 0 && <div className="quota-empty quota-empty-compact"><Coins size={18} />暂无 Grant，创建后会显示在这里</div>}</div></section>;
}

function AdjustmentPanel({ adjustments, form, setForm, onSave }: { adjustments: QuotaAdjustment[]; form: AdjustmentForm; setForm: Dispatch<SetStateAction<AdjustmentForm>>; onSave: () => Promise<void> }) {
  const end = periodEndInput(form.period_start, form.bucket_type as "daily" | "weekly");
  return <section className="quota-panel quota-route-content"><SectionHeading icon={History} title="手工调整" hint="正数补充额度，负数扣减额度；原因和幂等键必填。" /><div className="quota-form-grid"><Field label="归属类型"><select aria-label="调整归属类型" value={form.owner_type} onChange={(event) => setForm({ ...form, owner_type: event.target.value })}><option value="user">用户</option><option value="workspace">工作空间</option><option value="classroom">课堂</option></select></Field><Field label="归属 ID"><input aria-label="调整归属 ID" value={form.owner_id} onChange={(event) => setForm({ ...form, owner_id: event.target.value })} /></Field><Field label="周期"><select aria-label="调整周期" value={form.bucket_type} onChange={(event) => setForm({ ...form, bucket_type: event.target.value })}><option value="daily">每日</option><option value="weekly">每周</option></select></Field><Field label="调整量（μcredits）"><input aria-label="调整量" inputMode="numeric" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} placeholder="可为负数" /></Field><Field label="周期开始"><input aria-label="调整周期开始" type="datetime-local" value={form.period_start} onChange={(event) => setForm({ ...form, period_start: event.target.value })} /></Field><Field label="周期结束（自动）"><input aria-label="调整周期结束" type="datetime-local" value={end} readOnly /></Field><Field label="原因"><input aria-label="调整原因" value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} /></Field><Field label="幂等键"><input aria-label="调整幂等键" value={form.idempotency_key} onChange={(event) => setForm({ ...form, idempotency_key: event.target.value })} /></Field></div><button className="quota-primary-button" type="button" onClick={() => void onSave()} disabled={!form.owner_id || !form.amount || !form.reason || !form.idempotency_key}><History size={15} />记录调整</button><div className="quota-table-wrap"><table><thead><tr><th>归属</th><th>调整量</th><th>原因</th><th>操作人</th><th>时间</th></tr></thead><tbody>{adjustments.map((item) => <tr key={item.adjustment_id}><td>{item.owner_type} / {item.owner_id}</td><td>{displayMicro(item.amount_micro)}</td><td>{item.reason}</td><td>{item.actor_user_id}</td><td>{new Date(item.created_at).toLocaleString("zh-CN")}</td></tr>)}</tbody></table>{adjustments.length === 0 && <div className="quota-empty quota-empty-compact"><History size={18} />暂无手工调整记录</div>}</div></section>;
}

function QuotaControlCenter({ route, onRouteChange, policies, pricingRules, bindings, grants, adjustments, activePolicies, policyForm, setPolicyForm, bindingForm, setBindingForm, grantForm, setGrantForm, adjustmentForm, setAdjustmentForm, savePolicy, savePricing, retirePricing, saveBinding, saveGrant, saveAdjustment, publish, archivePolicy, retireBinding, revokeGrant }: { route: ControlRoute; onRouteChange: (route: ControlRoute) => void; policies: QuotaPolicy[]; pricingRules: QuotaPricingRule[]; bindings: QuotaBinding[]; grants: QuotaGrant[]; adjustments: QuotaAdjustment[]; activePolicies: QuotaPolicy[]; policyForm: PolicyForm; setPolicyForm: Dispatch<SetStateAction<PolicyForm>>; bindingForm: BindingForm; setBindingForm: Dispatch<SetStateAction<BindingForm>>; grantForm: AllocationForm; setGrantForm: Dispatch<SetStateAction<AllocationForm>>; adjustmentForm: AdjustmentForm; setAdjustmentForm: Dispatch<SetStateAction<AdjustmentForm>>; savePolicy: (policyId: string | null) => Promise<void>; savePricing: (form: PricingRuleForm) => Promise<void>; retirePricing: (pricingRuleId: string) => Promise<void>; saveBinding: () => Promise<void>; saveGrant: () => Promise<void>; saveAdjustment: () => Promise<void>; publish: (policyId: string) => Promise<void>; archivePolicy: (policyId: string) => Promise<void>; retireBinding: (bindingId: string) => Promise<void>; revokeGrant: (grantId: string) => Promise<void> }) {
  const content = route === "policies" ? <PolicyPanel policies={policies} form={policyForm} setForm={setPolicyForm} onSave={savePolicy} onPublish={publish} onArchive={archivePolicy} /> : route === "pricing" ? <PricingPanel rules={pricingRules} onSave={savePricing} onRetire={retirePricing} /> : route === "bindings" ? <BindingPanel bindings={bindings} activePolicies={activePolicies} form={bindingForm} setForm={setBindingForm} onSave={saveBinding} onRetire={retireBinding} /> : route === "grants" ? <GrantPanel grants={grants} form={grantForm} setForm={setGrantForm} onSave={saveGrant} onRevoke={revokeGrant} /> : <AdjustmentPanel adjustments={adjustments} form={adjustmentForm} setForm={setAdjustmentForm} onSave={saveAdjustment} />;
  return <><SubRouteNav value={route} onChange={(value) => onRouteChange(value as ControlRoute)} items={[{ value: "policies", label: "策略版本" }, { value: "pricing", label: "价格规则" }, { value: "bindings", label: "策略绑定" }, { value: "grants", label: "Grant 分配" }, { value: "adjustments", label: "手工调整" }]} /><div className="quota-route-panel">{content}</div></>;
}
function CreditForm({ onSaved, history }: { onSaved: (message: string) => Promise<void>; history: QuotaCreditOperation[] }) {
  const [mode, setMode] = useState<"gift" | "reset">("gift");
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [rolesLoading, setRolesLoading] = useState(true);
  const [form, setForm] = useState({ owner_type: "user" as CreditOwnerType, owner_id: "", bucket_type: "daily" as QuotaCreditOperationInput["bucket_type"], amount: "", period_start: localInput(), period_end: localInput(86_400_000), expires_at: "", reason: "", idempotency_key: "" });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let mounted = true;
    void api.listRoles().then((result) => { if (mounted) setRoles(result.items.filter((role) => role.status === "active")); }).catch(() => { if (mounted) setRoles([]); }).finally(() => { if (mounted) setRolesLoading(false); });
    return () => { mounted = false; };
  }, []);

  const save = async () => {
    setMessage("");
    if (!form.owner_id || !form.amount || !form.reason || !form.idempotency_key) { setMessage("请填写对象、额度、原因和幂等键。幂等键用于防止重复赠送。"); return; }
    const shared = { bucket_type: form.bucket_type, period_start: toUtc(form.period_start), period_end: toUtc(periodEndInput(form.period_start, form.bucket_type)), amount_micro: Number(form.amount), reason: form.reason, effective_from: new Date().toISOString(), expires_at: form.expires_at ? toUtc(form.expires_at) : null };
    setSaving(true);
    try {
      if (mode === "gift" && form.owner_type === "role") {
        const input: QuotaRoleCreditOperationInput = { ...shared, role_code: form.owner_id, idempotency_key: form.idempotency_key };
        const result = await api.giftQuotaRoleCredits(input);
        setMessage(`已向角色“${form.owner_id}”的 ${result.recipient_count} 个有效账号发放额度。`);
        await onSaved("角色额度赠送完成");
      } else {
        const input: QuotaCreditOperationInput = { ...shared, owner_type: form.owner_type as QuotaCreditOperationInput["owner_type"], owner_id: form.owner_id, idempotency_key: form.idempotency_key };
        await (mode === "gift" ? api.giftQuotaCredits(input) : api.resetQuotaCredits(input));
        setMessage(mode === "gift" ? "赠送已入账，相关账号的额度快照会同步更新。" : "重置已入账，旧的同范围 Grant 已按事务过期。");
        await onSaved(mode === "gift" ? "Credits 赠送完成" : "Credits 重置完成");
      }
    } catch (reason) { setMessage(reasonOf(reason)); } finally { setSaving(false); }
  };

  const ownerLabel = form.owner_type === "role" ? "角色" : "归属 ID";
  return <section className="quota-panel quota-route-content"><SectionHeading icon={Coins} title="Credits 赠送 / 重置" hint="赠送会增加指定周期额度；重置只替换旧 Grant，不清空消费记录。" action={<div className="quota-segmented"><button type="button" className={mode === "gift" ? "active" : ""} onClick={() => setMode("gift")}>赠送</button><button type="button" className={mode === "reset" ? "active" : ""} onClick={() => { setMode("reset"); if (form.owner_type === "role") setForm({ ...form, owner_type: "user", owner_id: "" }); }}>重置</button></div>} /><div className="quota-form-grid"><Field label="发放对象"><select aria-label="发放对象" value={form.owner_type} onChange={(event) => setForm({ ...form, owner_type: event.target.value as CreditOwnerType, owner_id: "" })}><option value="user">用户（单个）</option>{mode === "gift" && <option value="role">角色（批量）</option>}<option value="workspace">工作空间</option><option value="classroom">课堂</option></select></Field><Field label={ownerLabel}>{form.owner_type === "role" ? <select aria-label="角色" value={form.owner_id} disabled={rolesLoading || roles.length === 0} onChange={(event) => setForm({ ...form, owner_id: event.target.value })}><option value="">{rolesLoading ? "读取角色…" : roles.length === 0 ? "暂无有效角色" : "选择角色"}</option>{roles.map((role) => <option key={role.code} value={role.code}>{role.name} · {role.code}</option>)}</select> : <input aria-label="归属 ID" value={form.owner_id} onChange={(event) => setForm({ ...form, owner_id: event.target.value })} placeholder="填写用户 / 工作空间 / 课堂 ID" />}</Field><Field label="周期"><select aria-label="Credits周期" value={form.bucket_type} onChange={(event) => setForm({ ...form, bucket_type: event.target.value as typeof form.bucket_type })}><option value="daily">每日</option><option value="weekly">每周</option></select></Field><Field label="额度（μcredits）"><input aria-label="额度（μcredits）" inputMode="numeric" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} placeholder="例如 1000000" /></Field><Field label="周期开始"><input type="datetime-local" value={form.period_start} onChange={(event) => setForm({ ...form, period_start: event.target.value })} /></Field><Field label="周期结束（自动）"><input type="datetime-local" value={periodEndInput(form.period_start, form.bucket_type)} readOnly /></Field><Field label="过期时间（可选）"><input type="datetime-local" value={form.expires_at} onChange={(event) => setForm({ ...form, expires_at: event.target.value })} /></Field><Field label="幂等键"><input aria-label="幂等键" value={form.idempotency_key} onChange={(event) => setForm({ ...form, idempotency_key: event.target.value })} placeholder="业务单号或工单号" /></Field><Field label="原因" className="quota-field-wide"><input aria-label="原因" value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} placeholder="例如：新学期学生统一赠送" /></Field></div>{form.owner_type === "role" && <p className="quota-form-help">角色赠送会按当前有效角色成员逐人落账；后续新加入成员不会追溯获得本次额度。</p>}<button className="quota-primary-button" type="button" onClick={() => void save()} disabled={saving || !form.owner_id || !form.amount || !form.reason || !form.idempotency_key}><Coins size={15} />{saving ? "提交中…" : mode === "gift" ? "确认赠送" : "确认重置"}</button><Notice message={message} tone={message.includes("完成") || message.includes("入账") || message.includes("已向") ? "success" : "error"} /><div className="quota-history-section"><div className="quota-subsection-heading"><h3>最近操作</h3><span>保留赠送、重置和角色批次记录</span></div><div className="quota-table-wrap"><table><thead><tr><th>类型</th><th>对象</th><th>额度</th><th>状态</th><th>原因</th></tr></thead><tbody>{history.slice(0, 20).map((item) => <tr key={item.operation_id}><td>{item.operation_type === "reset" ? "重置" : "赠送"}</td><td>{item.owner_type} / {item.owner_id}{item.recipient_count != null && <small>{item.recipient_count} 个账号</small>}</td><td>{displayMicro(item.amount_micro)}</td><td><span className={`quota-status-pill ${item.status}`}>{item.status}</span></td><td>{item.reason}</td></tr>)}</tbody></table>{history.length === 0 && <div className="quota-empty quota-empty-compact"><History size={18} />暂无 Credits 操作记录</div>}</div></div></section>;
}

function BillingCenter({ billing, onRefresh }: { billing: QuotaBillingRecord[]; onRefresh: () => Promise<void> }) {
  const [form, setForm] = useState({ provider: "", statement_id: "", operation_id: "", billed_at: localInput(), credits: "", idempotency_key: "" });
  const [repair, setRepair] = useState({ reason: "核对 Provider 账单后修复", idempotency_key: "" });
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const submit = async () => {
    setMessage("");
    if (!form.provider || !form.statement_id || !form.operation_id || !form.idempotency_key) { setMessage("Provider、账单号、operation_id 和幂等键均为必填项。"); return; }
    setSaving(true);
    try { const result = await api.reconcileQuotaBilling({ provider: form.provider, statement_id: form.statement_id, operation_id: form.operation_id, billed_at: toUtc(form.billed_at), billed_credits_micro: form.credits ? Number(form.credits) : null, billed_tokens: {}, idempotency_key: form.idempotency_key }); setMessage(`已处理 ${result.total} 条账单：匹配 ${result.matched}，差异 ${result.discrepancies}，未匹配 ${result.unmatched}。`); await onRefresh(); } catch (reason) { setMessage(reasonOf(reason)); } finally { setSaving(false); }
  };
  const repairBilling = async (item: QuotaBillingRecord) => {
    if (!repair.reason.trim() || !repair.idempotency_key.trim()) { setMessage("修复账单前请填写修复原因和幂等键。"); return; }
    try { await api.repairQuotaBilling(item.billing_id, repair.reason.trim(), repair.idempotency_key.trim()); setRepair({ ...repair, idempotency_key: "" }); await onRefresh(); } catch (error) { setMessage(reasonOf(error)); }
  };
  return <section className="quota-panel quota-route-content"><SectionHeading icon={FileCheck2} title="Provider 账单对账" hint="按 provider + statement_id 幂等接收账单，并将差异定位到具体 UsageEvent。" /><div className="quota-form-grid"><Field label="Provider"><input value={form.provider} onChange={(event) => setForm({ ...form, provider: event.target.value })} placeholder="openai" /></Field><Field label="账单号"><input value={form.statement_id} onChange={(event) => setForm({ ...form, statement_id: event.target.value })} /></Field><Field label="operation_id"><input value={form.operation_id} onChange={(event) => setForm({ ...form, operation_id: event.target.value })} /></Field><Field label="Provider Credits"><input inputMode="numeric" value={form.credits} onChange={(event) => setForm({ ...form, credits: event.target.value })} placeholder="未知可留空" /></Field><Field label="账单时间"><input type="datetime-local" value={form.billed_at} onChange={(event) => setForm({ ...form, billed_at: event.target.value })} /></Field><Field label="幂等键"><input value={form.idempotency_key} onChange={(event) => setForm({ ...form, idempotency_key: event.target.value })} /></Field></div><button className="quota-primary-button" type="button" onClick={() => void submit()} disabled={saving}><FileCheck2 size={15} />{saving ? "对账中…" : "提交账单对账"}</button><div className="quota-inline-tool-row"><Field label="账单修复原因"><input value={repair.reason} onChange={(event) => setRepair({ ...repair, reason: event.target.value })} /></Field><Field label="修复幂等键"><input value={repair.idempotency_key} onChange={(event) => setRepair({ ...repair, idempotency_key: event.target.value })} placeholder="每次修复唯一" /></Field></div><Notice message={message} tone={message.startsWith("已处理") ? "success" : "error"} /><div className="quota-table-wrap"><table><thead><tr><th>Provider / 账单</th><th>Operation</th><th>Provider Credits</th><th>本地</th><th>差异</th><th>状态</th><th>操作</th></tr></thead><tbody>{billing.map((item) => <tr key={item.billing_id}><td>{item.provider}<small>{item.statement_id}</small></td><td><code>{item.operation_id}</code></td><td>{displayMicro(item.billed_credits_micro)}</td><td>{displayMicro(item.local_credits_micro)}</td><td className={item.difference_micro ? "quota-value-warning" : ""}>{displayMicro(item.difference_micro)}</td><td><span className={`quota-status-pill ${item.status}`}>{item.status}</span></td><td>{item.status === "discrepancy" && <button type="button" onClick={() => void repairBilling(item)}>修复</button>}</td></tr>)}</tbody></table>{billing.length === 0 && <div className="quota-empty quota-empty-compact"><FileCheck2 size={18} />暂无账单记录</div>}</div></section>;
}

function AlertCenter({ alerts, onRefresh }: { alerts: QuotaAlert[]; onRefresh: () => Promise<void> }) {
  const [reason, setReason] = useState("已核对用量和 Provider 账单");
  const [message, setMessage] = useState("");
  const update = async (item: QuotaAlert, status: "acknowledged" | "resolved") => {
    try { await api.updateQuotaAlert(item.alert_id, status, reason.trim() || "开发者处理"); setMessage(""); await onRefresh(); } catch (error) { setMessage(reasonOf(error)); }
  };
  return <section className="quota-panel quota-route-content"><SectionHeading icon={AlertTriangle} title="告警中心" hint="异常消费告警可确认和解决；原始 UsageEvent 与流水不会被覆盖。" action={<Field label="操作原因"><input className="quota-compact-input" value={reason} onChange={(event) => setReason(event.target.value)} /></Field>} /><Notice message={message} /><div className="quota-alert-list">{alerts.map((item) => <article className="quota-alert-row" key={item.alert_id}><div className={`quota-alert-dot ${item.severity}`} /><div className="quota-alert-main"><strong>{item.owner_type} / {item.owner_id} · 用量突增</strong><small>{item.window_start.slice(0, 10)} · 基线 {displayMicro(item.baseline_micro)} → 实际 {displayMicro(item.actual_micro)} · {item.threshold_multiplier}x</small></div><span className={`quota-status-pill ${item.status}`}>{item.status}</span><div className="quota-alert-actions">{item.status === "open" && <button type="button" onClick={() => void update(item, "acknowledged")}>确认</button>}{item.status !== "resolved" && <button type="button" onClick={() => void update(item, "resolved")}>解决</button>}</div></article>)}{alerts.length === 0 && <div className="quota-empty quota-empty-compact"><Check size={18} />当前没有待处理告警</div>}</div></section>;
}

function OperationsCenter({ route, onRouteChange, billing, alerts, creditOperations, onRefresh, onSaved }: { route: OperationsRoute; onRouteChange: (route: OperationsRoute) => void; billing: QuotaBillingRecord[]; alerts: QuotaAlert[]; creditOperations: QuotaCreditOperation[]; onRefresh: () => Promise<void>; onSaved: (message: string) => Promise<void> }) {
  const content = route === "credits" ? <CreditForm history={creditOperations} onSaved={onSaved} /> : route === "billing" ? <BillingCenter billing={billing} onRefresh={onRefresh} /> : <AlertCenter alerts={alerts} onRefresh={onRefresh} />;
  return <><SubRouteNav value={route} onChange={(value) => onRouteChange(value as OperationsRoute)} items={[{ value: "credits", label: "Credits 赠送 / 重置" }, { value: "billing", label: "账单对账" }, { value: "alerts", label: "告警中心" }]} /><div className="quota-route-panel">{content}</div></>;
}

function RecoveryCenter({ route, onRouteChange, rollups, buckets, archiveBatches, onRefresh }: { route: RecoveryRoute; onRouteChange: (route: RecoveryRoute) => void; rollups: QuotaDailyRollup[]; buckets: QuotaBucketCandidate[]; archiveBatches: QuotaArchiveBatch[]; onRefresh: () => Promise<void> }) {
  const [archive, setArchive] = useState({ before: localInput(-90 * 86_400_000), batch: "10000" });
  const [bucket, setBucket] = useState({ id: "", reason: "", key: "" });
  const [replay, setReplay] = useState<QuotaBucketReplay | null>(null);
  const [message, setMessage] = useState("");
  const doArchive = async () => { try { const result = await api.archiveQuotaUsage(toUtc(archive.before), Number(archive.batch)); setMessage(result.batch_id ? `归档批次 ${result.batch_id.slice(0, 8)} 已完成，共标记 ${result.archived_events} 条事件。` : "没有符合条件的新事件，无需归档。"); await onRefresh(); } catch (error) { setMessage(reasonOf(error)); } };
  const doPurge = async () => { if (!window.confirm("清理后将永久删除已归档的原始 UsageEvent，Ledger 和归档批次清单会保留。确定继续吗？")) return; try { const result = await api.purgeQuotaUsage(toUtc(archive.before), Number(archive.batch)); setMessage(`已清理 ${result.deleted_events} 条归档事件，原始流水已永久删除。`); await onRefresh(); } catch (error) { setMessage(reasonOf(error)); } };
  const doReplay = async () => { if (!bucket.id) { setMessage("请输入 Bucket ID。"); return; } try { setReplay(await api.replayQuotaBucket(bucket.id)); setMessage("Ledger 重放完成，请先确认漂移再执行修复。"); } catch (error) { setMessage(reasonOf(error)); } };
  const doRepair = async () => { if (!bucket.id || !bucket.reason || !bucket.key) { setMessage("Bucket ID、修复原因和幂等键均为必填项。"); return; } try { setReplay(await api.repairQuotaBucket(bucket.id, bucket.reason, bucket.key)); setMessage("Bucket 已按 Ledger 重放结果修复，原始流水保持不变。"); await onRefresh(); } catch (error) { setMessage(reasonOf(error)); } };
  const ledgerPanel = <section className="quota-panel quota-route-content"><SectionHeading icon={Database} title="Ledger 重放与 UsageEvent 归档" hint="归档后不再进入日常统计；原始事件保留用于对账、审计和余额重放。" /><div className="quota-subsection"><h3>非破坏性归档</h3><div className="quota-form-grid"><Field label="归档此前事件"><input type="datetime-local" value={archive.before} onChange={(event) => setArchive({ ...archive, before: event.target.value })} /></Field><Field label="批量大小"><input inputMode="numeric" value={archive.batch} onChange={(event) => setArchive({ ...archive, batch: event.target.value })} /></Field></div><div className="quota-button-row"><button type="button" onClick={() => void doArchive()}><Database size={14} />开始归档</button><button type="button" className="quota-danger-button" onClick={() => void doPurge()}>清理已归档</button></div><small className="quota-form-help">清理是永久删除操作，只处理已归档且已结算、没有 Provider 账单引用的事件；Ledger 与归档批次记录会保留。</small></div><div className="quota-subsection"><h3>Bucket 恢复</h3><div className="quota-form-grid"><Field label="选择 Bucket" className="quota-field-wide"><select aria-label="选择 Bucket" value={bucket.id} onChange={(event) => setBucket({ ...bucket, id: event.target.value })}><option value="">选择需要检查的 Bucket</option>{buckets.map((item) => <option key={item.bucket_id} value={item.bucket_id}>{item.owner_type} / {item.owner_id} · {item.bucket_type} · {item.period_start.slice(0, 10)} · {item.bucket_id.slice(0, 8)}</option>)}</select></Field><Field label="修复原因"><input value={bucket.reason} onChange={(event) => setBucket({ ...bucket, reason: event.target.value })} /></Field><Field label="修复幂等键"><input value={bucket.key} onChange={(event) => setBucket({ ...bucket, key: event.target.value })} /></Field></div><div className="quota-button-row"><button type="button" onClick={() => void doReplay()}><RotateCcw size={14} />重放 Ledger</button><button className="quota-primary-button" type="button" onClick={() => void doRepair()}><ShieldCheck size={14} />确认修复余额</button></div>{replay && <div className={`quota-replay-result ${replay.needs_repair ? "needs-repair" : "healthy"}`}><strong>{replay.needs_repair ? "检测到物化余额漂移" : "Bucket 与 Ledger 一致"}</strong><span>Consumed {displayMicro(replay.stored_consumed_micro)} → {displayMicro(replay.expected_consumed_micro)} · Reserved {displayMicro(replay.stored_reserved_micro)} → {displayMicro(replay.expected_reserved_micro)}</span><small>{replay.ledger_entries} 条 Ledger · over_limit={String(replay.expected_over_limit)}</small></div>}</div><div className="quota-subsection"><h3>归档历史</h3><div className="quota-table-wrap"><table><thead><tr><th>批次</th><th>截止时间</th><th>事件数</th><th>操作人</th><th>状态</th></tr></thead><tbody>{archiveBatches.map((item) => <tr key={item.batch_id}><td><code>{item.batch_id.slice(0, 8)}</code></td><td>{new Date(item.cutoff_at).toLocaleString("zh-CN")}</td><td>{item.event_count}</td><td>{item.actor_user_id || "系统"}</td><td><span className={`quota-status-pill ${item.status}`}>{item.status}</span></td></tr>)}</tbody></table>{archiveBatches.length === 0 && <div className="quota-empty quota-empty-compact"><Database size={18} />暂无归档批次</div>}</div></div><Notice message={message} tone={message.includes("完成") || message.includes("修复") ? "success" : "error"} /></section>;
  const rollupPanel = <section className="quota-panel quota-route-content"><SectionHeading icon={History} title="Daily Rollup" hint="查询加速表，不作为权威账本；可按用户和工作空间核对趋势。" /><div className="quota-table-wrap"><table><thead><tr><th>日期</th><th>用户 / 工作空间</th><th>用途 / 模型</th><th>事件</th><th>Credits</th><th>状态</th></tr></thead><tbody>{rollups.map((item, index) => <tr key={`${item.rollup_date}-${item.user_id}-${item.provider_model}-${index}`}><td>{item.rollup_date}</td><td>{item.user_id}<small>{item.workspace_id || "无工作空间"}</small></td><td>{item.purpose}<small>{item.provider} / {item.provider_model}</small></td><td>{item.events}</td><td>{displayMicro(item.priced_credits_micro)}</td><td>{item.pending_events || item.unavailable_events ? <span className="quota-status-pill pending">待处理 {item.pending_events + item.unavailable_events}</span> : <span className="quota-status-pill matched">完整</span>}</td></tr>)}</tbody></table>{rollups.length === 0 && <div className="quota-empty quota-empty-compact"><History size={18} />暂无 Rollup 数据</div>}</div></section>;
  return <><SubRouteNav value={route} onChange={(value) => onRouteChange(value as RecoveryRoute)} items={[{ value: "ledger", label: "Ledger 重放与归档" }, { value: "rollups", label: "Daily Rollup" }]} /><div className="quota-route-panel">{route === "ledger" ? ledgerPanel : rollupPanel}</div></>;
}

export function QuotaManagementPage() {
  const [tab, setTab] = useState<Tab>("control");
  const [controlRoute, setControlRoute] = useState<ControlRoute>("policies");
  const [operationsRoute, setOperationsRoute] = useState<OperationsRoute>("credits");
  const [recoveryRoute, setRecoveryRoute] = useState<RecoveryRoute>("ledger");
  const [policies, setPolicies] = useState<QuotaPolicy[]>([]);
  const [pricingRules, setPricingRules] = useState<QuotaPricingRule[]>([]);
  const [bindings, setBindings] = useState<QuotaBinding[]>([]);
  const [grants, setGrants] = useState<QuotaGrant[]>([]);
  const [adjustments, setAdjustments] = useState<QuotaAdjustment[]>([]);
  const [creditOperations, setCreditOperations] = useState<QuotaCreditOperation[]>([]);
  const [billing, setBilling] = useState<QuotaBillingRecord[]>([]);
  const [alerts, setAlerts] = useState<QuotaAlert[]>([]);
  const [rollups, setRollups] = useState<QuotaDailyRollup[]>([]);
  const [buckets, setBuckets] = useState<QuotaBucketCandidate[]>([]);
  const [archiveBatches, setArchiveBatches] = useState<QuotaArchiveBatch[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [policyForm, setPolicyForm] = useState<PolicyForm>({ code: "student", version: "", name: "", daily: "", weekly: "", request: "", concurrency: "2", overdraft: "0", profiles: "", unlimited: false });
  const [bindingForm, setBindingForm] = useState<BindingForm>({ subject_type: "role", subject_id: "student", policy_id: "", priority: "10" });
  const [grantForm, setGrantForm] = useState<AllocationForm>({ owner_type: "user", owner_id: "", bucket_type: "daily", amount: "", period_start: localInput(), period_end: localInput(86_400_000), expires_at: "", idempotency_key: "", reason: "" });
  const [adjustmentForm, setAdjustmentForm] = useState<AdjustmentForm>({ owner_type: "user", owner_id: "", bucket_type: "daily", amount: "", period_start: localInput(), period_end: localInput(86_400_000), expires_at: "", idempotency_key: "", reason: "" });
  const load = useCallback(async () => {
    setError("");
    const results = await Promise.allSettled([
      api.listQuotaPolicies(),
      api.listQuotaPricingRules(),
      api.listQuotaBindings(),
      api.listQuotaGrants(),
      api.listQuotaAdjustments(),
      api.listQuotaCreditOperations(),
      api.listQuotaBilling(),
      api.listQuotaAlerts(),
      api.listQuotaDailyRollups(dateInput(-30), dateInput(1)),
      api.listQuotaBuckets(),
      api.listQuotaArchiveBatches(),
    ]);
    const [policyResult, pricingResult, bindingResult, grantResult, adjustmentResult, creditResult, billingResult, alertResult, rollupResult, bucketResult, archiveResult] = results;
    if (policyResult.status === "fulfilled") {
      setPolicies(policyResult.value.items);
      setBindingForm((current) => ({ ...current, policy_id: current.policy_id || policyResult.value.items.find((item) => item.status === "active")?.policy_id || "" }));
    }
    if (pricingResult.status === "fulfilled") setPricingRules(pricingResult.value.items);
    if (bindingResult.status === "fulfilled") setBindings(bindingResult.value.items);
    if (grantResult.status === "fulfilled") setGrants(grantResult.value.items);
    if (adjustmentResult.status === "fulfilled") setAdjustments(adjustmentResult.value.items);
    if (creditResult.status === "fulfilled") setCreditOperations(creditResult.value.items);
    if (billingResult.status === "fulfilled") setBilling(billingResult.value.items);
    if (alertResult.status === "fulfilled") setAlerts(alertResult.value.items);
    if (rollupResult.status === "fulfilled") setRollups(rollupResult.value.items);
    if (bucketResult.status === "fulfilled") setBuckets(bucketResult.value.items);
    if (archiveResult.status === "fulfilled") setArchiveBatches(archiveResult.value.items);
    const failedCount = results.filter((result) => result.status === "rejected").length;
    if (failedCount > 0) setError(`部分管理数据加载失败，已保留可用模块。失败 ${failedCount} 项，请刷新重试。`);
    setLoading(false);
  }, []);
  useEffect(() => { queueMicrotask(() => void load()); }, [load]);
  const activePolicies = useMemo(() => policies.filter((item) => item.status === "active"), [policies]);
  const savePolicy = async (policyId: string | null) => { try { const policyValues = { code: policyForm.code, version: policyForm.version, name: policyForm.name, request_limit_micro: policyForm.request ? Number(policyForm.request) : null, daily_limit_micro: policyForm.daily ? Number(policyForm.daily) : null, weekly_limit_micro: policyForm.weekly ? Number(policyForm.weekly) : null, concurrency_limit: policyForm.concurrency ? Number(policyForm.concurrency) : null, max_overdraft_micro: policyForm.overdraft ? Number(policyForm.overdraft) : 0, allowed_model_profiles: policyForm.profiles.split(",").map((item) => item.trim()).filter(Boolean), unlimited: policyForm.unlimited }; if (policyId) { await api.updateQuotaPolicy(policyId, policyValues); setMessage("策略草稿已保存"); } else { await api.createQuotaPolicy({ ...policyValues, effective_from: new Date().toISOString(), effective_until: null, status: "draft" }); setMessage("策略草稿已创建"); } await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const savePricing = async (form: PricingRuleForm) => { try { await api.createQuotaPricingRule({ pricing_key: form.pricing_key.trim(), version: form.version.trim(), effective_from: toUtc(form.effective_from), effective_until: form.effective_until ? toUtc(form.effective_until) : null, ordinary_input_credits_micro_per_million_tokens: Number(form.ordinary_input), cached_input_credits_micro_per_million_tokens: Number(form.cached_input), cache_write_credits_micro_per_million_tokens: Number(form.cache_write), output_credits_micro_per_million_tokens: Number(form.output), reasoning_output_credits_micro_per_million_tokens: form.reasoning_output ? Number(form.reasoning_output) : null }); setMessage("价格规则已创建"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const retirePricing = async (pricingRuleId: string) => { try { await api.retireQuotaPricingRule(pricingRuleId); setMessage("价格规则已停用"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const saveBinding = async () => { try { await api.bindQuotaPolicy({ subject_type: bindingForm.subject_type, subject_id: bindingForm.subject_id, policy_id: bindingForm.policy_id, priority: Number(bindingForm.priority), effective_from: new Date().toISOString(), effective_until: null }); setMessage("策略绑定已发布"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const saveGrant = async () => { try { await api.createQuotaGrant({ owner_type: grantForm.owner_type, owner_id: grantForm.owner_id, bucket_type: grantForm.bucket_type, period_start: toUtc(grantForm.period_start), period_end: toUtc(periodEndInput(grantForm.period_start, grantForm.bucket_type as "daily" | "weekly")), allocated_micro: Number(grantForm.amount), source_type: "grant", source_id: null, effective_from: new Date().toISOString(), expires_at: grantForm.expires_at ? toUtc(grantForm.expires_at) : null, reason: grantForm.reason, idempotency_key: grantForm.idempotency_key }); setMessage("额度 Grant 已分配"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const saveAdjustment = async () => { try { await api.createQuotaAdjustment({ owner_type: adjustmentForm.owner_type, owner_id: adjustmentForm.owner_id, bucket_type: adjustmentForm.bucket_type, period_start: toUtc(adjustmentForm.period_start), period_end: toUtc(periodEndInput(adjustmentForm.period_start, adjustmentForm.bucket_type as "daily" | "weekly")), amount_micro: Number(adjustmentForm.amount), reason: adjustmentForm.reason, idempotency_key: adjustmentForm.idempotency_key }); setMessage("手工调整已记录"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const publish = async (policyId: string) => { try { await api.publishQuotaPolicy(policyId); setMessage("策略已发布"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const archivePolicy = async (policyId: string) => { try { await api.archiveQuotaPolicy(policyId); setMessage("策略草稿已归档"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const retireBinding = async (bindingId: string) => { try { await api.retireQuotaBinding(bindingId); setMessage("策略绑定已结束"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  const revokeGrant = async (grantId: string) => { try { await api.revokeQuotaGrant(grantId, `revoke-${grantId}`); setMessage("Grant 已撤销"); await load(); } catch (reason) { setError(reasonOf(reason)); } };
  if (loading) return <main className="quota-page developer-quota-page"><div className="quota-loading"><RefreshCw className="spin" />正在读取额度控制面板…</div></main>;
  return <main className="quota-page developer-quota-page"><header className="quota-page-header developer-quota-hero"><div><span className="quota-eyebrow">DEVELOPER CONTROL PLANE</span><h1>额度管理</h1><p>统一管理策略、用户与工作空间分配、Credits 运营和账务恢复。每次变更都保留原因、操作人和幂等键。</p></div><div className="quota-header-actions"><span className="quota-live-indicator"><i />快照服务正常</span><button type="button" onClick={() => void load()} disabled={loading}><RefreshCw size={16} />刷新数据</button></div></header>{error && <Notice message={error} />}{message && <Notice message={message} tone="success" />}<section className="quota-management-summary"><article><WalletCards size={18} /><span>有效策略</span><strong>{activePolicies.length}</strong><small>版本化发布</small></article><article><Coins size={18} /><span>Grant 记录</span><strong>{grants.length}</strong><small>含过期和撤销</small></article><article><FileCheck2 size={18} /><span>待对账</span><strong>{billing.filter((item) => item.status !== "matched" && item.status !== "repaired").length}</strong><small>Provider 账单</small></article><article><AlertTriangle size={18} /><span>开放告警</span><strong>{alerts.filter((item) => item.status !== "resolved").length}</strong><small>需要运营处理</small></article></section><nav className="quota-management-tabs" aria-label="额度管理模块"><button type="button" className={tab === "control" ? "active" : ""} onClick={() => setTab("control")}><ShieldCheck size={16} />策略与分配</button><button type="button" className={tab === "operations" ? "active" : ""} onClick={() => setTab("operations")}><Sparkles size={16} />运营与对账</button><button type="button" className={tab === "recovery" ? "active" : ""} onClick={() => setTab("recovery")}><Database size={16} />恢复与归档</button></nav>{tab === "operations" ? <OperationsCenter route={operationsRoute} onRouteChange={setOperationsRoute} billing={billing} alerts={alerts} creditOperations={creditOperations} onRefresh={load} onSaved={async (item) => { setMessage(item); await load(); }} /> : tab === "recovery" ? <RecoveryCenter route={recoveryRoute} onRouteChange={setRecoveryRoute} rollups={rollups} buckets={buckets} archiveBatches={archiveBatches} onRefresh={load} /> : <QuotaControlCenter route={controlRoute} onRouteChange={setControlRoute} policies={policies} pricingRules={pricingRules} bindings={bindings} grants={grants} adjustments={adjustments} activePolicies={activePolicies} policyForm={policyForm} setPolicyForm={setPolicyForm} bindingForm={bindingForm} setBindingForm={setBindingForm} grantForm={grantForm} setGrantForm={setGrantForm} adjustmentForm={adjustmentForm} setAdjustmentForm={setAdjustmentForm} savePolicy={savePolicy} savePricing={savePricing} retirePricing={retirePricing} saveBinding={saveBinding} saveGrant={saveGrant} saveAdjustment={saveAdjustment} publish={publish} archivePolicy={archivePolicy} retireBinding={retireBinding} revokeGrant={revokeGrant} />}</main>;
}

export default QuotaManagementPage;
