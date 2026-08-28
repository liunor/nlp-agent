import { fireEvent, render, screen, waitFor } from "@testing-library/react";

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

  it("renders attachment thumbnails and strips internal attachment markers from content", () => {
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
    const img = screen.getByRole("img", { name: "sample.png" });
    expect(img).toBeVisible();
    expect(img).toHaveAttribute("src", "/api/v1/uploads/sess/sample.png");
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