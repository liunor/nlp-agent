import { readFileSync } from "node:fs";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { render, screen } from "@testing-library/react";

import { AppShell } from "./AppShell";

vi.mock("@/platform/auth/AuthContext", () => ({
  useAuth: () => ({
    isAuthExpired: false,
    login: async () => undefined,
  }),
}));

const stylesheet = readFileSync("src/app/styles.css", "utf8");

describe("student ICP record bar", () => {
  it("shows the hard-coded record number on the student home without replacing routed content", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<button type="button">发送</button>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "发送" })).toBeVisible();
    expect(screen.getByText("© 2026 乐山师范学院自然语言处理教学平台")).toBeVisible();

    const recordLink = screen.getByRole("link", { name: "蜀ICP备2026055638号" });
    expect(recordLink).toHaveAttribute("href", "https://beian.miit.gov.cn/");
    expect(recordLink).toHaveAttribute("target", "_blank");
    expect(recordLink).toHaveAttribute("rel", "noreferrer");
  });

  it("does not add the record bar to non-student routes", () => {
    render(
      <MemoryRouter initialEntries={["/teacher"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="*" element={<button type="button">教师页面</button>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "教师页面" })).toBeVisible();
    expect(screen.queryByRole("link", { name: "蜀ICP备2026055638号" })).not.toBeInTheDocument();
  });

  it("keeps the record bar lightweight and reserves the conversation safe area", () => {
    const recordBarRule = stylesheet.match(/\.site-icp-bar\s*\{([^}]*)\}/)?.[1] ?? "";
    const recordLinkRule = stylesheet.match(/\.site-icp-bar a\s*\{([^}]*)\}/)?.[1] ?? "";
    const studentConversationRule = stylesheet.match(/\.student-app-shell > \.thread-shell\s*\{([^}]*)\}/)?.[1] ?? "";
    const guestConversationRule = stylesheet.match(/\.unauthenticated-app-shell > \.unauthenticated-student-shell\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(recordBarRule).toContain("position: fixed");
    expect(recordBarRule).toContain("z-index: 33");
    expect(recordBarRule).toContain("pointer-events: none");
    expect(recordLinkRule).toContain("pointer-events: auto");
    expect(recordLinkRule).toContain("flex: 0 0 auto");
    expect(studentConversationRule).toContain("padding-bottom: calc(34px + env(safe-area-inset-bottom))");
    expect(guestConversationRule).toContain("padding-bottom: calc(34px + env(safe-area-inset-bottom))");
    expect(stylesheet).toContain(".student-school-logo { position: fixed; z-index: 32;");
  });
});
