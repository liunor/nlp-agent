import { useEffect, useState } from "react";
import { api, type FileContent } from "@/platform/http/api";

export function FileSpaceReader({ fileId, name, workspaceId }: { fileId: string; name: string; workspaceId?: string }) {
  const [result, setResult] = useState<FileContent | null>(null);
  const [offset, setOffset] = useState(0);
  const [charOffset, setCharOffset] = useState(0);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const extension = name.toLowerCase().split(".").pop();
  const binary = ["pdf", "png", "jpg", "jpeg", "webp"].includes(extension ?? "");
  const [showText, setShowText] = useState(!binary);
  const query = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : "";
  const base = `/api/v1/storage/files/${encodeURIComponent(fileId)}`;
  const hash = result?.sha256;
  const loading = showText && !error && (!result || result.offset !== offset || (result.char_offset ?? 0) !== charOffset);

  useEffect(() => {
    if (!showText) return;
    let active = true;
    void api.readFileContent(fileId, workspaceId, offset, hash, charOffset).then(value => {
      if (active) setResult(value);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "读取失败");
    });
    return () => { active = false; };
    // The hash pins subsequent requests; receiving it must not repeat this request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, workspaceId, offset, charOffset, showText, retry]);

  return <section className="files-panel-preview" aria-label={`阅读 ${name}`}>
    <header className="files-panel-preview-header"><strong>{name}</strong><a href={`${base}/download${query}`} download>下载</a></header>
    {binary && !showText && (extension === "pdf"
      ? <iframe title={`预览 ${name}`} src={`${base}/view${query}`} style={{ width: "100%", height: "65vh", border: 0 }} />
      : <img src={`${base}/view${query}`} alt={name} style={{ maxWidth: "100%" }} />)}
    {extension === "pdf" && <button type="button" onClick={() => setShowText(value => !value)}>{showText ? "查看原始页面" : "读取页面文字"}</button>}
    {loading && <p role="status">正在读取……</p>}
    {error && <p role="alert">{error}<button type="button" onClick={() => { setError(""); setResult(null); setRetry(value => value + 1); }}>读取当前版本</button></p>}
    {showText && !loading && !error && result && <div className="files-panel-preview-body">
      {result.warnings.map(warning => <p key={warning}>{warning}</p>)}
      {result.blocks.map(block => <div key={block.number} id={`file-${fileId}-${block.number}`}>
        <small>{result.unit === "page" ? "页" : result.unit === "line" ? "行" : "段/表"} {block.number}</small>
        <pre className="files-panel-plain">{block.needs_ocr ? "此页没有可提取文字，需要 OCR。" : block.text}</pre>
        {block.truncated && <p>本单元超出单次读取预算，仅显示前段。</p>}
      </div>)}
      {offset > 0 && <button type="button" onClick={() => { setOffset(0); setCharOffset(0); }}>返回开头</button>}
      {result.next_offset !== null && <button type="button" onClick={() => { setOffset(result.next_offset!); setCharOffset(result.next_char_offset ?? 0); }}>继续读取</button>}
    </div>}
  </section>;
}
