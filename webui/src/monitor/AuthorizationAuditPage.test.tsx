import { fireEvent, render, screen } from "@testing-library/react";

const { monitorApi } = vi.hoisted(() => ({
  monitorApi: {
    authorizationAudit: vi.fn(),
    authorizationAuditStats: vi.fn(),
  },
}));

vi.mock("./api", () => ({ monitorApi }));

import type { AuthorizationAuditListResponse, AuthorizationAuditSummary } from "@/shared/types";
import { AuthorizationAuditPage } from "./AuthorizationAuditPage";

const summary: AuthorizationAuditSummary = {
  period_days: 30,
  since: "2026-08-01T00:00:00Z",
  total: 3,
  by_decision: { allow: 2, deny: 1 },
  top_reasons: [{ reason_code: "authorization_required", count: 2 }],
};

const page: AuthorizationAuditListResponse = {
  items: [
    { id: "audit-1", actor_user_id: "developer", target_user_id: null, decision: "allow", reason_code: "authorization_required", permission_code: "system:runtime:monitor", resource_type: "sandbox", resource_id: "runtime-1", detail: { request_id: "request-1" }, created_at: "2026-09-06T10:02:00Z" },
    { id: "audit-2", actor_user_id: "developer", target_user_id: null, decision: "allow", reason_code: "authorization_required", permission_code: "system:runtime:monitor", resource_type: "sandbox", resource_id: "runtime-2", detail: { request_id: "request-2", secret: "must stay lazy" }, created_at: "2026-09-06T10:01:00Z" },
    { id: "audit-3", actor_user_id: "developer", target_user_id: "student-1", decision: "deny", reason_code: "permission_denied", permission_code: "system:user:manage", resource_type: "user", resource_id: "student-1", detail: { request_id: "request-3" }, created_at: "2026-09-06T10:00:00Z" },
  ],
  total: 3,
  offset: 0,
  limit: 20,
  has_more: false,
};

describe("AuthorizationAuditPage", () => {
  beforeEach(() => {
    monitorApi.authorizationAudit.mockReset().mockResolvedValue(page);
    monitorApi.authorizationAuditStats.mockReset().mockResolvedValue(summary);
  });

  it("loads a small page and groups repeated authorization checks", async () => {
    render(<AuthorizationAuditPage />);

    await vi.waitFor(() => expect(monitorApi.authorizationAudit).toHaveBeenCalled());
    expect(monitorApi.authorizationAudit).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    await vi.waitFor(() => expect(screen.getByRole("button", { name: /展开 authorization_required/ })).toBeVisible());
    expect(screen.getByText("2 次")).toBeVisible();
    expect(screen.getByText(/3\s*条审计记录/)).toBeVisible();
  });

  it("does not mount detail JSON before the operator expands a group", async () => {
    render(<AuthorizationAuditPage />);

    await vi.waitFor(() => expect(monitorApi.authorizationAudit).toHaveBeenCalled());
    expect(monitorApi.authorizationAudit).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    await vi.waitFor(() => expect(screen.getByRole("button", { name: /展开 authorization_required/ })).toBeVisible());
    expect(screen.queryByText("must stay lazy")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /展开 authorization_required/ }));
    expect(screen.getByText(/must stay lazy/)).toBeVisible();
  });
});
