import { render, screen, waitFor } from "@testing-library/react";

import { ProfileDialog } from "./ProfileDialog";

const methods = vi.hoisted(() => ({
  getCurrentUser: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({ api: methods, ApiError: class ApiError extends Error {} }));

describe("ProfileDialog", () => {
  beforeEach(() => {
    methods.getCurrentUser.mockResolvedValue({
      id: "user-1", username: "nova", display_name: "Nova", roles: ["student"],
      status: "active", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    });
  });

  it("does not expose the duplicate quota page in personal account settings", async () => {
    render(<ProfileDialog open onClose={vi.fn()} sessionRoles={["student"]} />);

    await waitFor(() => expect(screen.getByRole("heading", { name: "个人设置" })).toBeVisible());
    expect(screen.queryByRole("button", { name: "额度与用量" })).not.toBeInTheDocument();
  });
});
