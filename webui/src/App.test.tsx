import { render, screen } from "@testing-library/react";

import { App } from "./App";

describe("App", () => {
  it("renders the backend bootstrap state", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(() => undefined));
    render(<App />);
    expect(screen.getByText("正在验证身份…")).toBeVisible();
  });
});
