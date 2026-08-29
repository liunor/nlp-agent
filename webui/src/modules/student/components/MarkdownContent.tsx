import { Check, Copy, ExternalLink, MessageCircleQuestion } from "lucide-react";
import { Children, Fragment, isValidElement, lazy, memo, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { renderToString } from "katex";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import "katex/dist/katex.min.css";

const LazyCode = lazy(async () => {
  const [{ default: SyntaxHighlighter }, { default: oneLight }] = await Promise.all([
    import("react-syntax-highlighter/dist/esm/prism-async-light"),
    import("react-syntax-highlighter/dist/esm/styles/prism/one-light"),
  ]);
  return {
    default({ language, code }: { language: string; code: string }) {
      return <SyntaxHighlighter language={language} style={oneLight} customStyle={{ margin: 0, padding: "20px 22px 22px", borderRadius: 0, fontSize: 14, lineHeight: 1.7, background: "#f3f4f6" }}>{code}</SyntaxHighlighter>;
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

function LessonCodeBlock({ code, language, actions, streaming = false }: { code: string; language: string; actions?: MarkdownCodeActions; streaming?: boolean }) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "error">("idle");
  const [codeReady, setCodeReady] = useState(() => typeof IntersectionObserver === "undefined");
  const codeRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (copyStatus === "idle") return undefined;
    const timer = window.setTimeout(() => setCopyStatus("idle"), 1800);
    return () => window.clearTimeout(timer);
  }, [copyStatus]);

  useEffect(() => {
    if (codeReady) return undefined;
    const target = codeRef.current;
    if (!target || typeof IntersectionObserver === "undefined") {
      setCodeReady(true);
      return undefined;
    }
    const root = target.closest<HTMLElement>(".knowledge-book-page-scroll,.teacher-book-preview");
    // happy-dom does not calculate layout boxes, so IntersectionObserver never
    // reports an intersection there. Render the code immediately in that
    // environment while keeping the viewport-gated path in a real browser.
    if (root && root.getBoundingClientRect().height === 0) {
      setCodeReady(true);
      return undefined;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setCodeReady(true);
        observer.disconnect();
      }
    }, { root, rootMargin: "640px 0px", threshold: 0 });
    observer.observe(target);
    return () => observer.disconnect();
  }, [codeReady]);

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
  return <div ref={codeRef} className="code-shell">
    <div className="code-toolbar">
      <div className="code-label">{language}</div>
      <div className="code-actions">
        <button className="code-copy-button" type="button" aria-label={`${copyStatus === "copied" ? "已复制" : "复制"} ${language} 代码`} onClick={() => void copy()}>{copyStatus === "copied" ? <Check size={14} /> : <Copy size={14} />}<span className="code-action-text">{copyStatus === "copied" ? "已复制" : "复制"}</span></button>
        {lessonActions && <>
        {lessonActions.onAskNova && <button type="button" aria-label="询问 Nova" onClick={() => lessonActions.onAskNova?.(code, language)}><MessageCircleQuestion size={13} />询问 Nova</button>}
        {lessonActions.onOpenInSandbox && <button type="button" aria-label="在沙箱中打开" onClick={() => lessonActions.onOpenInSandbox?.(code, language)}><ExternalLink size={13} />在沙箱中打开</button>}
        </>}
        <span className="sr-only" aria-live="polite">{copyStatus === "copied" ? `已复制 ${language} 代码` : copyStatus === "error" ? `复制 ${language} 代码失败` : ""}</span>
        {copyStatus === "error" && <span className="code-action-status" role="status">复制失败</span>}
      </div>
    </div>
    {codeReady && !streaming ? <Suspense fallback={<pre><code>{code}</code></pre>}><LazyCode language={language} code={code} /></Suspense> : <pre className="code-lazy-fallback"><code>{code}</code></pre>}
  </div>;
}

function useThrottledValue(value: string, active: boolean, intervalMs: number): string {
  const [visibleValue, setVisibleValue] = useState(value);
  const latestValue = useRef(value);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    latestValue.current = value;

    if (!active || intervalMs <= 0) {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
      return;
    }

    if (timer.current !== null) return;
    timer.current = window.setTimeout(() => {
      timer.current = null;
      setVisibleValue(latestValue.current);
    }, intervalMs);
  }, [active, intervalMs, value]);

  useEffect(() => () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  return !active || intervalMs <= 0 ? value : visibleValue;
}

