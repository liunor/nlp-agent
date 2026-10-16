import { describe, expect, it } from "vitest";

import { addKnowledgeBookWhiteboardRef, parseKnowledgeBookWhiteboardRefs, renderKnowledgeBookWhiteboardRefs, stripKnowledgeBookWhiteboardRefs } from "./knowledgeBookRefs";

describe("knowledge-book whiteboard references", () => {
  it("stores a named reference at the front of the lesson and reads it back", () => {
    const markdown = addKnowledgeBookWhiteboardRef("# 注意力\n\n正文", { asset_id: "asset-1", name: "注意力计算过程" });

    expect(markdown.startsWith("<!-- nova-whiteboard")).toBe(true);
    expect(parseKnowledgeBookWhiteboardRefs(markdown)).toEqual([{ asset_id: "asset-1", name: "注意力计算过程" }]);
    expect(stripKnowledgeBookWhiteboardRefs(markdown)).toBe("# 注意力\n\n正文");
  });

  it("keeps multiple references in insertion order", () => {
    const markdown = addKnowledgeBookWhiteboardRef(
      addKnowledgeBookWhiteboardRef("正文", { asset_id: "first", name: "第一张图" }),
      { asset_id: "second", name: "第二张图" },
    );

    expect(parseKnowledgeBookWhiteboardRefs(markdown).map((ref) => ref.asset_id)).toEqual(["second", "first"]);
  });

  it("stores the human-readable asset code alongside the UUID", () => {
    const markdown = addKnowledgeBookWhiteboardRef("正文", { asset_id: "asset-1", asset_code: "WB-000001", name: "第一张图" });

    expect(parseKnowledgeBookWhiteboardRefs(markdown)).toEqual([{ asset_id: "asset-1", asset_code: "WB-000001", name: "第一张图" }]);
  });

  it("inserts the whiteboard marker above the line containing the editor caret", () => {
    const markdown = "第一段\n\n第二段\n第三段";
    const next = addKnowledgeBookWhiteboardRef(markdown, { asset_id: "asset-1", asset_code: "WB-000001", name: "第一张图" }, markdown.indexOf("第三段") + 2);

    expect(next).toBe("第一段\n\n第二段\n<!-- nova-whiteboard asset=\"asset-1\" code=\"WB-000001\" name=\"%E7%AC%AC%E4%B8%80%E5%BC%A0%E5%9B%BE\" -->\n\n第三段");
  });

  it("turns markers into inline whiteboard links without moving their source line", () => {
    const markdown = "## 核心概念\n\n<!-- nova-whiteboard asset=\"asset-1\" code=\"WB-000001\" name=\"%E6%B3%A8%E6%84%8F%E5%8A%9B\" -->\n\n正文";
    const rendered = renderKnowledgeBookWhiteboardRefs(markdown);

    expect(rendered).toContain("[查看图画](nova-whiteboard://asset-1?code=WB-000001&name=%E6%B3%A8%E6%84%8F%E5%8A%9B)");
    expect(rendered.split("\n")).toHaveLength(markdown.split("\n").length);
  });

  it("does not crash on a legacy marker with malformed percent encoding", () => {
    expect(() => renderKnowledgeBookWhiteboardRefs(
      '<!-- nova-whiteboard asset="asset-1" name="坏%名称" -->',
    )).not.toThrow();
  });
});
