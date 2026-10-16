import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RoleQuotaPanel } from "./RoleQuotaPanel";

const methods = vi.hoisted(() => ({ listQuotaPolicies: vi.fn(), listQuotaBindings: vi.fn(), listRoles: vi.fn(),
  createQuotaPolicy: vi.fn(), publishQuotaPolicy: vi.fn(), bindQuotaPolicy: vi.fn() }));
vi.mock("@/platform/http/api", () => ({ api: methods }));

const roles = ["guest", "student", "teacher", "developer"].map((code) => ({ code, status: "active" }));
const policy = { policy_id: "previous", code: "role-teacher", version: "1", status: "active", daily_limit_micro: 2250000,
  weekly_limit_micro: 11250000, request_limit_micro: 2000000, concurrency_limit: 1, max_overdraft_micro: 0,
  allowed_model_profiles: [], unlimited: false };
const binding = { binding_id: "teacher-binding", subject_type: "role", subject_id: "teacher", policy_id: "previous",
  status: "active", priority: 30, effective_from: "2020-01-01T00:00:00Z", effective_until: null };

describe("RoleQuotaPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    methods.listQuotaPolicies.mockResolvedValue({ items: [] });
    methods.listQuotaBindings.mockResolvedValue({ items: [] });
    methods.listRoles.mockResolvedValue({ items: roles });
    methods.createQuotaPolicy.mockImplementation(async (value) => ({ ...value, policy_id: `policy-${value.code}` }));
    methods.publishQuotaPolicy.mockResolvedValue({ effective_from: "2020-01-01T00:00:00Z" });
    methods.bindQuotaPolicy.mockResolvedValue({});
  });

  it("offers the approved four-role trial plan without writing on page load", async () => {
    render(<RoleQuotaPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存全部角色额度" })).toBeEnabled());
    expect(screen.getByLabelText("学生每日上限")).toHaveValue("3000000");
    expect(screen.getByLabelText("教师每日上限")).toHaveValue("2250000");
    expect(screen.getByLabelText("游客每周上限")).toHaveValue("7500000");
    expect(screen.getByLabelText("开发者每周上限")).toHaveValue("15000000");
    expect(methods.createQuotaPolicy).not.toHaveBeenCalled();
  });

  it("creates and publishes a new version before replacing the role binding", async () => {
    methods.listQuotaPolicies.mockResolvedValue({ items: [policy] });
    methods.listQuotaBindings.mockResolvedValue({ items: [binding] });
    render(<RoleQuotaPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存教师额度" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("教师每日上限"), { target: { value: "2400000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存教师额度" }));
    await waitFor(() => expect(methods.bindQuotaPolicy).toHaveBeenCalled());
    expect(methods.createQuotaPolicy).toHaveBeenCalledWith(expect.objectContaining({ code: "role-teacher", status: "draft", daily_limit_micro: 2400000, weekly_limit_micro: 11250000 }));
    expect(methods.bindQuotaPolicy).toHaveBeenCalledWith(expect.objectContaining({ subject_type: "role", subject_id: "teacher", priority: 30, policy_id: "policy-role-teacher" }));
    expect(methods.createQuotaPolicy.mock.invocationCallOrder[0]).toBeLessThan(methods.publishQuotaPolicy.mock.invocationCallOrder[0]);
    expect(methods.publishQuotaPolicy.mock.invocationCallOrder[0]).toBeLessThan(methods.bindQuotaPolicy.mock.invocationCallOrder[0]);
  });

  it("validates every row before changing any role in a bulk save", async () => {
    render(<RoleQuotaPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存全部角色额度" })).toBeEnabled());
    fireEvent.change(screen.getByLabelText("教师每日上限"), { target: { value: "-5" } });
    fireEvent.click(screen.getByRole("button", { name: "保存全部角色额度" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("教师每日上限必须是非负整数");
    expect(methods.createQuotaPolicy).not.toHaveBeenCalled();
  });

  it("stops before writing when another administrator has changed the binding", async () => {
    methods.listQuotaPolicies.mockResolvedValue({ items: [policy] });
    methods.listQuotaBindings.mockResolvedValueOnce({ items: [binding] }).mockResolvedValue({ items: [{ ...binding, binding_id: "changed-binding" }] });
    render(<RoleQuotaPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存教师额度" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "保存教师额度" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("已被其他操作修改");
    expect(methods.createQuotaPolicy).not.toHaveBeenCalled();
  });

  it("keeps the old binding when publishing the new policy fails", async () => {
    methods.publishQuotaPolicy.mockRejectedValue(new Error("publish failed"));
    render(<RoleQuotaPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存教师额度" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "保存教师额度" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("发布新版本未完成");
    expect(methods.bindQuotaPolicy).not.toHaveBeenCalled();
  });

  it("uses distinct priorities and approved totals when applying all four roles", async () => {
    render(<RoleQuotaPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存全部角色额度" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "填入试用方案" }));
    fireEvent.click(screen.getByRole("button", { name: "保存全部角色额度" }));
    await screen.findByRole("status");
    expect(methods.bindQuotaPolicy.mock.calls.map(([input]) => input.priority)).toEqual([10, 20, 30, 40]);
    expect(methods.createQuotaPolicy.mock.calls.map(([input]) => [input.daily_limit_micro, input.weekly_limit_micro, input.request_limit_micro])).toEqual([
      [1500000, 7500000, 1000000], [3000000, 15000000, 1000000], [2250000, 11250000, 1000000], [3000000, 15000000, 1000000],
    ]);
  });

  it("reconciles a committed binding after its response was lost", async () => {
    methods.listQuotaBindings.mockResolvedValueOnce({ items: [] }).mockResolvedValueOnce({ items: [] })
      .mockResolvedValue({ items: [{ ...binding, policy_id: "policy-role-teacher" }] });
    methods.bindQuotaPolicy.mockRejectedValue(new Error("network response lost"));
    render(<RoleQuotaPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存教师额度" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "保存教师额度" }));
    expect(await screen.findByRole("status")).toHaveTextContent("教师额度已保存");
    expect(methods.bindQuotaPolicy).toHaveBeenCalledTimes(1);
  });

  it("shows that user-specific bindings override the role defaults", async () => {
    methods.listQuotaBindings.mockResolvedValue({ items: [{ ...binding, subject_type: "user", subject_id: "specific-user" }] });
    render(<RoleQuotaPanel />);
    expect(await screen.findByText(/当前有 1 个用户专属绑定/)).toBeInTheDocument();
  });

  it("resets legacy student restrictions and saves only the student row", async () => {
    methods.listQuotaPolicies.mockResolvedValue({ items: [{ ...policy, code: "role-student",
      daily_limit_micro: 3000000, weekly_limit_micro: 21000000, request_limit_micro: 1000000,
      max_overdraft_micro: 10000, allowed_model_profiles: ["economy", "standard"] }] });
    methods.listQuotaBindings.mockResolvedValue({ items: [{ ...binding, subject_id: "student", priority: 20 }] });
    render(<RoleQuotaPanel />);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存学生额度" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "填入试用方案" }));
    fireEvent.change(screen.getByLabelText("学生单请求上限"), { target: { value: "1000000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存学生额度" }));
    await waitFor(() => expect(methods.bindQuotaPolicy).toHaveBeenCalledTimes(1));
    expect(methods.createQuotaPolicy).toHaveBeenCalledTimes(1);
    expect(methods.createQuotaPolicy).toHaveBeenCalledWith(expect.objectContaining({
      code: "role-student", daily_limit_micro: 3000000, weekly_limit_micro: 15000000,
      request_limit_micro: 1000000, concurrency_limit: 1, max_overdraft_micro: 0,
      allowed_model_profiles: [], unlimited: false,
    }));
    expect(methods.bindQuotaPolicy).toHaveBeenCalledWith(expect.objectContaining({
      subject_type: "role", subject_id: "student", priority: 20,
    }));
  });
});
