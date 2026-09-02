import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ProfileDialog } from "./ProfileDialog";

const methods = vi.hoisted(() => ({
  getCurrentUser: vi.fn(),
  getQuota: vi.fn(),
  getUsage: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({ api: methods, ApiError: class ApiError extends Error {} }));
vi.mock("@/platform/realtime/client", () => ({
  StudentSocket: class { connect() {} close() {} },
}));

describe("ProfileDialog quota section", () => {
  beforeEach(() => {
    methods.getCurrentUser.mockResolvedValue({
      id: "user-1", username: "nova", display_name: "Nova", roles: ["student"],
      status: "active", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    });
    methods.getQuota.mockResolvedValue({ quota: { user_id: "user-1", workspace_id: "workspace-a", buckets: [] }, policy: null });
    methods.getUsage.mockResolvedValue({ events: 0, priced_credits_micro: 0, unpriced_events: 0, credits_complete: true, tokens: {}, breakdown: [] });
  });

  it("shows personal quota inside settings and scopes requests to an authorized workspace", async () => {
    render(<ProfileDialog open onClose={vi.fn()} sessionRoles={["student"]} userId="user-1" workspaceIds={["workspace-a", "workspace-b"]} />);

    fireEvent.click(await screen.findByRole("button", { name: "额度与用量" }));

    await waitFor(() => expect(methods.getQuota).toHaveBeenCalledWith("workspace-a"));
    expect(methods.getUsage).toHaveBeenCalledWith(7, "workspace-a", "day");
    expect(methods.getUsage).toHaveBeenCalledWith(182, "workspace-a", "day");
    expect(methods.getUsage).toHaveBeenCalledWith(182, "workspace-a", "week");
    expect(await screen.findByText("Token 活动")).toBeVisible();
    expect(screen.queryByRole("combobox", { name: "工作空间" })).not.toBeInTheDocument();
  });

  it("keeps the quota view inside the settings dialog without exposing workspace scope", async () => {
    render(<ProfileDialog open onClose={vi.fn()} sessionRoles={["student"]} userId="user-1" workspaceIds={["workspace-a", "workspace-b"]} />);
    fireEvent.click(await screen.findByRole("button", { name: "额度与用量" }));

    await waitFor(() => expect(methods.getQuota).toHaveBeenCalledWith("workspace-a"));
    expect(screen.queryByRole("combobox", { name: "工作空间" })).not.toBeInTheDocument();
    expect(window.location.pathname).not.toBe("/usage");
  });
});