type StreamingMarkdownBlock =
  | { kind: "text"; lines: string[]; signature: string }
  | { kind: "code"; language: string; code: string }
  | { kind: "table"; header: string[]; alignments: Array<"left" | "center" | "right" | undefined>; rows: string[][]; signature: string };

function splitTableRow(line: string): string[] {
  const source = line.trim();
  const start = source.startsWith("|") ? 1 : 0;
  const end = source.endsWith("|") && !source.endsWith("\\|") ? source.length - 1 : source.length;
  const cells: string[] = [];
  let cell = "";
  let codeDelimiter = "";
  let escaped = false;

  for (let index = start; index < end; index += 1) {
    const character = source[index];
    if (escaped) {
      cell += character;
      escaped = false;
      continue;
    }
    if (character === "\\") {
      cell += character;
      escaped = true;
      continue;
    }
    if (character === "`") {
      let run = "`";
      while (source[index + run.length] === "`") run += "`";
      cell += run;
      index += run.length - 1;
      codeDelimiter = codeDelimiter ? (codeDelimiter === run ? "" : codeDelimiter) : run;
      continue;
    }
    if (character === "|" && !codeDelimiter) {
      cells.push(cell.trim().replace(/\\\|/g, "|"));
      cell = "";
      continue;
    }
    cell += character;
  }
  cells.push(cell.trim().replace(/\\\|/g, "|"));
  return cells;
}

function parseStreamingTable(lines: string[], startIndex: number): Extract<StreamingMarkdownBlock, { kind: "table" }> & { nextIndex: number } | null {
  const header = splitTableRow(lines[startIndex]);
  if (header.length < 2 || !lines[startIndex + 1]) return null;
  const delimiter = splitTableRow(lines[startIndex + 1]);
  if (delimiter.length !== header.length || !delimiter.every((cell) => /^:?-{1,}:?$/.test(cell))) return null;

  const alignments = delimiter.map((cell) => {
    if (cell.startsWith(":") && cell.endsWith(":")) return "center" as const;
    if (cell.startsWith(":")) return "left" as const;
    if (cell.endsWith(":")) return "right" as const;
    return undefined;
  });
  const rows: string[][] = [];
  let nextIndex = startIndex + 2;
  while (nextIndex < lines.length && lines[nextIndex].trim()) {
    const row = splitTableRow(lines[nextIndex]);
    if (row.length !== header.length) break;
    rows.push(row);
    nextIndex += 1;
  }

  return {
    kind: "table",
    header,
    alignments,
    rows,
    signature: lines.slice(startIndex, nextIndex).join("\n"),
    nextIndex,
  };
}

