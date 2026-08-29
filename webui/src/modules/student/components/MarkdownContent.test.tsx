import { act, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { MarkdownContent, stripInternalChatMetadata } from "./MarkdownContent";

describe("MarkdownContent LaTeX delimiters", () => {
  it("renders backslash-delimited inline and display formulas with KaTeX", () => {
    const { container } = render(
      <MarkdownContent>{String.raw`行内公式：\(\frac{r}{c}\)

\[
\mathrm{BLEU}=BP \times \mathrm{GM}
\]`}</MarkdownContent>,
    );

    expect(container.querySelectorAll(".katex")).toHaveLength(2);
    expect(container.querySelector(".katex-display")).toBeInTheDocument();
  });

  it("renders absolute-value formulas inside Markdown table cells", () => {
    const { container } = render(
      <MarkdownContent>{String.raw`| 公式 |
| --- |
| \(\mathrm{IDF}=\lg\left(\frac{|D|}{DF}\right)\) |`}</MarkdownContent>,
    );

    expect(container.querySelectorAll("td")).toHaveLength(1);
    expect(container.querySelector("td .katex")).toBeInTheDocument();
  });

  it("leaves LaTeX-looking text inside fenced code blocks untouched", () => {
    const source = ["```latex", String.raw`\[x^2\]`, "```"].join("\n");
    const { container } = render(
      <MarkdownContent>{source}</MarkdownContent>,
    );

    expect(container.querySelector(".katex")).not.toBeInTheDocument();
    expect(container.querySelector("code")).toHaveTextContent(String.raw`\[x^2\]`);
  });

  it("leaves LaTeX-looking text inside inline code untouched", () => {
    const source = ["Use `", String.raw`\(x^2\)`, "` literally."].join("");
    const { container } = render(
      <MarkdownContent>{source}</MarkdownContent>,
    );

    expect(container.querySelector(".katex")).not.toBeInTheDocument();
    expect(container.querySelector("code")).toHaveTextContent(String.raw`\(x^2\)`);
  });

  it("hides guided-session protocol metadata, including an incomplete streaming marker", async () => {
    const complete = "请先判断哪一类词更重要。\n<!-- guided-result: {\"status\":\"continue\",\"known_concepts\":[],\"misconceptions\":[]} -->";
    const { rerender } = render(<MarkdownContent>{complete}</MarkdownContent>);

    expect(screen.getByText("请先判断哪一类词更重要。")).toBeInTheDocument();
    expect(screen.queryByText(/guided-result/)).not.toBeInTheDocument();
    expect(stripInternalChatMetadata(complete)).toBe("请先判断哪一类词更重要。");

    rerender(<MarkdownContent streaming>{"继续思考。<!-- guided-result: {\"status\":"}</MarkdownContent>);
    await waitFor(() => expect(screen.getByText("继续思考。")).toBeInTheDocument());
    expect(screen.queryByText(/guided-result/)).not.toBeInTheDocument();
  });

  it("renders model-provided external links as inert text while keeping same-origin links", () => {
    render(
      <MarkdownContent>{String.raw`[外部资料](https://evil.example/phishing) [反斜杠绕过](/\evil.example/phishing) [课程目录](/teacher) [本节](#attention)`}</MarkdownContent>,
    );

    expect(screen.getByText("外部资料")).not.toHaveAttribute("href");
    expect(screen.getByText("反斜杠绕过")).not.toHaveAttribute("href");
    expect(screen.getByRole("link", { name: "课程目录" })).toHaveAttribute("href", "/teacher");
    expect(screen.getByRole("link", { name: "本节" })).toHaveAttribute("href", "#attention");
  });

  it("renders Markdown image alt text as a figure caption", () => {
    render(<MarkdownContent>{"![图 10.1.2：注意力被引导到书上](/api/v1/learning/book/workspace-1/assets/figure.png)"}</MarkdownContent>);

    expect(screen.getByRole("img", { name: "图 10.1.2：注意力被引导到书上" })).toBeVisible();
    expect(screen.getByText("图 10.1.2：注意力被引导到书上")).toHaveClass("markdown-image-caption");
  });

  it("supports a safe manual image width and lazy image loading", () => {
    render(<MarkdownContent>{'![示意图](/api/v1/learning/book/workspace-1/assets/figure.png "width=320px")'}</MarkdownContent>);

    const image = screen.getByRole("img", { name: "示意图" });
    expect(image).toHaveStyle({ width: "320px" });
    expect(image).toHaveAttribute("loading", "lazy");
    expect(image).toHaveAttribute("decoding", "async");
  });

  it("puts indexed lesson headings behind dedicated invisible anchors", () => {
    const { container } = render(<MarkdownContent headingIds={["lesson-title", "attention-score"]}>{"# 课程标题\n\n## 注意力评分"}</MarkdownContent>);

    const anchor = container.querySelector("#attention-score");
    expect(anchor).toHaveAttribute("data-knowledge-book-heading-anchor", "true");
    expect(anchor).toHaveClass("knowledge-book-heading-anchor");
    expect(screen.getByRole("heading", { name: "注意力评分" })).toHaveAttribute("data-knowledge-book-heading-id", "attention-score");
  });

  it("keeps a later heading mapped to its own source heading when Markdown contains nested headings", () => {
    render(<MarkdownContent headingIdsByLine={{ 1: "10.2", 5: "10.3" }}>{"## 10.2 注意力汇聚\n\n> ### 引用中的标题\n\n## 10.3 注意力评分函数"}</MarkdownContent>);

    expect(screen.getByRole("heading", { name: "10.3 注意力评分函数" })).toHaveAttribute("data-knowledge-book-heading-id", "10.3");
  });

  it("exposes copy and ask-Nova actions only when lesson code actions are enabled", async () => {
    const user = userEvent.setup();
    const askNova = vi.fn();
    const openInSandbox = vi.fn();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });

    render(<MarkdownContent codeActions={{ onAskNova: askNova, onOpenInSandbox: openInSandbox }}>{"```python\nprint('hello')\n```"}</MarkdownContent>);

    expect(screen.getByRole("button", { name: "复制 python 代码" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复制 python 代码" }));
    expect(await screen.findByRole("button", { name: "已复制 python 代码" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "询问 Nova" }));
    expect(askNova).toHaveBeenCalledWith("print('hello')", "python");
    await user.click(screen.getByRole("button", { name: "在沙箱中打开" }));
    expect(openInSandbox).toHaveBeenCalledWith("print('hello')", "python");
  });

  it("keeps the copy action when Nova actions are unavailable", () => {
    render(<MarkdownContent codeActions={{ }}>{"```python\nprint('hello')\n```"}</MarkdownContent>);

    expect(screen.getByRole("button", { name: "复制 python 代码" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "询问 Nova" })).not.toBeInTheDocument();
  });

  it("shows a copy action for fenced code on the main page", () => {
    render(<MarkdownContent>{"```javascript\nconsole.log('hello');\n```"}</MarkdownContent>);

    expect(screen.getByRole("button", { name: "复制 javascript 代码" })).toBeInTheDocument();
  });

  it("uses the same roomy code card for fenced code without a language", () => {
    const { container } = render(<MarkdownContent>{"```\nplain text\n```"}</MarkdownContent>);

    expect(container.querySelector(".code-shell")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制 text 代码" })).toBeInTheDocument();
  });

  it("renders Markdown incrementally during streaming", async () => {
    const { container, rerender } = render(<MarkdownContent streaming streamRenderIntervalMs={0}>{"## 流式标题"}</MarkdownContent>);

    expect(screen.getByRole("heading", { name: "流式标题" })).toBeVisible();

    rerender(<MarkdownContent streaming streamRenderIntervalMs={0}>{"## 流式标题\n\n```python\nprint('streaming')\n"}</MarkdownContent>);

    await waitFor(() => expect(container.querySelector(".code-shell")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "复制 python 代码" })).toBeVisible();
  });

  it("keeps refreshing throttled streaming Markdown under StrictMode", () => {
    vi.useFakeTimers();
    try {
      const { rerender } = render(
        <StrictMode>
          <MarkdownContent streaming streamRenderIntervalMs={30}>第一段</MarkdownContent>
        </StrictMode>,
      );

      rerender(
        <StrictMode>
          <MarkdownContent streaming streamRenderIntervalMs={30}>第一段第二段</MarkdownContent>
        </StrictMode>,
      );

      act(() => vi.advanceTimersByTime(31));

      expect(document.querySelector(".markdown-streaming-preview")).toHaveTextContent("第一段第二段");
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders a completed Markdown table while streaming", () => {
    const source = "| 名称 | 说明 |\n| --- | --- |\n| `message` | \\(x^2\\) |";
    const { container } = render(<MarkdownContent streaming streamRenderIntervalMs={0}>{source}</MarkdownContent>);

    expect(container.querySelector("table")).toBeInTheDocument();
    expect(container.querySelector("thead")).toHaveTextContent("名称");
    expect(container.querySelector("tbody")).toHaveTextContent("message");
    expect(container.querySelector("tbody .katex")).toBeInTheDocument();
  });

  it("renders inline code while streaming", () => {
    const { container } = render(<MarkdownContent streaming streamRenderIntervalMs={0}>请使用 `message` 变量。</MarkdownContent>);

    expect(container.querySelector("code")).toHaveTextContent("message");
  });

  it("renders LaTeX formulas while streaming", () => {
    const { container } = render(<MarkdownContent streaming streamRenderIntervalMs={0}>{String.raw`公式：\(x^2 + y^2\)`}</MarkdownContent>);

    expect(container.querySelector(".katex")).toBeInTheDocument();
  });

  it("decodes HTML character references while streaming", () => {
    const source = "前&#x20;后";
    const { container } = render(<MarkdownContent streaming streamRenderIntervalMs={0}>{source}</MarkdownContent>);

    expect(container.textContent).toContain("前 后");
    expect(container.textContent).not.toContain("&#x20;");
  });

  it("keeps LaTeX formulas rendered before and after streaming completes", () => {
    const source = String.raw`推理过程：\(x^2 + y^2\)`;
    const { container, rerender } = render(<MarkdownContent streaming streamRenderIntervalMs={0}>{source}</MarkdownContent>);

    expect(container.querySelector(".katex")).toBeInTheDocument();

    rerender(<MarkdownContent>{source}</MarkdownContent>);

    expect(container.querySelector(".katex")).toBeInTheDocument();
  });
});
