import { createUuid } from "@/shared/utils/uuid";
import type { ImportedFile } from "./importedFiles";
// 800k UTF-16 characters is a generous but bounded preview. The byte slice
// below must stay a little larger than the worst case (4 bytes per character)
// so a CJK-only 800k-character document does not get cut short accidentally.
export const MAX_PREVIEW_CHARS = 800_000;
export const MAX_PREVIEW_BYTES = 4 * 1024 * 1024;

const CODE_EXTENSIONS: Record<string, string> = {
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "jsx", ts: "typescript", tsx: "tsx",
  py: "python", json: "json", css: "css", scss: "scss", html: "markup", htm: "markup", xml: "markup",
  yaml: "yaml", yml: "yaml", sh: "bash", bash: "bash", zsh: "bash", sql: "sql", java: "java",
  c: "c", h: "c", cpp: "cpp", hpp: "cpp", cs: "csharp", go: "go", rs: "rust", rb: "ruby",
  php: "php", swift: "swift", kt: "kotlin", vue: "markup", svelte: "markup", toml: "toml",
  ini: "ini", env: "bash",
};
// Extensionless files like Dockerfile / Makefile are matched by basename instead.
const CODE_FILENAMES: Record<string, string> = {
  dockerfile: "docker",
  makefile: "makefile",
};
const MARKDOWN_EXTENSIONS = new Set(["md", "markdown", "mdown", "mkd"]);
const TEXT_EXTENSIONS = new Set(["txt", "text", "log", "csv", "tsv"]);
const SUPPORTED_EXTENSIONS = new Set([
  ...MARKDOWN_EXTENSIONS,
  ...TEXT_EXTENSIONS,
  ...Object.keys(CODE_EXTENSIONS),
  "dockerfile",
  "makefile",
]);
export const ACCEPT_ATTRIBUTE = Array.from(SUPPORTED_EXTENSIONS, (extension) => "." + extension).join(",") + ",.pdf,.docx";

export interface FilesPanelPreviewRequest {
  id: string;
  name: string;
  url: string;
  mediaType: string;
  bytes: number;
  source?: "learning-book" | "storage";
  workspaceId?: string;
}

function extensionOf(name: string) {
  const normalized = name.toLowerCase();
  const index = normalized.lastIndexOf(".");
  return index >= 0 ? normalized.slice(index + 1) : "";
}

function baseNameOf(name: string) {
  return name.toLowerCase().replace(/\.[^.]*$/, "");
}

export function isSupportedFile(file: File) {
  const extension = extensionOf(file.name);
  if (extension) return SUPPORTED_EXTENSIONS.has(extension);
  if (CODE_FILENAMES[baseNameOf(file.name)]) return true;
  return file.type.startsWith("text/");
}

function describeFile(file: File): ImportedFile {
  const extension = extensionOf(file.name);
  const baseName = baseNameOf(file.name);
  const isMarkdown = MARKDOWN_EXTENSIONS.has(extension) || /readme(?:\.[\w-]+)?$/i.test(file.name);
  const codeLanguage = CODE_EXTENSIONS[extension] ?? CODE_FILENAMES[baseName] ?? "";
  const language = isMarkdown ? "markdown" : codeLanguage ? "code" : "text";
  return {
    id: createUuid(),
    name: file.name,
    content: "",
    language,
    codeLanguage,
    bytes: file.size,
    truncated: false,
    importedAt: Date.now(),
  };
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

export function previewFile(file: File, text: string): ImportedFile {
  const meta = describeFile(file);
  return {
    ...meta,
    content: text.slice(0, MAX_PREVIEW_CHARS),
    truncated: file.size > MAX_PREVIEW_BYTES || text.length > MAX_PREVIEW_CHARS,
  };
}
