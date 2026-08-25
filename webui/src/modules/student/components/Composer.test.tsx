import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { uploadAttachment } from "@/platform/http/api";
import { Composer } from "./Composer";

vi.mock("@/platform/http/api", () => ({ uploadAttachment: vi.fn() }));

describe("Composer", () => {
  beforeEach(() => {
    vi.mocked(uploadAttachment).mockReset();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:attachment-preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("submits on Enter and keeps Shift+Enter for multiline questions", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} running={false} onSend={onSend} onCancel={vi.fn()} />);
    const input = screen.getByLabelText("学习问题");
    fireEvent.change(input, { target: { value: "解释 BERT" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledWith("解释 BERT");
  });

  it("sends preset questions through the same send path, preserving typed content", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} running={false} onSend={onSend} onCancel={vi.fn()} />);
    const input = screen.getByLabelText("学习问题");
    fireEvent.change(input, { target: { value: "前一个问题" } });
    fireEvent.click(screen.getByRole("button", { name: "用简单语言解释" }));

    expect(onSend).toHaveBeenCalledWith("前一个问题\n用简单语言解释");
    expect((input as HTMLTextAreaElement).value).toBe("");
  });

  it("sends an empty preset untouched when no content is typed", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} running={false} onSend={onSend} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "用简单语言解释" }));

    expect(onSend).toHaveBeenCalledWith("用简单语言解释");
  });

  it("trims surrounding whitespace from the merged preset value", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} running={false} onSend={onSend} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("学习问题"), { target: { value: "  前一个问题  " } });
    fireEvent.click(screen.getByRole("button", { name: "用简单语言解释" }));

    expect(onSend).toHaveBeenCalledWith("前一个问题\n用简单语言解释");
  });

  it("disables preset buttons while a turn is running", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} running onSend={onSend} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "用简单语言解释" }));

    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "用简单语言解释" })).toBeDisabled();
  });

  it("switches to the backend cancel action while streaming", () => {
    const onCancel = vi.fn();
    render(<Composer disabled={false} running onSend={vi.fn()} onCancel={onCancel} />);
    fireEvent.click(screen.getByLabelText("停止生成"));
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("offers available backend models and disables selection while running", () => {
    const onModelProfileChange = vi.fn();
    const props = {
      disabled: false,
      onSend: vi.fn(),
      onCancel: vi.fn(),
      modelProfiles: {
        deepseek: { label: "DeepSeek", provider: "deepseek", available: true },
        qwen: { label: "Qwen", provider: "dashscope", available: true },
        offline: { label: "Offline", provider: "local", available: false },
      },
      modelProfile: "deepseek",
      onModelProfileChange,
    };
    const { rerender } = render(<Composer {...props} running={false} />);
    const select = screen.getByRole("combobox", { name: "选择模型" });

    expect(screen.getByRole("option", { name: "Qwen" })).toBeEnabled();
    expect(screen.getByRole("option", { name: "Offline（不可用）" })).toBeDisabled();
    fireEvent.change(select, { target: { value: "qwen" } });
    expect(onModelProfileChange).toHaveBeenCalledWith("qwen");

    rerender(<Composer {...props} modelProfile="qwen" running />);
    expect(screen.getByRole("combobox", { name: "选择模型" })).toBeDisabled();
  });

  it("renders attachment upload button when sessionId is provided and disables when absent", () => {
    const { rerender } = render(
      <Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId={null} />
    );
    expect(screen.getByRole("button", { name: "上传附件" })).toBeDisabled();

    rerender(
      <Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId="sess-1" />
    );
    expect(screen.getByRole("button", { name: "上传附件" })).toBeEnabled();
  });

  it("keeps the uploaded safe filename and sends a ready image without text", async () => {
    const onSend = vi.fn();
    vi.mocked(uploadAttachment).mockResolvedValue({
      file_name: "safe-image.png",
      url: "/api/v1/uploads/sess-1/safe-image.png",
      media_type: "image/png",
      size_bytes: 100,
      width: 120,
      height: 80,
      sha256: "a".repeat(64),
    });
    const { container } = render(
      <Composer disabled={false} running={false} onSend={onSend} onCancel={vi.fn()} sessionId="sess-1" />
    );
    const file = new File(["image"], "original.png", { type: "image/png" });

    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
    await waitFor(() => expect(screen.getByLabelText("发送")).toBeEnabled());
    fireEvent.click(screen.getByLabelText("发送"));

    expect(onSend).toHaveBeenCalledWith("", [expect.objectContaining({
      fileName: "safe-image.png",
      displayName: "original.png",
      url: "/api/v1/uploads/sess-1/safe-image.png",
      status: "ready",
    })]);
  });

  it("blocks sending during upload and lets a failed attachment retry or be removed", async () => {
    let rejectUpload: ((reason?: unknown) => void) | undefined;
    vi.mocked(uploadAttachment).mockImplementationOnce(() => new Promise((_resolve, reject) => {
      rejectUpload = reject;
    }));
    const { container } = render(
      <Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId="sess-1" />
    );
    const file = new File(["image"], "failed.png", { type: "image/png" });

    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
    expect(screen.getByLabelText("发送")).toBeDisabled();
    rejectUpload?.(new Error("network"));
    const retry = await screen.findByRole("button", { name: "重试附件 failed.png" });
    expect(screen.getByRole("button", { name: "移除附件 failed.png" })).toBeEnabled();

    vi.mocked(uploadAttachment).mockResolvedValueOnce({
      file_name: "retried.png",
      url: "/api/v1/uploads/sess-1/retried.png",
      media_type: "image/png",
      size_bytes: 100,
      width: 100,
      height: 100,
      sha256: "b".repeat(64),
    });
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByLabelText("发送")).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "移除附件 failed.png" }));

    expect(screen.queryByRole("img", { name: "failed.png" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("发送")).toBeDisabled();
  });

  it("shows the server rejection reason for a failed upload", async () => {
    vi.mocked(uploadAttachment).mockRejectedValueOnce(
      Object.assign(new Error("仅支持 JPEG、PNG 或 WebP 图片"), { status: 415 }),
    );
    const { container } = render(
      <Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId="sess-1" />
    );
    const file = new File(["unsupported"], "image.gif", { type: "image/gif" });

    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });

    expect(await screen.findByRole("status")).toHaveTextContent("仅支持 JPEG、PNG 或 WebP 图片");
  });
});
