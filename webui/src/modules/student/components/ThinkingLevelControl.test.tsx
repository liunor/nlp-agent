import { fireEvent, render, screen } from "@testing-library/react";
import { ThinkingLevelControl } from "./ThinkingLevelControl";

const supportedLevels = ["quick", "light", "deep", "extreme"] as const;
const props = { value: "light" as const, supportedLevels, onChange: vi.fn(), onPreviewChange: vi.fn() };

beforeEach(() => vi.clearAllMocks());

it.each([
  [0, "quick", "快速"],
  [1, "light", "轻度"],
  [2, "deep", "深度"],
  [3, "extreme", "极致"],
] as const)("commits detent %i as the backend level %s", (position, level, label) => {
  render(<ThinkingLevelControl {...props} value={level === "light" ? "deep" : "light"} />);
  fireEvent.click(screen.getByRole("button", { name: /思考程度：/ }));
  const slider = screen.getByRole("slider");
  fireEvent.pointerDown(slider);
  fireEvent.change(slider, { target: { value: String(position) } });
  expect(slider).toHaveAttribute("aria-valuetext", label);
  expect(props.onChange).not.toHaveBeenCalled();
  fireEvent.pointerUp(slider);
  fireEvent.lostPointerCapture(slider);
  fireEvent.blur(slider);
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith(level);
});

it("tracks fractional drag positions and snaps to one supported level on release", () => {
  render(<ThinkingLevelControl {...props} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：轻度" }));
  const slider = screen.getByRole("slider");
  fireEvent.pointerDown(slider);
  fireEvent.change(slider, { target: { value: "1.65" } });
  expect(slider).toHaveValue("1.65");
  expect(slider).toHaveAttribute("aria-valuetext", "深度");
  expect(props.onChange).not.toHaveBeenCalled();
  fireEvent.pointerUp(slider);
  expect(slider).toHaveValue("2");
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith("deep");
});

it("snaps dragging past an unavailable endpoint to the nearest supported level", () => {
  render(<ThinkingLevelControl {...props} value="deep" supportedLevels={["light", "deep"]} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：深度" }));
  const slider = screen.getByRole("slider");
  fireEvent.pointerDown(slider);
  fireEvent.change(slider, { target: { value: "0.2" } });
  fireEvent.pointerUp(slider);
  expect(slider).toHaveValue("1");
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith("light");
});

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

it("skips unsupported levels with keyboard", () => {
  render(<ThinkingLevelControl {...props} supportedLevels={["light", "deep", "extreme"]} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：轻度" }));
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
  fireEvent.change(screen.getByRole("slider"), { target: { value: "2" } });
  expect(props.onChange).toHaveBeenCalledExactlyOnceWith("deep");
});

it("suggests switching models when no thinking level is supported", () => {
  render(<ThinkingLevelControl {...props} supportedLevels={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "思考程度：轻度" }));
  expect(screen.getByRole("status")).toHaveTextContent("请切换支持该功能的模型");
  expect(screen.getByRole("slider")).toBeDisabled();
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