function parseStreamingMarkdown(markdown: string): StreamingMarkdownBlock[] {
  const lines = stripInternalChatMetadata(markdown).split(/\r?\n/);
  const blocks: StreamingMarkdownBlock[] = [];
  let textLines: string[] = [];

  const flushText = () => {
    if (!textLines.length) return;
    blocks.push({ kind: "text", lines: textLines, signature: textLines.join("\n") });
    textLines = [];
  };

  for (let index = 0; index < lines.length; index += 1) {
    const table = index + 1 < lines.length ? parseStreamingTable(lines, index) : null;
    if (table) {
      flushText();
      blocks.push(table);
      index = table.nextIndex - 1;
      continue;
    }

    const opening = /^(?: {0,3})(`{3,}|~{3,})(.*)$/.exec(lines[index]);
    if (!opening) {
      textLines.push(lines[index]);
      continue;
    }

    flushText();
    const marker = opening[1];
    const markerCharacter = marker[0];
    const closingMarker = markerCharacter.repeat(marker.length);
    const codeLines: string[] = [];
    const language = opening[2].trim().split(/\s+/)[0] || "text";

    while (index + 1 < lines.length) {
      index += 1;
      const candidate = lines[index].trim();
      if (candidate.length >= closingMarker.length && [...candidate].every((character) => character === markerCharacter)) break;
      codeLines.push(lines[index]);
    }

    blocks.push({ kind: "code", language, code: codeLines.join("\n") });
  }

  flushText();
  return blocks;
}

function decodeHtmlEntities(text: string): string {
  if (!text.includes("&")) return text;
  const textarea = document.createElement("textarea");
  textarea.innerHTML = text.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return textarea.value;
}

function streamingMath(source: string, displayMode: boolean, key: string): ReactNode {
  try {
    return <span key={key} className={displayMode ? "streaming-math streaming-math-display" : "streaming-math"} dangerouslySetInnerHTML={{ __html: renderToString(normalizeFormulaContent(source), { displayMode, output: "htmlAndMathml", throwOnError: false }) }} />;
  } catch {
    return <Fragment key={key}>{decodeHtmlEntities(source)}</Fragment>;
  }
}

function renderStreamingInline(text: string, keyPrefix: string): ReactNode[] {
  const tokenPattern = /(`+[^`\r\n]*`+|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$|\$(?!\$)[^$\r\n]+\$|\*\*[^*\r\n]+\*\*|__[^_\r\n]+__|\*[^*\r\n]+\*|_[^_\r\n]+_)/g;
  return text.split(tokenPattern).map((part, index) => {
    const code = /^(`+)([^`\r\n]*)\1$/.exec(part);
    if (code) return <code key={`${keyPrefix}-code-${index}`}>{code[2]}</code>;

    const inlineLatex = /^\\\(([\s\S]*)\\\)$/.exec(part);
    if (inlineLatex) return streamingMath(inlineLatex[1], false, `${keyPrefix}-math-${index}`);
    const displayLatex = /^\\\[([\s\S]*)\\\]$/.exec(part);
    if (displayLatex) return streamingMath(displayLatex[1], true, `${keyPrefix}-math-${index}`);
    const displayDollars = /^\$\$([\s\S]*)\$\$$/.exec(part);
    if (displayDollars) return streamingMath(displayDollars[1], true, `${keyPrefix}-math-${index}`);
    const inlineDollars = /^\$([^$\r\n]+)\$$/.exec(part);
    if (inlineDollars) return streamingMath(inlineDollars[1], false, `${keyPrefix}-math-${index}`);

    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) return <strong key={`${keyPrefix}-strong-${index}`}>{decodeHtmlEntities(part.slice(2, -2))}</strong>;
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) return <em key={`${keyPrefix}-em-${index}`}>{decodeHtmlEntities(part.slice(1, -1))}</em>;
    return <Fragment key={`${keyPrefix}-text-${index}`}>{decodeHtmlEntities(part)}</Fragment>;
  });
}

function renderStreamingTextBlock(lines: string[], blockKey: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let paragraph: string[] = [];
  let quote: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let nodeIndex = 0;

  const nextKey = (kind: string) => `${blockKey}-${kind}-${nodeIndex++}`;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    nodes.push(<p className="streaming-paragraph" key={nextKey("paragraph")}>{renderStreamingInline(paragraph.join("\n"), nextKey("inline"))}</p>);
    paragraph = [];
  };
  const flushQuote = () => {
    if (!quote.length) return;
    nodes.push(<blockquote className="streaming-blockquote" key={nextKey("quote")}><p>{renderStreamingInline(quote.join("\n"), nextKey("quote-inline"))}</p></blockquote>);
    quote = [];
  };
  const flushList = () => {
    if (!list) return;
    const List = list.ordered ? "ol" : "ul";
    nodes.push(<List key={nextKey(list.ordered ? "ordered-list" : "unordered-list")}>{list.items.map((item, index) => <li key={`${blockKey}-item-${index}`}>{renderStreamingInline(item, `${blockKey}-item-${index}`)}</li>)}</List>);
    list = null;
  };
  const flushAll = () => {
    flushParagraph();
    flushQuote();
    flushList();
  };

  lines.forEach((line) => {
    if (!line.trim()) {
      flushAll();
      return;
    }

    const heading = /^(#{1,4})\s+(.+?)\s*$/.exec(line);
    if (heading) {
      flushAll();
      const content = renderStreamingInline(heading[2], nextKey("heading-inline"));
      if (heading[1].length === 1) nodes.push(<h1 key={nextKey("h1")}>{content}</h1>);
      else if (heading[1].length === 2) nodes.push(<h2 key={nextKey("h2")}>{content}</h2>);
      else if (heading[1].length === 3) nodes.push(<h3 key={nextKey("h3")}>{content}</h3>);
      else nodes.push(<h4 key={nextKey("h4")}>{content}</h4>);
      return;
    }

    const quoteLine = /^ {0,3}>\s?(.*)$/.exec(line);
    if (quoteLine) {
      flushParagraph();
      flushList();
      quote.push(quoteLine[1]);
      return;
    }
    flushQuote();

    const listLine = /^\s*((?:[-*+]\s+)|(?:\d+\.\s+))(.*)$/.exec(line);
    if (listLine) {
      flushParagraph();
      const ordered = /^\d/.test(listLine[1]);
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push(listLine[2]);
      return;
    }
    flushList();

    if (/^ {0,3}(?:[-*_]\s*){3,}$/.test(line)) {
      flushParagraph();
      nodes.push(<hr key={nextKey("rule")} />);
      return;
    }
    paragraph.push(line);
  });

  flushAll();
  return nodes;
}

