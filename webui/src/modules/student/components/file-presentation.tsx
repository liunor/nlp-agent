import { FileArchive, FileCode2, FileImage, FileSpreadsheet, FileText } from "lucide-react";

export function filePresentation(name: string, mediaType = "") {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  if (mediaType.startsWith("image/") || /^(png|jpe?g|webp|gif|bmp|svg)$/.test(extension)) return { kind: "image", label: extension.toUpperCase() || "图片", Icon: FileImage };
  if (extension === "pdf") return { kind: "pdf", label: "PDF", Icon: FileText };
  if (/^(docx?|odt)$/.test(extension)) return { kind: "word", label: "WORD", Icon: FileText };
  if (/^(xlsx?|csv|tsv)$/.test(extension)) return { kind: "sheet", label: extension.toUpperCase(), Icon: FileSpreadsheet };
  if (/^(zip|rar|7z|tar|gz)$/.test(extension)) return { kind: "archive", label: extension.toUpperCase(), Icon: FileArchive };
  if (/^(py|[cm]?js|jsx|tsx?|json|html?|css|sql|yaml|yml|sh|java|cpp|rs|go)$/.test(extension)) return { kind: "code", label: extension.toUpperCase(), Icon: FileCode2 };
  return { kind: "text", label: extension === name.toLowerCase() ? "文件" : extension.toUpperCase(), Icon: FileText };
}

export function fileSizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}
