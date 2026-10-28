import { fireEvent, render, screen } from "@testing-library/react";
import { ThinkingLevelControl } from "./ThinkingLevelControl";

const supportedLevels = ["quick", "light", "deep", "extreme"] as const;
const props = { value: "light" as const, supportedLevels, onChange: vi.fn(), onPreviewChange: vi.fn() };

beforeEach(() => vi.clearAllMocks());

it("previews dragging and commits only once on release without showing prices", () => {
  render(<ThinkingLevelControl {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：轻度" }));
  const slider = screen.getByRole("slider", { name: "思考程度" });
  fireEvent.pointerDown(slider);
  fireEvent.change(slider, { target: { value: "2" } });
  fireEvent.change(slider, { target: { value: "3" } });
  expect(slider).toHaveAttribute("aria-valuetext", "极致");
  expect(props.onChange).not.toHaveBeenCalled();
  fireEvent.pointerUp(slider);
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith("extreme");
  expect(screen.getByRole("dialog")).not.toHaveTextContent(/额度|余额|消耗|倍率/);
});

it("skips unsupported levels with keyboard and disables their labels", () => {
  render(<ThinkingLevelControl {...props} supportedLevels={["light", "deep", "extreme"]} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：轻度" }));
  expect(screen.getByRole("button", { name: "快速" })).toBeDisabled();
  const slider = screen.getByRole("slider");
  fireEvent.keyDown(slider, { key: "Home" });
  fireEvent.keyUp(slider, { key: "Home" });
  expect(props.onChange).not.toHaveBeenCalled();
  fireEvent.keyDown(slider, { key: "End" });
  fireEvent.keyUp(slider, { key: "End" });
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith("extreme");
});

it("dismisses an uncommitted preview with Escape and restores trigger focus", () => {
  render(<ThinkingLevelControl {...props} />);
  const trigger = screen.getByRole("button", { name: "思考程度：轻度" });
  fireEvent.click(trigger);
  const slider = screen.getByRole("slider");
  fireEvent.pointerDown(slider);
  fireEvent.change(slider, { target: { value: "3" } });
  fireEvent.keyDown(slider, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(props.onChange).not.toHaveBeenCalled();
  expect(props.onPreviewChange).toHaveBeenLastCalledWith(false);
});

it("prevents changes while running or saving, and delegates guest clicks to login", () => {
  const { rerender } = render(<ThinkingLevelControl {...props} disabled />);
  expect(screen.getByRole("button", { name: "思考程度：轻度" })).toBeDisabled();
  const login = vi.fn();
  rerender(<ThinkingLevelControl {...props} onRequireLogin={login} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：轻度" }));
  expect(login).toHaveBeenCalledOnce();
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
});

it("does not silently change an unsupported saved selection", () => {
  render(<ThinkingLevelControl {...props} value="quick" supportedLevels={["light", "deep", "extreme"]} />);
  expect(screen.getByRole("button", { name: "思考程度：快速" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "思考程度：快速" }));
  expect(screen.getByRole("status")).toHaveTextContent("请选择其他等级");
  fireEvent.click(screen.getByRole("button", { name: "深度" }));
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith("deep");
});

it("releases a preview when an empty history response remounts the composer", () => {
  const { unmount } = render(<ThinkingLevelControl {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：轻度" }));
  const slider = screen.getByRole("slider");
  fireEvent.pointerDown(slider);
  fireEvent.change(slider, { target: { value: "2" } });
  expect(props.onPreviewChange).toHaveBeenLastCalledWith(true);
  unmount();
  expect(props.onPreviewChange).toHaveBeenLastCalledWith(false);
  expect(props.onChange).not.toHaveBeenCalled();
});

it("keeps keyboard focus recoverable when saving temporarily disables the slider", () => {
  const { rerender } = render(<ThinkingLevelControl {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：轻度" }));
  rerender(<ThinkingLevelControl {...props} value="deep" disabled />);
  fireEvent.blur(screen.getByRole("slider"), { relatedTarget: null });
  expect(screen.getByRole("dialog")).toBeVisible();
  rerender(<ThinkingLevelControl {...props} value="deep" />);
  expect(screen.getByRole("slider")).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("slider"), { key: "Escape" });
  expect(screen.getByRole("button", { name: "思考程度：深度" })).toHaveFocus();
});