function StreamingMarkdownPreview({ markdown, codeActions }: { markdown: string; codeActions?: MarkdownCodeActions }) {
  const blocks = useMemo(() => parseStreamingMarkdown(markdown), [markdown]);
  return <div className="markdown-streaming-preview">
    {blocks.map((block, index) => block.kind === "code"
      ? <StreamingCodeBlock key={`stream-code-${index}`} code={block.code} language={block.language} actions={codeActions} />
      : block.kind === "table"
        ? <StreamingTableBlock key={`stream-table-${index}`} {...block} />
        : <StreamingTextBlock key={`stream-text-${index}`} lines={block.lines} signature={block.signature} blockKey={`stream-${index}`} />)}
  </div>;
}

function StreamingTableBlock({ header, alignments, rows }: { header: string[]; alignments: Array<"left" | "center" | "right" | undefined>; rows: string[][] }) {
  const cellStyle = (index: number) => alignments[index] ? { textAlign: alignments[index] } : undefined;
  return <div className="streaming-table-wrap">
    <table>
      <thead><tr>{header.map((cell, index) => <th key={`header-${index}`} style={cellStyle(index)}>{renderStreamingInline(cell, `table-header-${index}`)}</th>)}</tr></thead>
      <tbody>{rows.map((row, rowIndex) => <tr key={`row-${rowIndex}`}>{row.map((cell, cellIndex) => <td key={`cell-${rowIndex}-${cellIndex}`} style={cellStyle(cellIndex)}>{renderStreamingInline(cell, `table-cell-${rowIndex}-${cellIndex}`)}</td>)}</tr>)}</tbody>
    </table>
  </div>;
}

const StreamingTextBlock = memo(function StreamingTextBlock({ lines, blockKey }: { lines: string[]; signature: string; blockKey: string }) {
  return <>{renderStreamingTextBlock(lines, blockKey)}</>;
}, (previous, next) => previous.blockKey === next.blockKey && previous.signature === next.signature);

const StreamingCodeBlock = memo(function StreamingCodeBlock({ code, language, actions }: { code: string; language: string; actions?: MarkdownCodeActions }) {
  return <LessonCodeBlock code={code} language={language} actions={actions} streaming />;
}, (previous, next) => previous.code === next.code && previous.language === next.language && previous.actions === next.actions);

function headingText(children: ReactNode): string {
  return Children.toArray(children).join("");
}

