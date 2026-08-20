import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ensureAuth } from "@/platform/http/api";

import { AuthProvider } from "@/platform/auth/AuthContext";
import { AuthGate } from "./AuthGate";

vi.mock("@/platform/http/api", () => ({
  ensureAuth: vi.fn(),
  api: { logout: vi.fn() },
}));

function renderGate(initialEntry = "/protected") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<p>登录页</p>} />
          <Route element={<AuthGate><p>受保护内容</p></AuthGate>}>
            <Route path="/protected" element={<p>受保护内容</p>} />
          </Route>
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("AuthGate", () => {
  beforeEach(() => vi.mocked(ensureAuth).mockReset());

  it("redirects an unauthenticated browser session to the login page", async () => {
    vi.mocked(ensureAuth).mockResolvedValue(null as never);

    renderGate();

    expect(await screen.findByText("登录页")).toBeVisible();
  });

  it("renders protected routes after the database session is bootstrapped", async () => {
    vi.mocked(ensureAuth).mockResolvedValue({
      user_id: "user-1",
      workspace_ids: ["workspace-1"],
      roles: ["guest"],
      csrf_token: "csrf-1",
      expires_at: 1_900_000_000,
    });

    renderGate();

    await waitFor(() => expect(screen.getByText("受保护内容")).toBeVisible());
  });
});
