import { render, screen } from "@testing-library/react";

import { AppErrorBoundary } from "@/shared/ui/AppErrorBoundary";
import { MessageList } from "./MessageList";
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
