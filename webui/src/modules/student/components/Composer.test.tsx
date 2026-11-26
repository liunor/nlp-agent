import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { uploadAttachment } from "@/platform/http/api";
import { Composer } from "./Composer";

vi.mock("@/platform/http/api", () => ({ uploadAttachment: vi.fn() }));

function clipboardFileItem(file: File): DataTransferItem {
  return {
    kind: "file",
    type: file.type,
    getAsFile: () => file,
  } as DataTransferItem;
}

function clipboardTextItem(): DataTransferItem {
  return {
    kind: "string",
    type: "text/plain",
    getAsFile: () => null,
  } as DataTransferItem;
}

function createPasteEvent(items: DataTransferItem[], files: File[] = []): Event {
  const event = new Event("paste", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "clipboardData", {
    value: { items, files },
  });
  return event;
}

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

  it("enables attachment upload when a session exists or can be created on demand", () => {
    const { rerender } = render(
      <Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId={null} />
    );
    expect(screen.getByRole("button", { name: "上传附件" })).toBeDisabled();

    rerender(
      <Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId={null} onEnsureSession={vi.fn()} />
    );
    expect(screen.getByRole("button", { name: "上传附件" })).toBeEnabled();

    rerender(
      <Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId="sess-1" />
    );
    expect(screen.getByRole("button", { name: "上传附件" })).toBeEnabled();
  });

  it("creates a session before uploading the first image in a new chat", async () => {
    const onEnsureSession = vi.fn().mockResolvedValue("session-new");
    vi.mocked(uploadAttachment).mockResolvedValue({
      file_name: "safe-image.png",
      url: "/api/v1/uploads/session-new/safe-image.png",
      media_type: "image/png",
      size_bytes: 100,
      width: 120,
      height: 80,
      sha256: "a".repeat(64),
    });
    const { container, rerender } = render(
      <Composer
        disabled={false}
        running={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
        sessionId={null}
        onEnsureSession={onEnsureSession}
      />
    );
    const file = new File(["image"], "first-image.png", { type: "image/png" });

    expect(screen.getByRole("button", { name: "上传附件" })).toBeEnabled();
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });

    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith("session-new", file));
    expect(onEnsureSession).toHaveBeenCalledOnce();
    expect(screen.getByRole("img", { name: "first-image.png" })).toBeVisible();
    rerender(
      <Composer
        disabled={false}
        running={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
        sessionId="session-new"
        onEnsureSession={onEnsureSession}
      />
    );
    expect(screen.getByLabelText("发送")).toBeEnabled();
  });

  it("uploads every pasted image and suppresses clipboard fallback text", async () => {
    vi.mocked(uploadAttachment).mockResolvedValue({
      file_name: "safe-image.png",
      url: "/api/v1/uploads/sess-1/safe-image.png",
      media_type: "image/png",
      size_bytes: 100,
      width: 120,
      height: 80,
      sha256: "a".repeat(64),
    });
    render(<Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId="sess-1" />);
    const firstImage = new File(["first"], "", { type: "image/png" });
    const secondImage = new File(["second"], "diagram.webp", { type: "image/webp" });
    const paste = createPasteEvent([
      clipboardFileItem(firstImage),
      clipboardTextItem(),
      clipboardFileItem(secondImage),
    ]);

    fireEvent(screen.getByLabelText("学习问题"), paste);

    expect(paste.defaultPrevented).toBe(true);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(2));
    expect(uploadAttachment).toHaveBeenNthCalledWith(1, "sess-1", firstImage);
    expect(uploadAttachment).toHaveBeenNthCalledWith(2, "sess-1", secondImage);
    expect(screen.getByRole("img", { name: "pasted-image.png" })).toBeVisible();
    expect(screen.getByRole("img", { name: "diagram.webp" })).toBeVisible();
  });

  it("leaves text-only paste to the native textarea behavior", () => {
    render(<Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId="sess-1" />);
    const paste = createPasteEvent([clipboardTextItem()]);

    fireEvent(screen.getByLabelText("学习问题"), paste);

    expect(paste.defaultPrevented).toBe(false);
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  it("does not upload pasted images while a turn is running", () => {
    const onEnsureSession = vi.fn().mockResolvedValue("session-new");
    render(<Composer disabled={false} running onSend={vi.fn()} onCancel={vi.fn()} sessionId={null} onEnsureSession={onEnsureSession} />);
    const image = new File(["image"], "running.png", { type: "image/png" });
    const paste = createPasteEvent([clipboardFileItem(image)]);

    fireEvent(screen.getByLabelText("学习问题"), paste);

    expect(paste.defaultPrevented).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent("当前正在生成，暂不能上传图片");
    expect(uploadAttachment).not.toHaveBeenCalled();
    expect(onEnsureSession).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("does not let paste bypass an unavailable attachment upload", () => {
    render(<Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId={null} />);
    const image = new File(["image"], "signed-out.png", { type: "image/png" });
    const paste = createPasteEvent([clipboardFileItem(image)]);

    fireEvent(screen.getByLabelText("学习问题"), paste);

    expect(paste.defaultPrevented).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent("请先登录后再上传图片");
    expect(screen.getByRole("button", { name: "上传附件" })).toBeDisabled();
    expect(uploadAttachment).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("clears an in-flight attachment when the composer conversation scope changes", async () => {
    let resolveUpload!: (response: {
      file_name: string;
      url: string;
      media_type: string;
      size_bytes: number;
      width: number;
      height: number;
      sha256: string;
    }) => void;
    vi.mocked(uploadAttachment).mockReturnValue(new Promise((resolve) => { resolveUpload = resolve; }));
    const onSend = vi.fn();
    const composer = (scope: number, sessionId: string) => (
      <Composer key={scope} disabled={false} running={false} onSend={onSend} onCancel={vi.fn()} sessionId={sessionId} />
    );
    const { container, rerender } = render(composer(0, "session-a"));
    const file = new File(["old-image"], "old-session.png", { type: "image/png" });

    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
    expect(screen.getByRole("img", { name: "old-session.png" })).toBeVisible();

    rerender(composer(1, "session-b"));

    expect(screen.queryByRole("img", { name: "old-session.png" })).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:attachment-preview");
    fireEvent.change(screen.getByLabelText("学习问题"), { target: { value: "新会话问题" } });
    fireEvent.click(screen.getByLabelText("发送"));
    expect(onSend).toHaveBeenCalledWith("新会话问题");

    await act(async () => {
      resolveUpload({
        file_name: "old-safe.png",
        url: "/api/v1/uploads/session-a/old-safe.png",
        media_type: "image/png",
        size_bytes: 100,
        width: 120,
        height: 80,
        sha256: "a".repeat(64),
      });
      await Promise.resolve();
    });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does not send an attachment whose owner session no longer matches", async () => {
    const onSend = vi.fn();
    vi.mocked(uploadAttachment).mockResolvedValue({
      file_name: "session-a.png",
      url: "/api/v1/uploads/session-a/session-a.png",
      media_type: "image/png",
      size_bytes: 100,
      width: 120,
      height: 80,
      sha256: "a".repeat(64),
    });
    const { container, rerender } = render(
      <Composer disabled={false} running={false} onSend={onSend} onCancel={vi.fn()} sessionId="session-a" />
    );
    const file = new File(["image"], "session-a.png", { type: "image/png" });
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [file] } });
    await waitFor(() => expect(screen.getByLabelText("发送")).toBeEnabled());

    rerender(<Composer disabled={false} running={false} onSend={onSend} onCancel={vi.fn()} sessionId="session-b" />);
    fireEvent.change(screen.getByLabelText("学习问题"), { target: { value: "不能携带旧附件" } });

    expect(screen.getByLabelText("发送")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("发送"));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("places attachment upload before branding and learning settings before send", () => {
    const { container } = render(
      <Composer
        disabled={false}
        running={false}
        onSend={vi.fn()}
        onCancel={vi.fn()}
        sessionId="sess-1"
        contextControl={<button type="button">学习设置</button>}
      />
    );
    const toolbar = container.querySelector(".composer-toolbar");
    const visibleControls = Array.from(toolbar?.children ?? []).filter((element) => !element.hasAttribute("hidden"));

    expect(visibleControls).toEqual([
      screen.getByRole("button", { name: "上传附件" }),
      screen.getByText("Nova · LSNU NLP Learning Agent"),
      screen.getByRole("button", { name: "学习设置" }),
      screen.getByRole("button", { name: "发送" }),
    ]);
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

  it("shows the server rejection reason for a failed pasted upload", async () => {
    vi.mocked(uploadAttachment).mockRejectedValueOnce(
      Object.assign(new Error("仅支持 JPEG、PNG 或 WebP 图片"), { status: 415 }),
    );
    render(
      <Composer disabled={false} running={false} onSend={vi.fn()} onCancel={vi.fn()} sessionId="sess-1" />
    );
    const file = new File(["unsupported"], "image.gif", { type: "image/gif" });

    fireEvent(screen.getByLabelText("学习问题"), createPasteEvent([clipboardFileItem(file)]));

    expect(await screen.findByRole("status")).toHaveTextContent("仅支持 JPEG、PNG 或 WebP 图片");
  });
});
