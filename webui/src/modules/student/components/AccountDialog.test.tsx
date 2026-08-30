import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";

vi.mock("@/platform/http/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    getCurrentUser: vi.fn().mockResolvedValue({
      id: "user-id",
      user_id: "user-id",
      username: "nova",
      display_name: "Nova 学习者",
      status: "active",
      roles: ["student"],
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T00:00:00Z",
    }),
  },
}));

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

  it("opens the profile settings as an in-platform overlay instead of navigating away", async () => {
    // Mirror production behaviour: clicking 个人设置 closes the account dialog
    // before the profile overlay opens in the same place.
    function Harness() {
      const [open, setOpen] = useState(true);
      return <AccountDialog open={open} session={{ user_id: "user-id", username: "nova", display_name: "Nova 学习者", workspace_ids: ["default"], roles: ["student"], csrf_token: "csrf", expires_at: 1 }} onClose={() => setOpen(false)} onLogout={vi.fn().mockResolvedValue(undefined)} />;
    }
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "个人设置" }));

    // The overlay renders in place; no URL navigation happens.
    expect(await screen.findByRole("heading", { name: "个人设置" })).toBeVisible();
    expect(window.location.pathname).not.toBe("/profile");
  });

  it("closes the profile dialog with Esc or the top-right close button", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <>
        <button type="button" onClick={() => setOpen(true)}>打开账户管理</button>
        <AccountDialog open={open} session={{ user_id: "user-id", username: "nova", display_name: "Nova 学习者", workspace_ids: ["default"], roles: ["student"], csrf_token: "csrf", expires_at: 1 }} onClose={() => setOpen(false)} onLogout={vi.fn().mockResolvedValue(undefined)} />
      </>;
    }
    render(<Harness />);

    fireEvent.click(screen.getByRole("button", { name: "打开账户管理" }));
    fireEvent.click(screen.getByRole("button", { name: "个人设置" }));
    expect(await screen.findByRole("heading", { name: "个人设置" })).toBeVisible();

    // Standard dialog behaviour: Escape closes it.
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Reopen and close via the top-right X button this time.
    fireEvent.click(screen.getByRole("button", { name: "打开账户管理" }));
    fireEvent.click(screen.getByRole("button", { name: "个人设置" }));
    expect(await screen.findByRole("heading", { name: "个人设置" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭个人设置" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("resets tabs and inputs when the profile dialog is reopened", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return <>
        <button type="button" onClick={() => setOpen(true)}>打开账户管理</button>
        <AccountDialog open={open} session={{ user_id: "user-id", username: "nova", display_name: "Nova 学习者", workspace_ids: ["default"], roles: ["student"], csrf_token: "csrf", expires_at: 1 }} onClose={() => setOpen(false)} onLogout={vi.fn().mockResolvedValue(undefined)} />
      </>;
    }
    render(<Harness />);

    // Open, switch tab, then close.
    fireEvent.click(screen.getByRole("button", { name: "打开账户管理" }));
    fireEvent.click(screen.getByRole("button", { name: "个人设置" }));
    expect(await screen.findByRole("heading", { name: "个人设置" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "修改密码" }));
    expect(screen.getByLabelText("当前密码")).toBeVisible();

    // Close (unmount) and reopen: back to 基本信息, no leftover tab state.
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "打开账户管理" }));
    fireEvent.click(screen.getByRole("button", { name: "个人设置" }));
    expect(await screen.findByRole("heading", { name: "个人设置" })).toBeVisible();
    expect(screen.queryByLabelText("当前密码")).not.toBeInTheDocument();
    expect(screen.getByText("账号")).toBeVisible();
  });
});
