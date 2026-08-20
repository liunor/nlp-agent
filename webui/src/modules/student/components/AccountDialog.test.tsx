import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AccountDialog } from "./AccountDialog";

describe("AccountDialog", () => {
  it("shows the active account and delegates logout", async () => {
    const logout = vi.fn().mockResolvedValue(undefined);
    render(<AccountDialog open session={{ user_id: "user-id", username: "nova", display_name: "Nova 学习者", workspace_ids: ["default"], roles: ["student"], csrf_token: "csrf", expires_at: 1 }} onClose={vi.fn()} onLogout={logout} />);

    expect(screen.getByText("nova")).toBeVisible();
    expect(screen.getByText("Nova 学习者")).toBeVisible();
    expect(screen.getByText("student")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
  });
});
