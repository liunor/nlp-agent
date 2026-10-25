import { act, fireEvent, render, screen } from "@testing-library/react";
import { ToolDock } from "./ToolDock";

const mocks = vi.hoisted(() => ({ lease: vi.fn(), usage: vi.fn(), execute: vi.fn() }));
vi.mock("@/platform/http/api", () => ({ api: { ensureSandboxLease: mocks.lease, getSandboxUsage: mocks.usage, executeSandbox: mocks.execute } }));
vi.mock("./FilesPanel", () => ({ FilesPanel: () => null }));
vi.mock("./SandboxArtifactFrame", () => ({ SandboxArtifactFrame: () => null }));

const ready = { runtime_available: true, runtime: { kind: "inmemory", ticket: null } };
const warming = { runtime_available: false, runtime: null, pool_status: "warming" };

function showSandbox() {
  return render(<ToolDock open expanded={false} openTools={["sandbox"]} activeTool="sandbox" toolMenuOpen={false}
    onToolMenuOpenChange={vi.fn()} onOpenTool={vi.fn()} onReorderTools={vi.fn()} onCloseTool={vi.fn()}
    onActiveToolChange={vi.fn()} onExplainCode={vi.fn()} learningPanel={null} knowledgeBookPanel={null}
    whiteboardPanel={null} filesUserId={null} filesWorkspaceId="default" />);
}

beforeEach(() => {
  vi.useFakeTimers();
  mocks.lease.mockReset();
  mocks.usage.mockReset();
  mocks.execute.mockReset();
});
afterEach(() => { vi.useRealTimers(); });

it.each([400, 401, 403, 404])("stops automatically retrying terminal HTTP %i", async (status) => {
  mocks.lease.mockRejectedValue(Object.assign(new Error("rejected"), { status }));
  showSandbox();
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(mocks.lease).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "重试连接" })).toBeVisible();
  expect(screen.getByRole("button", { name: "运行代码" })).toBeDisabled();
});

it("lets an explicit retry recover without clearing the editor", async () => {
  mocks.lease.mockRejectedValueOnce(Object.assign(new Error("forbidden"), { status: 403 })).mockResolvedValue(ready);
  showSandbox();
  await act(async () => {});
  fireEvent.change(screen.getByRole("textbox", { name: "沙箱代码" }), { target: { value: "my_variable = 42" } });
  fireEvent.click(screen.getByRole("button", { name: "重试连接" }));
  await act(async () => {});
  expect(mocks.lease).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("textbox", { name: "沙箱代码" })).toHaveValue("my_variable = 42");
  expect(screen.getByRole("button", { name: "运行代码" })).toBeEnabled();
});

it("bounds waiting for an unavailable pool instead of polling forever", async () => {
  mocks.lease.mockResolvedValue(warming);
  showSandbox();
  await act(async () => { await vi.advanceTimersByTimeAsync(61_000); });
  const count = mocks.lease.mock.calls.length;
  expect(screen.getByRole("status")).toHaveTextContent("已停止自动等待");
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(mocks.lease).toHaveBeenCalledTimes(count);
  expect(screen.getByRole("button", { name: "运行代码" })).toBeDisabled();
});

it("backs off and stops repeated network failures", async () => {
  mocks.lease.mockRejectedValue(new TypeError("network unavailable"));
  showSandbox();
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(mocks.lease).toHaveBeenCalledTimes(4);
  expect(screen.getByRole("status")).toHaveTextContent("已停止自动重试");
});

it("honors a rate-limit retry delay before trying again", async () => {
  mocks.lease.mockRejectedValueOnce(Object.assign(new Error("rate limited"), { status: 429, retryAfterSeconds: 5 })).mockResolvedValue(ready);
  showSandbox();
  await act(async () => { await vi.advanceTimersByTimeAsync(4_999); });
  expect(mocks.lease).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(mocks.lease).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("button", { name: "运行代码" })).toBeEnabled();
});

it("cancels the pending pool retry when the sandbox is closed", async () => {
  mocks.lease.mockResolvedValue(warming);
  const panel = showSandbox();
  await act(async () => {});
  panel.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(mocks.lease).toHaveBeenCalledTimes(1);
});

it("also bounds an unanswered request and ignores its late response", async () => {
  let finish: (value: typeof warming) => void = () => {};
  mocks.lease.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  showSandbox();
  await act(async () => { await vi.advanceTimersByTimeAsync(61_000); });
  expect(screen.getByRole("status")).toHaveTextContent("已停止自动等待");
  await act(async () => { finish(warming); });
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(mocks.lease).toHaveBeenCalledTimes(1);
});

it.each([401, 403])("stops resource polling on HTTP %i and lets the user reconnect", async (status) => {
  mocks.lease.mockResolvedValue({ runtime_available: true, runtime: { ticket: "test-ticket" } });
  mocks.usage.mockRejectedValue(Object.assign(new Error("expired"), { status }));
  showSandbox();
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(mocks.usage).toHaveBeenCalledTimes(1);
  expect(mocks.lease).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "重试连接" })).toBeVisible();
});

it("also bounds resource polling network failures without disabling a ready kernel", async () => {
  mocks.lease.mockResolvedValue({ runtime_available: true, runtime: { ticket: "test-ticket" } });
  mocks.usage.mockRejectedValue(new TypeError("network"));
  showSandbox();
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
  expect(mocks.usage).toHaveBeenCalledTimes(4);
  expect(screen.getByRole("button", { name: "运行代码" })).toBeEnabled();
});

it("uses renewed tickets after five minutes without creating an immediate poll loop", async () => {
  mocks.lease.mockResolvedValue({ runtime_available: true, runtime: { ticket: "original" } });
  mocks.usage.mockResolvedValue({ cpu_percent: 1, memory_percent: 2, ticket: "renewed" });
  mocks.execute.mockResolvedValue({ stdout: "state preserved" });
  showSandbox();
  await act(async () => {});
  expect(mocks.usage).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  expect(mocks.usage).toHaveBeenCalledTimes(2);
  expect(mocks.usage).toHaveBeenLastCalledWith("renewed");
  await act(async () => { await vi.advanceTimersByTimeAsync(300_000); });
  expect(mocks.usage).toHaveBeenCalledTimes(152);
  expect(mocks.lease).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "运行代码" }));
  await act(async () => {});
  expect(mocks.execute).toHaveBeenCalledWith(expect.any(String), "renewed");
});

it("ignores late resource responses that carry a ticket from before execution", async () => {
  let finish: (value: { ticket: string }) => void = () => {};
  mocks.lease.mockResolvedValue({ runtime_available: true, runtime: { ticket: "original-nonce" } });
  mocks.usage.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
    .mockResolvedValue({ ticket: "after-execution", cpu_percent: 0, memory_percent: 0 });
  mocks.execute.mockResolvedValue({ stdout: "ok", ticket: "after-execution" });
  showSandbox();
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "运行代码" }));
  await act(async () => {});
  await act(async () => { finish({ ticket: "stale-nonce" }); });
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  expect(mocks.usage).toHaveBeenLastCalledWith("after-execution");
  fireEvent.click(screen.getByRole("button", { name: "运行代码" }));
  await act(async () => {});
  expect(mocks.execute).toHaveBeenLastCalledWith(expect.any(String), "after-execution");
});
