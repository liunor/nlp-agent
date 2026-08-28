import { describe, expect, it } from "vitest";

import { indexMarkdownHeadings, readKnowledgeBookUrl } from "./knowledgeBook";

describe("knowledge book Markdown headings", () => {
  it("ignores fenced code and creates duplicate-safe anchors", () => {
    const result = indexMarkdownHeadings(["# 章节", "", "## 介绍", "```python", "# not a heading", "```", "## 介绍", "### [训练](#practice)"].join("\n"));

    expect(result.headingIds).toEqual(["章节", "介绍", "介绍-2", "训练"]);
    expect(result.headings.map((heading) => heading.text)).toEqual(["介绍", "介绍", "训练"]);
  });

  it("falls back to a readable section id for symbol-only headings", () => {
    expect(indexMarkdownHeadings("## !!!").headings[0]).toMatchObject({ text: "!!!", id: "section" });
  });

  it("reads a shareable reader URL without changing the active chat context", () => {
    expect(readKnowledgeBookUrl("?tool=knowledge-book&bookPoint=softmax&bookHeading=核心概念")).toEqual({
      tool: "knowledge-book",
      pointId: "softmax",
      headingId: "核心概念",
      demo: false,
    });
  });

  it("reads the explicit demo教材 switch", () => {
    expect(readKnowledgeBookUrl("?tool=knowledge-book&bookDemo=1").demo).toBe(true);
  });
});
