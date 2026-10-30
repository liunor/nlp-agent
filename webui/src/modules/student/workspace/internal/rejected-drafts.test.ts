import { clearRejectedDrafts, readRejectedDrafts, removeRejectedDraft, saveRejectedDraft } from "./rejected-drafts";
import type { ChatMessage } from "@/shared/types";

const draft: ChatMessage = { id: "request:user", turnId: "request", role: "user", content: "原问题",
  status: "failed", createdAt: "2026-10-29T00:00:00Z" };

beforeEach(() => sessionStorage.clear());

it("isolates saved questions by account and conversation and removes just the selected question", () => {
  saveRejectedDraft("a", "s1", draft);
  saveRejectedDraft("a", "s1", { ...draft, id: "another:user", turnId: "another", content: "另一问题" });
  saveRejectedDraft("b", "s1", { ...draft, content: "另一个账号" });
  saveRejectedDraft("a", "s2", { ...draft, content: "另一个会话" });
  expect(readRejectedDrafts("a", "s1").map((item) => item.content)).toEqual(["原问题", "另一问题"]);
  expect(readRejectedDrafts("b", "s1")[0].content).toBe("另一个账号");
  expect(readRejectedDrafts("a", "s2")[0].content).toBe("另一个会话");
  removeRejectedDraft("a", "s1", draft.id);
  expect(readRejectedDrafts("a", "s1").map((item) => item.content)).toEqual(["另一问题"]);
  clearRejectedDrafts("a");
  expect(readRejectedDrafts("a", "s1")).toEqual([]);
  expect(readRejectedDrafts("a", "s2")).toEqual([]);
  expect(readRejectedDrafts("b", "s1")).toHaveLength(1);
});

it("does not save questions for an unidentified account", () => {
  saveRejectedDraft(null, "s1", draft);
  expect(sessionStorage.length).toBe(0);
});

it("keeps earlier failed questions when the list grows", () => {
  for (let i = 0; i < 25; i++) saveRejectedDraft("a", "s1", { ...draft, id: `question-${i}` });
  expect(readRejectedDrafts("a", "s1")).toHaveLength(25);
});
