import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AuthGate } from "./AuthGate";

// Mock the AuthContext with a mutable state
const mockAuthState = {
  user: null as { user_id: string; username: string; display_name: string; status: string; roles: string[]; workspace_ids: string[]; permissions: string[]; created_at: string; updated_at: string } | null,
  isAuthenticated: false,
  isLoading: false,
  error: null as string | null,
  login: vi.fn(),
  logout: vi.fn(),
  refresh: vi.fn(),
};

vi.mock("@/platform/auth/AuthContext", () => ({
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAuth: () => mockAuthState,
}));

// Mock react-router-dom
vi.mock("react-router-dom", () => ({
  Navigate: ({ to }: { to: string }) => <div data-testid="navigate">{to}</div>,
  useLocation: () => ({ pathname: "/test" }),
}));

describe("AuthGate", () => {
  beforeEach(() => {
    mockAuthState.user = null;
    mockAuthState.isAuthenticated = false;
    mockAuthState.isLoading = false;
    mockAuthState.error = null;
  });

  it("shows loading state when auth is resolving", () => {
    mockAuthState.isLoading = true;

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>
    );

    expect(screen.getByText("正在验证身份…")).toBeInTheDocument();
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
  });

  it("redirects to login when not authenticated", () => {
    mockAuthState.isLoading = false;
    mockAuthState.isAuthenticated = false;

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>
    );

    expect(screen.getByTestId("navigate")).toHaveTextContent("/login");
    expect(screen.queryByText("Protected Content")).not.toBeInTheDocument();
  });

  it("renders children when authenticated", () => {
    mockAuthState.isLoading = false;
    mockAuthState.isAuthenticated = true;
    mockAuthState.user = {
      user_id: "u1",
      username: "test",
      display_name: "Test User",
      status: "active",
      roles: ["admin"],
      workspace_ids: ["default"],
      permissions: [],
      created_at: "",
      updated_at: "",
    };

    render(
      <AuthGate>
        <div>Protected Content</div>
      </AuthGate>
    );

    expect(screen.getByText("Protected Content")).toBeInTheDocument();
    expect(screen.queryByTestId("navigate")).not.toBeInTheDocument();
  });
});
