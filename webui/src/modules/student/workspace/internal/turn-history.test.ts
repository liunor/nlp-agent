import { turnMessages } from "./turn-history";
import type { TurnRecord } from "@/shared/types";

describe("turnMessages attachment restoration", () => {
  it("prefers structured file references and pins preview links to the sent version", () => {
    const turn: TurnRecord = {
      turn_id: "t", session_id: "s", status: "completed", input_text: "summarize",
      file_refs: [{ id: "file-1", name: "报告.docx", sha256: "a".repeat(64), workspace_id: "w", mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }],
      final_text: "done", error_kind: null, error_message: null,
      created_at: "2026-11-25T00:00:00Z", started_at: null, completed_at: null,
    };
    const file = turnMessages(turn)[0].attachments?.[0];
    expect(file).toMatchObject({ fileId: "file-1", expectedHash: "a".repeat(64), displayName: "报告.docx" });
    expect(file?.url).toContain("expected_hash=" + "a".repeat(64));
  });
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
