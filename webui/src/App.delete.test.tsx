import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => ({
  listSessions: vi.fn().mockResolvedValue({ items: [{ session_id: "session_1", user_id: "student", workspace_id: "default", channel: "web" }] }),
  getSettings: vi.fn().mockResolvedValue({ preferences: { settings: {} }, runtime: { default_model_profile: "deepseek", model_profiles: {} } }),
  getLearningCatalog: vi.fn().mockResolvedValue({ catalog: { topics: [] } }),
  deleteSession: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/platform/realtime/client", () => ({ StudentSocket: class { connect() {} close() {} setSession() {} sendChat() {} resume() {} cancel() {} } }));
vi.mock("@/platform/http/api", () => ({
  AUTH_EXPIRED_EVENT: "nova:auth-expired",
  ensureAuth: vi.fn().mockResolvedValue({}),
  api,
}));

import { App } from "./App";

describe("student deletion", () => {
  it("uses the shared dialog and only calls the FastAPI deletion endpoint after confirmation", async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "打开侧栏" }));
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    expect(screen.getByRole("alertdialog", { name: "删除“新的学习对话”对话？" })).toBeVisible();
    expect(api.deleteSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(api.deleteSession).toHaveBeenCalledWith("session_1"));
  });
});
