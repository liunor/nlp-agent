import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { QuotaManagementPage } from "./QuotaManagementPage";

const methods = vi.hoisted(() => ({
  listQuotaPolicies: vi.fn(),
  listQuotaBindings: vi.fn(),
  listQuotaGrants: vi.fn(),
  listQuotaAdjustments: vi.fn(),
  listQuotaCreditOperations: vi.fn(),
  listQuotaBilling: vi.fn(),
  listQuotaAlerts: vi.fn(),
  listQuotaDailyRollups: vi.fn(),
  listQuotaBuckets: vi.fn(),
  listQuotaArchiveBatches: vi.fn(),
  purgeQuotaUsage: vi.fn(),
  createQuotaPolicy: vi.fn(),
  updateQuotaPolicy: vi.fn(),
  archiveQuotaPolicy: vi.fn(),
  publishQuotaPolicy: vi.fn(),
  bindQuotaPolicy: vi.fn(),
  retireQuotaBinding: vi.fn(),
  getQuotaPolicy: vi.fn(),
  getQuotaBinding: vi.fn(),
  getQuotaGrant: vi.fn(),
  getQuotaAdjustment: vi.fn(),
  createQuotaGrant: vi.fn(),
  createQuotaAdjustment: vi.fn(),
  revokeQuotaGrant: vi.fn(),
  listRoles: vi.fn(),
  giftQuotaCredits: vi.fn(),
  giftQuotaRoleCredits: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({ api: methods }));

describe("QuotaManagementPage", () => {
  beforeEach(() => {
    Object.values(methods).forEach((method) => method.mockReset());
    methods.listQuotaPolicies.mockResolvedValue({ items: [] });
    methods.listQuotaBindings.mockResolvedValue({ items: [] });
    methods.listQuotaGrants.mockResolvedValue({ items: [] });
    methods.listQuotaAdjustments.mockResolvedValue({ items: [] });
    methods.listQuotaCreditOperations.mockResolvedValue({ items: [] });
    methods.listQuotaBilling.mockResolvedValue({ items: [] });
    methods.listQuotaAlerts.mockResolvedValue({ items: [] });
    methods.listQuotaDailyRollups.mockResolvedValue({ items: [] });
    methods.listQuotaBuckets.mockResolvedValue({ items: [] });
    methods.listQuotaArchiveBatches.mockResolvedValue({ items: [] });
    methods.purgeQuotaUsage.mockResolvedValue({ purged_events: 2, deleted_events: 2, cutoff_at: "2026-08-01T00:00:00Z" });
    methods.createQuotaPolicy.mockResolvedValue({});
    methods.updateQuotaPolicy.mockResolvedValue({});
    methods.archiveQuotaPolicy.mockResolvedValue({});
    methods.publishQuotaPolicy.mockResolvedValue({});
    methods.bindQuotaPolicy.mockResolvedValue({});
    methods.retireQuotaBinding.mockResolvedValue({});
    methods.createQuotaGrant.mockResolvedValue({});
    methods.createQuotaAdjustment.mockResolvedValue({});
    methods.revokeQuotaGrant.mockResolvedValue({});
    methods.listRoles.mockResolvedValue({ items: [{ code: "student", name: "学生", description: "", status: "active", is_builtin: true }] });
    methods.giftQuotaRoleCredits.mockResolvedValue({ operation_type: "gift", target_type: "role", target_id: "student", recipient_count: 3, items: [], idempotency_key: "role-gift-1" });
  });

  it("loads operational data and exposes one fixed subroute at a time", async () => {
    render(<QuotaManagementPage />);

    await waitFor(() => expect(methods.listQuotaBilling).toHaveBeenCalled());
    expect(methods.listQuotaAlerts).toHaveBeenCalled();
    expect(methods.listQuotaDailyRollups).toHaveBeenCalled();

    fireEvent.click(await screen.findByRole("button", { name: "运营与对账" }));
    expect(screen.getByRole("heading", { name: "Credits 赠送 / 重置" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Provider 账单对账" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "账单对账" }));
    expect(screen.getByRole("heading", { name: "Provider 账单对账" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Credits 赠送 / 重置" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "告警中心" }));
    expect(screen.getByRole("heading", { name: "告警中心" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "恢复与归档" }));
    expect(screen.getByRole("heading", { name: "Ledger 重放与 UsageEvent 归档" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Daily Rollup" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Daily Rollup" }));
    expect(screen.getByRole("heading", { name: "Daily Rollup" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Ledger 重放与 UsageEvent 归档" })).not.toBeInTheDocument();
  });

  it("exposes an explicit purge action for already archived usage events", async () => {
    const confirm = vi.fn().mockReturnValue(true);
    vi.stubGlobal("confirm", confirm);
    render(<QuotaManagementPage />);

    await waitFor(() => expect(methods.listQuotaPolicies).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "恢复与归档" }));
    fireEvent.click(screen.getByRole("button", { name: "清理已归档" }));

    await waitFor(() => expect(methods.purgeQuotaUsage).toHaveBeenCalled());
    expect(confirm).toHaveBeenCalled();
    vi.unstubAllGlobals();
  });

  it("switches the strategy and allocation subroutes without rendering all panels together", async () => {
    render(<QuotaManagementPage />);

    await waitFor(() => expect(methods.listQuotaPolicies).toHaveBeenCalled());
    expect(screen.getByRole("heading", { name: "策略版本" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "策略绑定" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "策略绑定" }));
    expect(screen.getByRole("heading", { name: "策略绑定" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "策略版本" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Grant 分配" }));
    expect(screen.getByRole("heading", { name: "Quota Grant 分配" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "手工调整" }));
    expect(screen.getByRole("heading", { name: "手工调整" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Quota Grant 分配" })).not.toBeInTheDocument();
  });

  it("offers role gifting and submits one batch operation for the selected role", async () => {
    render(<QuotaManagementPage />);

    await waitFor(() => expect(methods.listQuotaPolicies).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "运营与对账" }));
    await waitFor(() => expect(methods.listRoles).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("发放对象"), { target: { value: "role" } });
    fireEvent.change(screen.getByLabelText("角色"), { target: { value: "student" } });
    fireEvent.change(screen.getByLabelText("额度（μcredits）"), { target: { value: "300000" } });
    fireEvent.change(screen.getByLabelText("原因"), { target: { value: "新学期学生统一赠送" } });
    fireEvent.change(screen.getAllByLabelText("幂等键")[0], { target: { value: "role-gift-1" } });
    fireEvent.click(screen.getByRole("button", { name: "确认赠送" }));

    await waitFor(() => expect(methods.giftQuotaRoleCredits).toHaveBeenCalledWith(expect.objectContaining({
      role_code: "student",
      amount_micro: 300000,
      reason: "新学期学生统一赠送",
      idempotency_key: "role-gift-1",
    })));
    expect(methods.giftQuotaCredits).not.toHaveBeenCalled();
  });

  it("keeps overdraft and model profile restrictions configurable", async () => {
    render(<QuotaManagementPage />);

    await waitFor(() => expect(methods.listQuotaPolicies).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("版本"), { target: { value: "2026.08.30" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "学生策略" } });
    fireEvent.change(screen.getByLabelText("有限透支"), { target: { value: "5000" } });
    fireEvent.change(screen.getByLabelText("允许模型 Profile"), { target: { value: "economy, premium" } });
    fireEvent.click(screen.getByRole("button", { name: "创建策略草稿" }));

    await waitFor(() => expect(methods.createQuotaPolicy).toHaveBeenCalledWith(expect.objectContaining({
      max_overdraft_micro: 5000,
      allowed_model_profiles: ["economy", "premium"],
      unlimited: false,
    })));
  });

  it("rejects invalid policy numbers before submitting", async () => {
    render(<QuotaManagementPage />);

    await waitFor(() => expect(methods.listQuotaPolicies).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("版本"), { target: { value: "2026.08.30" } });
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "学生策略" } });
    fireEvent.change(screen.getByLabelText("每日上限"), { target: { value: "abc" } });

    expect(screen.getByText("每日上限必须是非负整数")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "创建策略草稿" }));
    expect(methods.createQuotaPolicy).not.toHaveBeenCalled();
  });

  it("supports editing and archiving draft policies from the list", async () => {
    const draft = {
      policy_id: "policy-draft-1",
      code: "student",
      version: "1",
      name: "学生策略",
      status: "draft",
      request_limit_micro: 5000,
      daily_limit_micro: 100000,
      weekly_limit_micro: 500000,
      concurrency_limit: 2,
      max_overdraft_micro: 0,
      allowed_model_profiles: ["economy"],
      unlimited: false,
      effective_from: "2026-09-01T00:00:00Z",
      effective_until: null,
      created_by: "developer-1",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    };
    methods.listQuotaPolicies.mockResolvedValue({ items: [draft] });
    render(<QuotaManagementPage />);

    await waitFor(() => expect(screen.getByText("学生策略")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "学生策略（调整版）" } });
    fireEvent.click(screen.getByRole("button", { name: "保存策略草稿" }));
    await waitFor(() => expect(methods.updateQuotaPolicy).toHaveBeenCalledWith("policy-draft-1", expect.objectContaining({ name: "学生策略（调整版）" })));

    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    await waitFor(() => expect(methods.archiveQuotaPolicy).toHaveBeenCalledWith("policy-draft-1"));
  });

  it("supports ending an active policy binding", async () => {
    const binding = {
      binding_id: "binding-1",
      subject_type: "role",
      subject_id: "student",
      policy_id: "policy-1",
      policy_code: "student",
      policy_version: "1",
      priority: 10,
      status: "active",
      effective_from: "2026-09-01T00:00:00Z",
      effective_until: null,
    };
    methods.listQuotaBindings.mockResolvedValue({ items: [binding] });
    render(<QuotaManagementPage />);

    await waitFor(() => expect(methods.listQuotaBindings).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "策略绑定" }));
    fireEvent.click(screen.getByRole("button", { name: "结束绑定" }));
    await waitFor(() => expect(methods.retireQuotaBinding).toHaveBeenCalledWith("binding-1"));
  });
});