function headingSourceLine(node: unknown): number | undefined {
  if (!node || typeof node !== "object") return undefined;
  const position = (node as { position?: { start?: { line?: unknown } } }).position;
  const line = position?.start?.line;
  return typeof line === "number" ? line : undefined;
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

function readMarkdownImageWidth(title: string | undefined): string | undefined {
  const match = title?.trim().match(/^width\s*=\s*(\d+(?:\.\d+)?)(px|%|rem|em|vw|vh)?$/i);
  if (!match) return undefined;
  const value = Number(match[1]);
  const unit = (match[2] ?? "px").toLowerCase();
  const maximum = unit === "%" || unit === "vw" || unit === "vh" ? 100 : 1600;
  if (!Number.isFinite(value) || value <= 0 || value > maximum) return undefined;
  return `${value}${unit}`;
}

/** Internal protocol metadata is parsed by the gateway and must never be shown or copied as lesson content. */
export function stripInternalChatMetadata(content: string): string {
  return content.replace(/\s*<!--\s*guided-result\s*:\s*(?:\{[\s\S]*?\}\s*-->|[\s\S]*$)/gi, "").trimEnd();
}

export function MarkdownContent({ children, streaming = false, streamRenderIntervalMs = 30, headingIds, headingIdsByLine, codeActions, allowDataImages = false }: { children: string; streaming?: boolean; streamRenderIntervalMs?: number; headingIds?: string[]; headingIdsByLine?: Record<number, string>; codeActions?: MarkdownCodeActions; allowDataImages?: boolean }) {
  const renderedChildren = useThrottledValue(children, streaming, Math.max(0, streamRenderIntervalMs));
  const renderedMarkdown = useMemo(() => {
    if (streaming) return null;
    let legacyHeadingIndex = 0;
    const nextHeadingId = (node: unknown) => {
      if (headingIdsByLine) {
        const sourceLine = headingSourceLine(node);
        return sourceLine === undefined ? undefined : headingIdsByLine[sourceLine];
      }
      return headingIds?.[legacyHeadingIndex++];
    };
    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        urlTransform={(url) => url}
        components={{
          h1: ({ children: value, node, ...props }) => {
            const headingId = nextHeadingId(node);
            return <><span id={headingId} className="knowledge-book-heading-anchor" data-knowledge-book-heading-anchor="true" aria-hidden="true" /><h1 {...props} data-knowledge-book-heading-id={headingId}>{value}</h1></>;
          },
          h2: ({ children: value, node }) => {
            const headingId = nextHeadingId(node);
            const text = headingText(value);
            const educational = /练习|思考|核心|概念|误区|总结/.test(text);
            return <><span id={headingId} className="knowledge-book-heading-anchor" data-knowledge-book-heading-anchor="true" aria-hidden="true" /><h2 data-knowledge-book-heading-id={headingId} className={educational ? "education-heading" : undefined}>{value}</h2></>;
          },
          h3: ({ children: value, node, ...props }) => {
            const headingId = nextHeadingId(node);
            return <><span id={headingId} className="knowledge-book-heading-anchor" data-knowledge-book-heading-anchor="true" aria-hidden="true" /><h3 {...props} data-knowledge-book-heading-id={headingId}>{value}</h3></>;
          },
          h4: ({ children: value, node, ...props }) => {
            const headingId = nextHeadingId(node);
            return <><span id={headingId} className="knowledge-book-heading-anchor" data-knowledge-book-heading-anchor="true" aria-hidden="true" /><h4 {...props} data-knowledge-book-heading-id={headingId}>{value}</h4></>;
          },
          pre: ({ children: value }) => {
            const child = Children.toArray(value)[0];
            if (isValidElement<{ className?: string; children?: ReactNode }>(child)) {
              const match = /language-([\w-]+)/.exec(child.props.className ?? "");
              const content = Children.toArray(child.props.children).join("").replace(/\n$/, "");
              return <LessonCodeBlock language={match?.[1] ?? "text"} code={content} actions={codeActions} streaming={streaming} />;
            }
            return <pre>{value}</pre>;
          },
          code: ({ className, children: value, ...props }) => {
            const match = /language-([\w-]+)/.exec(className ?? "");
            const content = String(value).replace(/\n$/, "");
            if (!match) return <code className={className} {...props}>{value}</code>;
            return <LessonCodeBlock language={match[1]} code={content} actions={codeActions} streaming={streaming} />;
          },
          a: ({ children: value, href, ...props }) => isSameOriginMarkdownLink(href)
            ? <a {...props} href={href}>{value}</a>
            : <span className="external-link-removed">{value}</span>,
          img: ({ node, src, alt, title, ...props }) => {
            void node;
            const imageWidth = readMarkdownImageWidth(title);
            return isSafeMarkdownImage(src, allowDataImages)
              ? <span className="markdown-image-figure"><img {...props} src={src} alt={alt ?? ""} title={imageWidth ? undefined : title} loading="lazy" decoding="async" style={imageWidth ? { ...props.style, width: imageWidth } : props.style} />{alt?.trim() && <span className="markdown-image-caption">{alt}</span>}</span>
              : <span className="external-link-removed">{alt || "图片资源不可用"}</span>;
          },
        }}
      >
        {normalizeLatexDelimiters(stripInternalChatMetadata(renderedChildren) || (streaming ? "" : "暂无内容"))}
      </ReactMarkdown>
    );
  }, [allowDataImages, codeActions, headingIds, headingIdsByLine, renderedChildren, streaming]);

  return (
    <div className="markdown-content prose prose-zinc max-w-none dark:prose-invert prose-headings:scroll-mt-20 prose-pre:p-0">
      {streaming ? <StreamingMarkdownPreview markdown={renderedChildren} codeActions={codeActions} /> : renderedMarkdown}
      {streaming && <span className="stream-caret" aria-label="正在生成" />}
    </div>
  );
}
