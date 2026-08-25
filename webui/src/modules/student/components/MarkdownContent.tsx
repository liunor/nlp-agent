import { Children, lazy, Suspense, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import "katex/dist/katex.min.css";

const LazyCode = lazy(async () => {
  const [{ default: SyntaxHighlighter }, { default: oneDark }, { default: oneLight }] = await Promise.all([
    import("react-syntax-highlighter/dist/esm/prism-async-light"),
    import("react-syntax-highlighter/dist/esm/styles/prism/one-dark"),
    import("react-syntax-highlighter/dist/esm/styles/prism/one-light"),
  ]);
  return {
    default({ language, code, dark }: { language: string; code: string; dark: boolean }) {
      return <SyntaxHighlighter language={language} style={dark ? oneDark : oneLight} customStyle={{ margin: 0, borderRadius: "0 0 12px 12px", fontSize: 13 }}>{code}</SyntaxHighlighter>;
    },
  };
});

function headingText(children: ReactNode): string {
  return Children.toArray(children).join("");
}

function normalizeFormulaContent(content: string): string {
  return content.replace(/(?<!\\)\|([^|\r\n]+?)(?<!\\)\|/g, "\\lvert $1\\rvert");
}

function normalizeFormulaDelimiters(text: string): string {
  const withDollarDelimiters = text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_match, content: string) => `$$\n${normalizeFormulaContent(content.trim())}\n$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, content: string) => `$${normalizeFormulaContent(content)}$`);
  return withDollarDelimiters.replace(/\$([^$\r\n]+)\$/g, (_match, content: string) => `$${normalizeFormulaContent(content)}$`);
}

function normalizeTextOutsideCode(text: string): string {
  return text.split(/(`+[\s\S]*?`+)/g).map((segment, index) => index % 2 ? segment : normalizeFormulaDelimiters(segment)).join("");
}

function normalizeLatexDelimiters(markdown: string): string {
  let result = "";
  let cursor = 0;
  const fenceStart = /^(?: {0,3})(`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)/gm;
  let opening: RegExpExecArray | null;

  while ((opening = fenceStart.exec(markdown))) {
    if (opening.index < cursor) continue;
    result += normalizeTextOutsideCode(markdown.slice(cursor, opening.index));
    const marker = opening[1];
    const fenceEnd = new RegExp(`^(?: {0,3})${marker[0]}{${marker.length},}[^\\r\\n]*(?:\\r?\\n|$)`, "gm");
    fenceEnd.lastIndex = opening.index + opening[0].length;
    const closing = fenceEnd.exec(markdown);
    if (!closing) return result + markdown.slice(opening.index);
    result += markdown.slice(opening.index, fenceEnd.lastIndex);
    cursor = fenceEnd.lastIndex;
    fenceStart.lastIndex = cursor;
  }

  return result + normalizeTextOutsideCode(markdown.slice(cursor));
}

function isSameOriginMarkdownLink(href: string | undefined): href is string {
  if (!href) return false;
  if (href.startsWith("#")) return true;
  if (!href.startsWith("/")) return false;
  if (/\\|%5c/i.test(href)) return false;
  try {
    return new URL(href, window.location.href).origin === window.location.origin;
  } catch {
    return false;
  }
}

/** Internal protocol metadata is parsed by the gateway and must never be shown or copied as lesson content. */
export function stripInternalChatMetadata(content: string): string {
  return content.replace(/\s*<!--\s*guided-result\s*:\s*(?:\{[\s\S]*?\}\s*-->|[\s\S]*$)/gi, "").trimEnd();
}

export function MarkdownContent({ children, streaming = false }: { children: string; streaming?: boolean }) {
  const dark = document.documentElement.classList.contains("dark");
  return (
    <div className="markdown-content prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-20 prose-pre:p-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          h2: ({ children: value }) => {
            const text = headingText(value);
            const educational = /练习|思考|核心|概念|误区|总结/.test(text);
            return <h2 className={educational ? "education-heading" : undefined}>{value}</h2>;
          },
          code: ({ className, children: value, ...props }) => {
            const match = /language-([\w-]+)/.exec(className ?? "");
            const content = String(value).replace(/\n$/, "");
            if (!match) return <code className={className} {...props}>{value}</code>;
            return (
              <div className="code-shell">
                <div className="code-label">{match[1]}</div>
                <Suspense fallback={<pre><code>{content}</code></pre>}>
                  <LazyCode language={match[1]} code={content} dark={dark} />
                </Suspense>
              </div>
            );
          },
          a: ({ children: value, href, ...props }) => isSameOriginMarkdownLink(href)
            ? <a {...props} href={href}>{value}</a>
            : <span className="external-link-removed">{value}</span>,
        }}
      >
        {normalizeLatexDelimiters(stripInternalChatMetadata(children) || (streaming ? "" : "暂无内容"))}
      </ReactMarkdown>
      {streaming && <span className="stream-caret" aria-label="正在生成" />}
    </div>
  );
}
