import { turnMessages } from "./turn-history";
import type { TurnRecord } from "@/shared/types";

describe("turnMessages attachment restoration", () => {
  it("reconstructs authenticated upload URLs from persisted attachment markers", () => {
    const turn: TurnRecord = {
      turn_id: "turn-1",
      session_id: "session with spaces",
      status: "completed",
      input_text: [
        "分析这张图",
        "",
        "---附件---",
        "[图片] safe-image.png",
        "路径: safe-image.png",
        "---附件结束---",
      ].join("\n"),
      final_text: "完成",
      error_kind: null,
      error_message: null,
      created_at: "2026-08-20T00:00:00Z",
      started_at: "2026-08-20T00:00:01Z",
      completed_at: "2026-08-20T00:00:02Z",
    };

    const [user] = turnMessages(turn);

    expect(user.attachments).toEqual([expect.objectContaining({
      fileName: "safe-image.png",
      url: "/api/v1/uploads/session%20with%20spaces/safe-image.png",
      status: "ready",
    })]);
  });
});
