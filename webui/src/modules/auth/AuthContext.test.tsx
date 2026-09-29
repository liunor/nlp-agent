import { act, render, screen, waitFor } from "@testing-library/react";

import { api, ensureAuth } from "@/platform/http/api";
import type { AuthSession } from "@/shared/types";

import { AuthProvider, useAuth } from "@/platform/auth/AuthContext";

vi.mock("@/platform/http/api", () => ({
  AUTH_EXPIRED_EVENT: "nova:auth-expired",
  ensureAuth: vi.fn(),
  api: {
    login: vi.fn(),
    logout: vi.fn(),
  },
}));

const session: AuthSession = {
  user_id: "user-1",
  workspace_ids: ["workspace-1"],
  roles: ["guest"],
  permissions: ["identity:profile:read_self"],
  csrf_token: "csrf-1",
  expires_at: 1_900_000_000,
};

function Consumer() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="user">{auth.user?.user_id ?? "anonymous"}</span>
      <span data-testid="loading">{String(auth.isLoading)}</span>
      <span data-testid="authenticated">{String(auth.isAuthenticated)}</span>
      <span data-testid="expired">{String(auth.isAuthExpired)}</span>
      <span data-testid="roles">{auth.roles.join(",")}</span>
      <button
  type="button"
  onClick={() => void auth.login("user", "password").catch(() => undefined)}
>
  login
</button>
      <button type="button" onClick={() => void auth.logout()}>logout</button>
    </div>
  );
}

describe("AuthProvider", () => {
  beforeEach(() => {
    vi.mocked(ensureAuth).mockReset();
    vi.mocked(api.login).mockReset();
    vi.mocked(api.logout).mockReset();
  });

  it("bootstraps the database-backed session and exposes authentication state", async () => {
    vi.mocked(ensureAuth).mockResolvedValue(session);

    render(<AuthProvider><Consumer /></AuthProvider>);

    await waitFor(() => expect(screen.getByTestId("user")).toHaveTextContent("user-1"));
    expect(screen.getByTestId("authenticated")).toHaveTextContent("true");
    expect(screen.getByTestId("roles")).toHaveTextContent("guest");
    expect(screen.getByTestId("loading")).toHaveTextContent("false");
  });

  it("updates the shared session after login and clears it after logout", async () => {
    vi.mocked(ensureAuth).mockRejectedValue(new Error("HTTP 401"));
    vi.mocked(api.login).mockResolvedValue(session);
    vi.mocked(api.logout).mockResolvedValue(undefined);

    render(<AuthProvider><Consumer /></AuthProvider>);

    await waitFor(() => expect(screen.getByTestId("authenticated")).toHaveTextContent("false"));
    await act(async () => { screen.getByRole("button", { name: "login" }).click(); });
    expect(screen.getByTestId("user")).toHaveTextContent("user-1");

    await act(async () => { screen.getByRole("button", { name: "logout" }).click(); });
    expect(screen.getByTestId("authenticated")).toHaveTextContent("false");
    expect(screen.getByTestId("user")).toHaveTextContent("anonymous");
  });
  it("keeps the existing authenticated session when re-login fails", async () => {
  vi.mocked(ensureAuth).mockResolvedValue(session);
  vi.mocked(api.login).mockRejectedValue(new Error("账号或密码错误"));

  render(<AuthProvider><Consumer /></AuthProvider>);

  await waitFor(() => {
    expect(screen.getByTestId("user")).toHaveTextContent("user-1");
  });

  await act(async () => {
  screen.getByRole("button", { name: "login" }).click();
  await Promise.resolve();
});

  await waitFor(() => {
    expect(api.login).toHaveBeenCalledWith("user", "password");
  });

  expect(screen.getByTestId("user")).toHaveTextContent("user-1");
  expect(screen.getByTestId("authenticated")).toHaveTextContent("true");
});
it("clears the expired-auth state after re-login succeeds", async () => {
  vi.mocked(ensureAuth).mockResolvedValue(session);
  vi.mocked(api.login).mockResolvedValue({
    ...session,
    csrf_token: "csrf-2",
  });

  render(
    <AuthProvider>
      <Consumer />
    </AuthProvider>,
  );

  await waitFor(() => {
    expect(screen.getByTestId("authenticated")).toHaveTextContent("true");
  });

  act(() => {
    window.dispatchEvent(new Event("nova:auth-expired"));
  });

  expect(screen.getByTestId("expired")).toHaveTextContent("true");

  await act(async () => {
    screen.getByRole("button", { name: "login" }).click();
    await Promise.resolve();
  });

  await waitFor(() => {
    expect(screen.getByTestId("expired")).toHaveTextContent("false");
  });

  expect(api.login).toHaveBeenCalledWith("user", "password");
  expect(screen.getByTestId("authenticated")).toHaveTextContent("true");
});
  it("marks the session expired when expires_at is reached", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_900_000_000_000);

    vi.mocked(ensureAuth).mockResolvedValue({
      ...session,
      expires_at: 1_900_000_060,
    });

    const view = render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>,
    );

    try {
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByTestId("authenticated")).toHaveTextContent("true");
      expect(screen.getByTestId("expired")).toHaveTextContent("false");

      act(() => {
        vi.advanceTimersByTime(60_000);
      });

      expect(screen.getByTestId("expired")).toHaveTextContent("true");
    } finally {
      view.unmount();
      vi.useRealTimers();
    }
  });
});
