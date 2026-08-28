import { render, screen } from "@testing-library/react";
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

  it("hides guided-session protocol metadata, including an incomplete streaming marker", () => {
    const complete = "请先判断哪一类词更重要。\n<!-- guided-result: {\"status\":\"continue\",\"known_concepts\":[],\"misconceptions\":[]} -->";
    const { rerender } = render(<MarkdownContent>{complete}</MarkdownContent>);

    expect(screen.getByText("请先判断哪一类词更重要。")).toBeInTheDocument();
    expect(screen.queryByText(/guided-result/)).not.toBeInTheDocument();
    expect(stripInternalChatMetadata(complete)).toBe("请先判断哪一类词更重要。");

    rerender(<MarkdownContent streaming>{"继续思考。<!-- guided-result: {\"status\":"}</MarkdownContent>);
    expect(screen.getByText("继续思考。")).toBeInTheDocument();
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

  it("exposes copy and ask-Nova actions only when lesson code actions are enabled", async () => {
    const user = userEvent.setup();
    const askNova = vi.fn();
    const openInSandbox = vi.fn();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });

    render(<MarkdownContent codeActions={{ onAskNova: askNova, onOpenInSandbox: openInSandbox }}>{"```python\nprint('hello')\n```"}</MarkdownContent>);

    expect(screen.getByRole("button", { name: "复制 python 代码" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "复制 python 代码" }));
    expect(await screen.findByRole("button", { name: "已复制 python 代码" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "问 Nova" }));
    expect(askNova).toHaveBeenCalledWith("print('hello')", "python");
    await user.click(screen.getByRole("button", { name: "在沙箱中打开" }));
    expect(openInSandbox).toHaveBeenCalledWith("print('hello')", "python");
  });

  it("keeps the copy action when Nova actions are unavailable", () => {
    render(<MarkdownContent codeActions={{ }}>{"```python\nprint('hello')\n```"}</MarkdownContent>);

    expect(screen.getByRole("button", { name: "复制 python 代码" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "问 Nova" })).not.toBeInTheDocument();
  });
});
