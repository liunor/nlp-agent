import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { AppErrorBoundary } from "@/shared/ui/AppErrorBoundary";
import {
  copyTextToClipboard,
  MessageList,
} from "./MessageList";
import type { ChatMessage } from "@/shared/types";

const message = (id: string, content: string): ChatMessage => ({ id, turnId: id, role: "user", content, createdAt: "2026-07-19T00:00:00Z" });
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;

describe("MessageList session updates", () => {
  afterEach(() => {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: originalScrollIntoView });
  });

  it("does not treat the browser scrollIntoView return value as an effect cleanup", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn(() => Promise.resolve()) });
    const view = (messages: ChatMessage[]) => <AppErrorBoundary><MessageList messages={messages} loading={false} showReasoning={false} onFollowUp={vi.fn()} /></AppErrorBoundary>;

    const { rerender } = render(view([message("turn-1", "第一个会话")]));
    rerender(view([message("turn-2", "第二个会话")]));

    expect(screen.getByText("第二个会话")).toBeVisible();
    expect(screen.queryByText("页面未能正常显示")).not.toBeInTheDocument();
    consoleError.mockRestore();
  });

  it("renders attachment as clickable button instead of target=_blank link, and opens in-page image preview dialog with matching src", async () => {
    const testMsg: ChatMessage = {
      id: "turn-3",
      turnId: "turn-3",
      role: "user",
      content: "分析这张图\n\n---附件---\n[图片] sample.png\n---附件结束---",
      createdAt: "2026-07-19T00:00:00Z",
      attachments: [
        {
          fileName: "sample.png",
          url: "/api/v1/uploads/sess/sample.png",
          mediaType: "image/png",
          width: 100,
          height: 100,
          status: "ready",
        },
      ],
    };

    render(
      <AppErrorBoundary>
        <MessageList messages={[testMsg]} loading={false} showReasoning={false} onFollowUp={vi.fn()} />
      </AppErrorBoundary>
    );

    expect(screen.getByText("分析这张图")).toBeVisible();
    expect(screen.queryByText(/---附件---/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();

    const img = screen.getByRole("img", { name: "sample.png" });
    expect(img).toBeVisible();
    expect(img).toHaveAttribute("src", "/api/v1/uploads/sess/sample.png");
    expect(img).toHaveAttribute("loading", "eager");
    expect(screen.getByRole("img", { name: "sample.png" }).closest(".message-attachments")).toHaveClass("has-content");

    const trigger = screen.getByRole("button", { name: "查看原图：sample.png" });
    fireEvent.click(trigger);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    const dialogImg = within(dialog).getByRole("img", { name: "sample.png" });
    expect(dialogImg).toHaveAttribute("src", "/api/v1/uploads/sess/sample.png");

    const closeBtn = screen.getByRole("button", { name: "关闭原图预览" });
    fireEvent.click(closeBtn);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes the image preview dialog via Escape key", async () => {
    const testMsg: ChatMessage = {
      id: "turn-esc",
      turnId: "turn-esc",
      role: "user",
      content: "按ESC关闭",
      createdAt: "2026-07-19T00:00:00Z",
      attachments: [
        {
          fileName: "escape.png",
          url: "/api/v1/uploads/sess/escape.png",
          mediaType: "image/png",
          width: 100,
          height: 100,
          status: "ready",
        },
      ],
    };

    render(
      <AppErrorBoundary>
        <MessageList messages={[testMsg]} loading={false} showReasoning={false} onFollowUp={vi.fn()} />
      </AppErrorBoundary>
    );

    fireEvent.click(screen.getByRole("button", { name: "查看原图：escape.png" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();

    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("supports multiple image attachments and opens the corresponding image on click", async () => {
    const multiMsg: ChatMessage = {
      id: "turn-multi",
      turnId: "turn-multi",
      role: "user",
      content: "对比两张图片",
      createdAt: "2026-07-19T00:00:00Z",
      attachments: [
        { fileName: "first.png", url: "/api/v1/uploads/sess/first.png", mediaType: "image/png", width: 100, height: 100, status: "ready" },
        { fileName: "second.png", url: "/api/v1/uploads/sess/second.png", mediaType: "image/png", width: 100, height: 100, status: "ready" },
      ],
    };

    render(
      <AppErrorBoundary>
        <MessageList messages={[multiMsg]} loading={false} showReasoning={false} onFollowUp={vi.fn()} />
      </AppErrorBoundary>
    );

    fireEvent.click(screen.getByRole("button", { name: "查看原图：second.png" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("img", { name: "second.png" })).toHaveAttribute("src", "/api/v1/uploads/sess/second.png");
  });

  it("allows previewing images recovered from turn history markdown attachment block", async () => {
    const historyMsg: ChatMessage = {
      id: "turn-hist",
      turnId: "turn-hist",
      role: "user",
      content: "看这个公式\n\n---附件---\n![formula.png](/api/v1/uploads/sess/formula.png)\n---附件结束---",
      createdAt: "2026-07-19T00:00:00Z",
    };

    render(
      <AppErrorBoundary>
        <MessageList messages={[historyMsg]} loading={false} showReasoning={false} onFollowUp={vi.fn()} />
      </AppErrorBoundary>
    );

    fireEvent.click(screen.getByRole("button", { name: "查看原图：formula.png" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("img", { name: "formula.png" })).toHaveAttribute("src", "/api/v1/uploads/sess/formula.png");
  });

  it("displays fallback text and does not open dialog when attachment has no url", () => {
    const noUrlMsg: ChatMessage = {
      id: "turn-nourl",
      turnId: "turn-nourl",
      role: "user",
      content: "上传失败的图片",
      createdAt: "2026-07-19T00:00:00Z",
      attachments: [
        { fileName: "broken.png", url: "", mediaType: "image/png", width: 0, height: 0, status: "error" },
      ],
    };

    render(
      <AppErrorBoundary>
        <MessageList messages={[noUrlMsg]} loading={false} showReasoning={false} onFollowUp={vi.fn()} />
      </AppErrorBoundary>
    );

    expect(screen.getByText("broken.png")).toBeVisible();
    expect(screen.queryByRole("button", { name: /查看原图/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not add content spacing when the user message only contains an image", () => {
    const imageOnlyMsg: ChatMessage = {
      id: "turn-image-only",
      turnId: "turn-image-only",
      role: "user",
      content: "",
      createdAt: "2026-07-19T00:00:00Z",
      attachments: [
        {
          fileName: "only.png",
          url: "/api/v1/uploads/sess/only.png",
          mediaType: "image/png",
          width: 100,
          height: 100,
          status: "ready",
        },
      ],
    };

    render(
      <AppErrorBoundary>
        <MessageList messages={[imageOnlyMsg]} loading={false} showReasoning={false} onFollowUp={vi.fn()} />
      </AppErrorBoundary>
    );

    expect(screen.getByRole("img", { name: "only.png" }).closest(".message-attachments")).not.toHaveClass("has-content");
  });

  it("keeps partial assistant content visible when the turn fails", () => {
    const failedAssistant: ChatMessage = {
      id: "turn-failed-assistant",
      turnId: "turn-failed",
      role: "assistant",
      content: "已经生成的部分答案",
      reasoning: "已经完成思考",
      status: "failed",
      createdAt: "2026-07-19T00:02:00Z",
      startedAt: "2026-07-19T00:02:00Z",
      completedAt: "2026-07-19T00:02:05Z",
    };

    render(
      <MessageList
        messages={[failedAssistant]}
        loading={false}
        showReasoning={false}
        onFollowUp={vi.fn()}
      />
    );

    expect(screen.getByText("已经生成的部分答案")).toBeVisible();
    expect(screen.getByText(/已保留已生成内容/)).toBeVisible();
    expect(screen.getByRole("button", { name: /已处理 5s/ })).toBeVisible();
  });
});
it("copies only the selected assistant response as Markdown", async () => {
  const clipboardDescriptor = Object.getOwnPropertyDescriptor(
    navigator,
    "clipboard"
  );
  const writeText = vi.fn(() => Promise.resolve());

  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });

  const messages: ChatMessage[] = [
    {
      id: "turn-user",
      turnId: "turn-user",
      role: "user",
      content: "什么是词向量？",
      createdAt: "2026-07-19T00:00:00Z",
    },
    {
      id: "turn-assistant",
      turnId: "turn-assistant",
      role: "assistant",
      content: "## 定义\n\n- **要点**",
      createdAt: "2026-07-19T00:01:00Z",
    },
  ];

  try {
    render(
      <MessageList
        messages={messages}
        loading={false}
        showReasoning={false}
        onFollowUp={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "复制" }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith("## 定义\n\n- **要点**");
    });
  } finally {
    if (clipboardDescriptor) {
      Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
    } else {
      Reflect.deleteProperty(navigator, "clipboard");
    }
  }
});
      it("falls back when the Clipboard API is unavailable", async () => {
    const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    const execCommandDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
    const execCommand = vi.fn(() => true);

    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: execCommand,
    });

    try {
      await copyTextToClipboard("# 会话记录");

      expect(execCommand).toHaveBeenCalledWith("copy");
      expect(document.querySelector("textarea")).not.toBeInTheDocument();
    } finally {
      if (clipboardDescriptor) {
        Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
      } else {
        Reflect.deleteProperty(navigator, "clipboard");
      }

      if (execCommandDescriptor) {
        Object.defineProperty(document, "execCommand", execCommandDescriptor);
      } else {
        Reflect.deleteProperty(document, "execCommand");
      }
    }
  });
