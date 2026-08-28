import { Check, Copy, ExternalLink, MessageCircleQuestion } from "lucide-react";
import { Children, lazy, Suspense, useEffect, useState, type ReactNode } from "react";
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
      return <SyntaxHighlighter language={language} style={dark ? oneDark : oneLight} customStyle={{ margin: 0, borderRadius: 0, fontSize: 14, background: dark ? "#202124" : "#f3f4f6" }}>{code}</SyntaxHighlighter>;
    },
  };
});

export interface MarkdownCodeActions {
  onAskNova?: (code: string, language: string) => void;
  onOpenInSandbox?: (code: string, language: string) => void;
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "true");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("clipboard copy failed");
}

function LessonCodeBlock({ code, dark, language, actions }: { code: string; dark: boolean; language: string; actions?: MarkdownCodeActions }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "error">("idle");
  useEffect(() => {
    if (copyStatus === "idle") return undefined;
    const timer = window.setTimeout(() => setCopyStatus("idle"), 1800);
    return () => window.clearTimeout(timer);
  }, [copyStatus]);

  const copy = async () => {
    try {
      await copyText(code);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("error");
    }
  };

  const supportsLessonActions = /^(?:python|pytorch|py)$/i.test(language);
  const lessonActions = supportsLessonActions ? actions : undefined;
  return <div className="code-shell">
    <div className="code-toolbar">
      <div className="code-label">{language}</div>
      {lessonActions && <div className="code-actions">
        <button type="button" aria-label={`${copyStatus === "copied" ? "已复制" : "复制"} ${language} 代码`} onClick={() => void copy()}>{copyStatus === "copied" ? <Check size={13} /> : <Copy size={13} />}{copyStatus === "copied" ? "已复制" : "复制"}</button>
        {lessonActions.onAskNova && <button type="button" aria-label="问 Nova" onClick={() => lessonActions.onAskNova?.(code, language)}><MessageCircleQuestion size={13} />问 Nova</button>}
        {lessonActions.onOpenInSandbox && <button type="button" aria-label="在沙箱中打开" onClick={() => lessonActions.onOpenInSandbox?.(code, language)}><ExternalLink size={13} />在沙箱中打开</button>}
        <span className="sr-only" aria-live="polite">{copyStatus === "copied" ? `已复制 ${language} 代码` : copyStatus === "error" ? `复制 ${language} 代码失败` : ""}</span>
        {copyStatus === "error" && <span className="code-action-status" role="status">复制失败</span>}
      </div>}
    </div>
    <Suspense fallback={<pre><code>{code}</code></pre>}>
      <LazyCode language={language} code={code} dark={dark} />
    </Suspense>
  </div>;
}

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

function isSafeMarkdownImage(src: string | undefined, allowDataImages = false): src is string {
  if (!src || src.startsWith("#")) return false;
  if (/^data:/i.test(src)) return allowDataImages && /^data:image\/(?:png|jpe?g|gif|webp);base64,/i.test(src);
  try {
    return new URL(src, window.location.href).origin === window.location.origin;
  } catch {
    return false;
  }
}

/** Internal protocol metadata is parsed by the gateway and must never be shown or copied as lesson content. */
export function stripInternalChatMetadata(content: string): string {
  return content.replace(/\s*<!--\s*guided-result\s*:\s*(?:\{[\s\S]*?\}\s*-->|[\s\S]*$)/gi, "").trimEnd();
}

export function MarkdownContent({ children, streaming = false, headingIds, codeActions, allowDataImages = false }: { children: string; streaming?: boolean; headingIds?: string[]; codeActions?: MarkdownCodeActions; allowDataImages?: boolean }) {
  const dark = document.documentElement.classList.contains("dark");
  let headingIndex = 0;
  const nextHeadingId = () => headingIds?.[headingIndex++];
  return (
    <div className="markdown-content prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-20 prose-pre:p-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        urlTransform={(url) => url}
        components={{
          h1: ({ children: value, ...props }) => <h1 {...props} id={nextHeadingId()}>{value}</h1>,
          h2: ({ children: value }) => {
            const text = headingText(value);
            const educational = /练习|思考|核心|概念|误区|总结/.test(text);
            return <h2 id={nextHeadingId()} className={educational ? "education-heading" : undefined}>{value}</h2>;
          },
          h3: ({ children: value, ...props }) => <h3 {...props} id={nextHeadingId()}>{value}</h3>,
          h4: ({ children: value, ...props }) => <h4 {...props} id={nextHeadingId()}>{value}</h4>,
          code: ({ className, children: value, ...props }) => {
            const match = /language-([\w-]+)/.exec(className ?? "");
            const content = String(value).replace(/\n$/, "");
            if (!match) return <code className={className} {...props}>{value}</code>;
            return <LessonCodeBlock language={match[1]} code={content} dark={dark} actions={codeActions} />;
          },
          a: ({ children: value, href, ...props }) => isSameOriginMarkdownLink(href)
            ? <a {...props} href={href}>{value}</a>
            : <span className="external-link-removed">{value}</span>,
          img: ({ src, alt, ...props }) => isSafeMarkdownImage(src, allowDataImages)
            ? <img {...props} src={src} alt={alt ?? ""} />
            : <span className="external-link-removed">{alt || "图片资源不可用"}</span>,
        }}
      >
        {normalizeLatexDelimiters(stripInternalChatMetadata(children) || (streaming ? "" : "暂无内容"))}
      </ReactMarkdown>
      {streaming && <span className="stream-caret" aria-label="正在生成" />}
    </div>
  );
}
